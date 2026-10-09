"""Small stdlib-only HLS parser (master + VOD media playlists) and quality selection.

Encrypted / DRM playlists are *detected* (``protection``) so callers can refuse them.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import urljoin

from .errors import StreamDetectionError

_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def parse_attrs(raw: str) -> dict:
    out = {}
    for k, v in _ATTR.findall(raw):
        out[k] = v[1:-1] if len(v) > 1 and v[0] == '"' and v[-1] == '"' else v
    return out


def _lines(text: str) -> List[str]:
    lines = [l.strip() for l in (text or "").lstrip("\ufeff").replace("\r", "\n").split("\n")]
    lines = [l for l in lines if l]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise StreamDetectionError("not an HLS playlist (missing #EXTM3U)")
    return lines


def looks_like_master(text: str) -> bool:
    return bool(text) and text.lstrip("\ufeff").lstrip().startswith("#EXTM3U") and "#EXT-X-STREAM-INF" in text


def looks_like_media(text: str) -> bool:
    return bool(text) and text.lstrip("\ufeff").lstrip().startswith("#EXTM3U") and "#EXTINF" in text


def protection_reasons(lines: List[str]) -> Tuple[str, ...]:
    reasons = []
    for l in lines:
        if l.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")):
            a = parse_attrs(l.split(":", 1)[1])
            m = a.get("METHOD", "").upper()
            if m and m != "NONE":
                reasons.append(f"encryption {m}")
            if a.get("KEYFORMAT", "identity").lower() != "identity":
                reasons.append(f"key format {a['KEYFORMAT']}")
    return tuple(dict.fromkeys(reasons))


@dataclass(frozen=True)
class Variant:
    url: str
    bandwidth: int
    width: Optional[int]
    height: Optional[int]
    codecs: str
    audio_group: Optional[str]

    @property
    def label(self) -> str:
        return f"{self.height}p" if self.height else "unknown"


@dataclass(frozen=True)
class AudioTrack:
    group_id: str
    name: str
    url: Optional[str]
    language: Optional[str]
    default: bool


@dataclass
class Master:
    url: str
    variants: List[Variant]
    audio: List[AudioTrack] = field(default_factory=list)
    protection: Tuple[str, ...] = ()


@dataclass
class Media:
    url: str
    segment_count: int
    duration: float
    is_vod: bool
    has_init: bool
    protection: Tuple[str, ...] = ()


def parse_master(text: str, base_url: str) -> Master:
    lines = _lines(text)
    variants, audio = [], []
    for i, l in enumerate(lines):
        if l.startswith("#EXT-X-MEDIA:"):
            a = parse_attrs(l.split(":", 1)[1])
            if a.get("TYPE", "").upper() == "AUDIO":
                uri = a.get("URI")
                audio.append(AudioTrack(a.get("GROUP-ID", ""), a.get("NAME", ""), urljoin(base_url, uri) if uri else None,
                                        a.get("LANGUAGE"), a.get("DEFAULT", "NO").upper() == "YES"))
        elif l.startswith("#EXT-X-STREAM-INF:"):
            a = parse_attrs(l.split(":", 1)[1])
            j = i + 1
            while j < len(lines) and lines[j].startswith("#"):
                j += 1
            if j >= len(lines):
                raise StreamDetectionError("STREAM-INF without URI")
            m = re.match(r"^(\d+)x(\d+)$", a.get("RESOLUTION", ""))
            bw = a.get("BANDWIDTH") or a.get("AVERAGE-BANDWIDTH") or "0"
            variants.append(Variant(urljoin(base_url, lines[j]), int(float(bw)) if bw.replace(".", "").isdigit() else 0,
                                    int(m.group(1)) if m else None, int(m.group(2)) if m else None,
                                    a.get("CODECS", ""), a.get("AUDIO")))
    if not variants:
        raise StreamDetectionError("master playlist has no video variants")
    return Master(base_url, variants, audio, protection_reasons(lines))


def parse_media(text: str, base_url: str) -> Media:
    lines = _lines(text)
    dur, count, ended, vod, init = 0.0, 0, False, False, False
    for l in lines:
        if l.startswith("#EXTINF:"):
            try:
                dur += float(l.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                raise StreamDetectionError(f"bad EXTINF line: {l[:40]!r}")
            count += 1
        elif l.startswith("#EXT-X-ENDLIST"):
            ended = True
        elif l.startswith("#EXT-X-PLAYLIST-TYPE:") and "VOD" in l.upper():
            vod = True
        elif l.startswith("#EXT-X-MAP:"):
            init = True
    if count == 0:
        raise StreamDetectionError("media playlist has no segments")
    return Media(base_url, count, dur, ended or vod, init, protection_reasons(lines))


def choose_variant(master: Master, quality: str = "best") -> Tuple[Variant, str]:
    """Return (variant, note). quality: 'best' or a height like '1080' / '1080p'."""
    # one variant per height, AVC preferred, then highest bandwidth
    by_h = {}
    for v in master.variants:
        by_h.setdefault(v.height or 0, []).append(v)
    best = sorted((sorted(g, key=lambda v: (0 if "avc1" in v.codecs.lower() else 1, -v.bandwidth))[0] for g in by_h.values()),
                  key=lambda v: v.height or 0, reverse=True)
    q = (quality or "best").strip().lower().rstrip("p")
    if q in ("best", ""):
        return best[0], ""
    if not q.isdigit():
        raise StreamDetectionError(f"unknown quality {quality!r}; use best, 1440, 1080, 720, 480")
    want = int(q)
    for v in best:
        if v.height == want:
            return v, ""
    lower = [v for v in best if (v.height or 0) < want]
    if lower:
        return lower[0], f"{want}p not offered; using {lower[0].label}"
    return best[-1], f"{want}p not offered; smallest available is {best[-1].label}"


def choose_audio(master: Master, variant: Variant) -> Optional[AudioTrack]:
    tracks = [a for a in master.audio if a.url]
    if not tracks:
        return None
    grouped = [a for a in tracks if variant.audio_group and a.group_id == variant.audio_group]
    tracks = grouped or tracks
    return next((a for a in tracks if a.default), tracks[0])
