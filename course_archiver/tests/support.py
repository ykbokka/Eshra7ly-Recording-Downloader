"""Test helpers: tiny real HLS fixtures made with ffmpeg and a local mock CDN with fault injection.

Nothing here talks to the internet or uses any account data.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_hls(dest: Path, *, single_file: bool = False, seconds: int = 8) -> Path:
    """Create master.m3u8 + two video renditions (640x360, 320x180) + a separate audio group."""
    dest.mkdir(parents=True, exist_ok=True)
    flags = ["-hls_flags", "single_file"] if single_file else []
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", str(seconds),
        "-filter_complex", "[0:v]split=2[a][b];[b]scale=320:180[bs]",
        "-map", "[a]", "-map", "[bs]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", "50", "-keyint_min", "50", "-sc_threshold", "0",
        "-c:a", "aac",
        "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod", "-hls_segment_type", "fmp4",
        "-hls_fmp4_init_filename", "init.mp4",
        *flags,
        "-hls_segment_filename", "%v/media.mp4" if single_file else "%v/seg_%03d.m4s",
        "-var_stream_map", "v:0,agroup:aud,name:v360 v:1,agroup:aud,name:v180 a:0,agroup:aud,name:audio,default:yes",
        "-master_pl_name", "master.m3u8",
        "%v/index.m3u8",
    ]
    subprocess.run(cmd, cwd=dest, check=True)  # noqa: S603
    return dest / "master.m3u8"


_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


class MockCdn:
    """Serves a directory over HTTP with Range support, request logging and failure injection."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.log: list[tuple[str, Optional[str], int]] = []  # (path, range header, status)
        self.headers_seen: list[tuple[str, dict]] = []  # (path, lowercase headers)
        self.delay: float = 0.0  # seconds per media response (for cancel tests)
        self.fail_first: dict[str, int] = {}  # path suffix -> number of 503s before serving normally
        self.deny_media_after: Optional[int] = None  # after this many media 200/206 responses -> 403
        self.html_for: set[str] = set()  # path suffixes answered with an HTML page and status 200
        self.override: dict[str, bytes] = {}  # path -> body served instead of the file
        self._media_ok = 0
        self._lock = threading.Lock()
        cdn = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # silence
                pass

            def do_GET(self) -> None:  # noqa: N802
                cdn._handle(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    # -- lifecycle -----------------------------------------------------------
    def __enter__(self) -> "MockCdn":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def url(self, rel: str) -> str:
        return f"{self.base}/{rel}"

    # -- helpers for assertions ---------------------------------------------
    def served(self, suffix: str) -> int:
        return sum(1 for p, _r, s in self.log if p.endswith(suffix) and s in (200, 206))

    def media_requests_ok(self) -> int:
        return sum(1 for p, _r, s in self.log if not p.endswith(".m3u8") and s in (200, 206))

    # -- request handling ----------------------------------------------------
    def _handle(self, h: BaseHTTPRequestHandler) -> None:
        path = unquote(urlsplit(h.path).path)
        rng = h.headers.get("Range")
        with self._lock:
            self.headers_seen.append((path, {k.lower(): v for k, v in h.headers.items()}))
        if self.delay and not path.endswith(".m3u8"):
            import time as _t
            _t.sleep(self.delay)
        is_playlist = path.endswith(".m3u8")

        def reply(status: int, body: bytes = b"", ctype: str = "application/octet-stream", extra: Optional[dict] = None) -> None:
            with self._lock:
                self.log.append((path, rng, status))
            h.send_response(status)
            h.send_header("Content-Type", ctype)
            h.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                h.send_header(k, v)
            h.end_headers()
            try:
                h.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        with self._lock:
            for suffix, remaining in list(self.fail_first.items()):
                if path.endswith(suffix) and remaining > 0:
                    self.fail_first[suffix] = remaining - 1
                    fail = True
                    break
            else:
                fail = False
            deny = (
                not is_playlist
                and self.deny_media_after is not None
                and self._media_ok >= self.deny_media_after
            )
        if fail:
            return reply(503, b"busy", "text/plain")
        if deny:
            return reply(403, b"expired", "text/plain")
        if any(path.endswith(s) for s in self.html_for):
            return reply(200, b"<html><body>oops</body></html>", "text/html")

        if path in self.override:
            data = self.override[path]
        else:
            file = (self.root / path.lstrip("/")).resolve()
            if not str(file).startswith(str(self.root.resolve())) or not file.is_file():
                return reply(404, b"nope", "text/plain")
            data = file.read_bytes()
        ctype = "application/vnd.apple.mpegurl" if is_playlist else "video/mp4"

        if rng:
            m = _RANGE_RE.match(rng)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else len(data) - 1
                end = min(end, len(data) - 1)
                chunk = data[start : end + 1]
                if not is_playlist:
                    with self._lock:
                        self._media_ok += 1
                return reply(206, chunk, ctype, {"Content-Range": f"bytes {start}-{end}/{len(data)}"})
        if not is_playlist:
            with self._lock:
                self._media_ok += 1
        return reply(200, data, ctype)


def run_with(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False
