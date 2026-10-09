"""Turn captured playlist responses into an unambiguous video/audio plan.

Never "first .m3u8 wins": the master playlist (if the player requested it) decides which media
playlist is video and which is audio. If no master was seen, each media playlist is identified by
asking ffprobe what streams it contains.
"""
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

import requests

from . import hls_parser as hls
from .errors import ProtectedStreamError, StreamDetectionError
from .models import Capture, StreamPlan, safe_headers
from .redact import redact_url


def _path_key(url: str) -> str:
    p = urlsplit(url)
    return f"{p.hostname}{p.path}"


def find_capture(captures: List[Capture], url: str) -> Optional[Capture]:
    exact = [c for c in captures if c.url == url]
    if exact:
        return exact[-1]
    same = [c for c in captures if _path_key(c.url) == _path_key(url)]
    return same[-1] if same else None


def default_fetch(headers: Dict[str, str]) -> Callable[[str], str]:
    def fetch(url: str) -> str:
        if urlsplit(url).scheme != "https":
            raise StreamDetectionError("refusing to fetch a non-https playlist")
        r = requests.get(url, headers=safe_headers(headers), timeout=30)
        if r.status_code in (401, 403, 404, 410):
            raise StreamDetectionError(f"playlist request refused (HTTP {r.status_code}); the link may have expired")
        r.raise_for_status()
        return r.text
    return fetch


def _media_for(url: str, captures: List[Capture], fetch: Callable[[str], str]) -> hls.Media:
    cap = find_capture(captures, url)
    text = cap.text if cap else fetch(url)
    media = hls.parse_media(text, url)
    if media.protection:
        raise ProtectedStreamError("stream is protected (" + ", ".join(media.protection) + "); this tool does not handle protected streams")
    if not media.is_vod:
        raise StreamDetectionError("this is a live stream, not a finished recording")
    return media


def list_masters(captures: List[Capture]) -> List[Capture]:
    return [c for c in captures if hls.looks_like_master(c.text)]


def build_plan(captures: List[Capture], quality: str = "best", *, headers: Optional[Dict[str, str]] = None,
               fetch: Optional[Callable[[str], str]] = None, probe: Optional[Callable[[str], dict]] = None,
               master_capture: Optional[Capture] = None) -> StreamPlan:
    headers = headers or {}
    fetch = fetch or default_fetch(headers)
    masters = list_masters(captures)
    if master_capture is None and masters:
        master_capture = masters[-1]  # newest request = the recording that was just opened

    if master_capture is not None:
        master = hls.parse_master(master_capture.text, master_capture.url)
        if master.protection:
            raise ProtectedStreamError("playlist declares protection (" + ", ".join(master.protection) + "); this tool does not handle it")
        variant, note = hls.choose_variant(master, quality)
        audio = hls.choose_audio(master, variant)
        vmedia = _media_for(variant.url, captures, fetch)
        notes = [note] if note else []
        if audio is None:
            notes.append("no separate audio playlist: audio is expected inside the video stream")
        else:
            amedia = _media_for(audio.url, captures, fetch)
            if abs(amedia.duration - vmedia.duration) > max(3.0, 0.02 * vmedia.duration):
                notes.append(f"audio ({amedia.duration:.0f}s) and video ({vmedia.duration:.0f}s) lengths differ")
        return StreamPlan(variant.url, audio.url if audio else None, variant.label, variant.width, variant.height,
                          vmedia.duration, "master", variant.codecs, notes)

    # No master seen: identify media playlists by content.
    medias = [c for c in captures if hls.looks_like_media(c.text)]
    if not medias:
        raise StreamDetectionError("no HLS playlists were captured")
    if probe is None:
        raise StreamDetectionError("only media playlists were captured and ffprobe is unavailable to classify them")
    unique: Dict[str, Capture] = {}
    for c in medias:
        unique[_path_key(c.url)] = c
    video = audio = None
    vh = -1
    for c in unique.values():
        info = probe(c.url)
        kinds = {s.get("codec_type") for s in info.get("streams", [])}
        if "video" in kinds:
            h = max((s.get("height") or 0) for s in info["streams"] if s.get("codec_type") == "video")
            if h > vh:
                video, vh, vinfo = c, h, info
        elif "audio" in kinds:
            audio = c
    if video is None:
        raise StreamDetectionError("none of the captured playlists contains video")
    vmedia = _media_for(video.url, captures, fetch)
    vs = next(s for s in vinfo["streams"] if s.get("codec_type") == "video")
    notes = ["master playlist was not observed; video/audio identified with ffprobe"]
    if audio is not None:
        _media_for(audio.url, captures, fetch)
    return StreamPlan(video.url, audio.url if audio else None, f"{vs.get('height')}p", vs.get("width"), vs.get("height"),
                      vmedia.duration, "ffprobe", vs.get("codec_name", ""), notes)


def describe_plan(plan: StreamPlan) -> List[str]:
    rows = [f"quality   : {plan.label}" + (f" ({plan.width}x{plan.height})" if plan.width else ""),
            f"duration  : {plan.duration:.0f}s",
            f"video     : {redact_url(plan.video_url)}",
            f"audio     : {redact_url(plan.audio_url) if plan.audio_url else 'muxed in video stream'}",
            f"identified: via {plan.source}"]
    rows += [f"note      : {n}" for n in plan.notes]
    return rows
