from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Only headers a normal player legitimately sends to the CDN. Cookies/Authorization are never kept.
FORWARD_HEADERS = ("referer", "origin", "user-agent", "accept-language")


def safe_headers(raw: dict) -> Dict[str, str]:
    low = {str(k).lower(): v for k, v in (raw or {}).items()}
    return {k: low[k] for k in FORWARD_HEADERS if k in low}


@dataclass
class Capture:
    """One HLS playlist response the authorized browser session received."""
    seq: int
    url: str          # contains temporary signed credentials: keep in memory only
    text: str
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class StreamPlan:
    video_url: str
    audio_url: Optional[str]          # None => audio is muxed into the video stream
    label: str                        # e.g. "1080p"
    width: Optional[int]
    height: Optional[int]
    duration: float                   # seconds, from the video media playlist
    source: str                       # "master" | "ffprobe"
    video_codecs: str = ""
    notes: List[str] = field(default_factory=list)
