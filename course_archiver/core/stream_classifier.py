"""Turn captured playlist responses into an unambiguous video/audio plan.

Never "first .m3u8 wins": the master playlist (if the player requested it) decides which media
playlist is video and which is audio. If no master was seen, each media playlist is identified by
asking ffprobe what streams it contains.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import threading
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

import requests

_HTTP_LOCAL = threading.local()


def _http_session():
    """Reuse keep-alive connections within each worker without sharing Sessions across threads."""
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        _HTTP_LOCAL.session = session
    return session


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
        # BANDWIDTH is the peak rate, which can greatly exaggerate the size of a long VOD.
        # Prefer AVERAGE-BANDWIDTH when the playlist provides it. Otherwise, use a conservative
        # 50% of peak is only a rough fallback; this is less misleading than treating peak
        # segment bandwidth as the sustained bitrate for a multi-hour recording.
        estimate_rate = variant.average_bandwidth
        if not estimate_rate and variant.bandwidth > 0:
            estimate_rate = int(variant.bandwidth * 0.50)
        estimate = int(vmedia.duration * estimate_rate / 8 * 1.03) if estimate_rate else None
        return StreamPlan(variant.url, audio.url if audio else None, variant.label, variant.width, variant.height,
                          vmedia.duration, "master", variant.codecs, notes, estimate)

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



def _resource_size(url: str, headers: Dict[str, str]) -> Optional[int]:
    """Read an object size from HTTP metadata without downloading the segment body."""
    clean = safe_headers(headers)
    clean["Accept-Encoding"] = "identity"
    try:
        response = _http_session().head(url, headers=clean, allow_redirects=True, timeout=(3, 5))
        try:
            if 200 <= response.status_code < 300:
                raw = response.headers.get("Content-Length")
                if raw and raw.strip().isdigit():
                    return int(raw.strip())
        finally:
            response.close()
    except requests.RequestException:
        pass

    # Some CDNs reject HEAD. A one-byte range request can expose the full object's
    # size in Content-Range while avoiding a full segment download.
    range_headers = dict(clean)
    range_headers["Range"] = "bytes=0-0"
    try:
        response = _http_session().get(
            url, headers=range_headers, allow_redirects=True,
            stream=True, timeout=(3, 5),
        )
        try:
            if response.status_code == 206:
                match = re.search(r"/(\d+)\s*$", response.headers.get("Content-Range", ""))
                if match:
                    return int(match.group(1))
            if response.status_code == 200:
                raw = response.headers.get("Content-Length")
                if raw and raw.strip().isdigit():
                    return int(raw.strip())
        finally:
            response.close()
    except requests.RequestException:
        pass
    return None


def fetch_source_size_bytes(plan: StreamPlan, captures: List[Capture],
                            headers: Optional[Dict[str, str]] = None,
                            on_status: Optional[Callable[[str], None]] = None,
                            cancel_event=None) -> Optional[int]:
    """Sum source HLS segment sizes from HTTP metadata without downloading segment bodies.

    Requests are issued concurrently in one bounded pool and each worker reuses its HTTP
    connections. If any resource with unknown size cannot be measured, return None so the
    caller can keep the quick bitrate estimate instead of waiting through repeated batches.
    """
    headers = headers or {}
    fetch = default_fetch(headers)
    resources = []

    try:
        playlist_urls = [plan.video_url]
        if plan.audio_url:
            playlist_urls.append(plan.audio_url)

        for playlist_url in dict.fromkeys(playlist_urls):
            if cancel_event is not None and cancel_event.is_set():
                return None
            capture = find_capture(captures, playlist_url)
            text = capture.text if capture else fetch(playlist_url)
            media = hls.parse_media(text, playlist_url)
            if media.protection:
                return None
            init_resources, segments = hls.media_resources(text, playlist_url)
            resources.extend(init_resources)
            resources.extend(segments)

        if not resources:
            return None

        # Deduplicate URLs: the same init segment can be referenced many times.
        urls_to_query = sorted({url for url, known_length in resources if known_length is None})
        sizes = {}
        if on_status:
            on_status(f"Fetching source size metadata · {len(urls_to_query)} resources (parallel)…")

        if urls_to_query:
            # HLS recordings can contain hundreds or thousands of tiny segments. Sequential
            # batches multiply CDN latency; bounded concurrency avoids that wait while keeping
            # request pressure finite. Use a single pool for the whole set.
            worker_count = min(48, len(urls_to_query))
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="size-meta") as pool:
                pending = {pool.submit(_resource_size, url, headers): url for url in urls_to_query}
                completed = 0
                failed = False
                for future in as_completed(pending):
                    if cancel_event is not None and cancel_event.is_set():
                        for task in pending:
                            task.cancel()
                        return None
                    url = pending[future]
                    try:
                        sizes[url] = future.result()
                    except Exception:
                        sizes[url] = None
                    completed += 1
                    if sizes[url] is None:
                        failed = True
                        for task in pending:
                            if not task.done():
                                task.cancel()
                        break
                    if on_status and (completed % 50 == 0 or completed == len(urls_to_query)):
                        on_status(f"Fetching source size metadata · {completed}/{len(urls_to_query)} checked…")
                if failed:
                    return None

        total = 0
        for url, known_length in resources:
            length = known_length if known_length is not None else sizes.get(url)
            if length is None:
                return None
            total += length

        return total if total > 0 else None
    except (requests.RequestException, ValueError, StreamDetectionError, OSError):
        return None

def describe_plan(plan: StreamPlan) -> List[str]:
    rows = [f"quality   : {plan.label}" + (f" ({plan.width}x{plan.height})" if plan.width else ""),
            f"duration  : {plan.duration:.0f}s",
            f"video     : {redact_url(plan.video_url)}",
            f"audio     : {redact_url(plan.audio_url) if plan.audio_url else 'muxed in video stream'}",
            f"identified: via {plan.source}"]
    rows += [f"note      : {n}" for n in plan.notes]
    return rows
