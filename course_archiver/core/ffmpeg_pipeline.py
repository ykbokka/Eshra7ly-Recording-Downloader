"""FFmpeg-driven HLS/fMP4 pipeline: video-only MKV, audio-only MKA, with progress, retry and cancel.

* FFmpeg's HLS demuxer handles init segments, byte ranges and fMP4; nothing is concatenated by hand.
* The signed playlist URL authorises each request, as in normal playback. Only Referer / Origin /
  User-Agent / Accept-Language from the player are forwarded -- never cookies or Authorization.
* Protocols are whitelisted to https so a hostile playlist cannot make ffmpeg read local files.
"""
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .errors import AccessDeniedError, CancelledError, FfmpegFailedError
from .models import StreamPlan, safe_headers
from .redact import redact_command, redact_text
from .tools import Tools, http_option_supported

_HTTP_ERR = re.compile(r"(?:HTTP error|Server returned) (\d{3})")
_TRANSIENT = re.compile(
    r"(connection (reset|refused|timed out)|timed out|i/o error|input/output error|end of file|"
    r"server returned 5\d\d|http error 5\d\d|network is unreachable|broken pipe|temporary failure)", re.I)


@dataclass
class PipelineConfig:
    tools: Tools
    headers: Dict[str, str] = field(default_factory=dict)
    protocol_whitelist: str = "https,tls,tcp"   # tests may add http for a local server
    retries: int = 2
    backoff: float = 3.0
    io_timeout: float = 30.0
    audio_mode: str = "copy"                    # "copy" keeps the original AAC; "flac" is a transcode
    debug: bool = False
    cancel: Optional[threading.Event] = None


def header_blob(headers: Dict[str, str]) -> str:
    return "".join(f"{k.title() if k != 'user-agent' else 'User-Agent'}: {v}\r\n" for k, v in safe_headers(headers).items())


def input_args(cfg: PipelineConfig, url: str) -> List[str]:
    a = ["-protocol_whitelist", cfg.protocol_whitelist]
    blob = header_blob(cfg.headers)
    if blob:
        a += ["-headers", blob]
    a += ["-rw_timeout", str(int(cfg.io_timeout * 1_000_000))]
    for opt, val in (("reconnect", "1"), ("reconnect_streamed", "1"), ("reconnect_delay_max", "30")):
        if http_option_supported(cfg.tools, opt):
            a += [f"-{opt}", val]
    if http_option_supported(cfg.tools, "reconnect_on_http_error"):
        a += ["-reconnect_on_http_error", "429,500,502,503,504"]
    return a + ["-i", url]


def default_progress(label: str, pct: Optional[float], out_s: float, size: int, speed: str) -> None:
    p = f"{pct:5.1f}%" if pct is not None else "  ?  "
    sys.stdout.write(f"\r  {label:<6} {p}  {out_s/60:6.1f} min  {size/1048576:8.1f} MB  {speed:>7}   ")
    sys.stdout.flush()


def _drain(stream, sink):
    for line in iter(stream.readline, ""):
        sink(line)
    stream.close()


def run_ffmpeg(cfg: PipelineConfig, args: List[str], *, label: str, duration: Optional[float],
               on_progress: Callable = default_progress) -> None:
    """Run one ffmpeg job. Raises AccessDeniedError / FfmpegFailedError(transient flag) / CancelledError."""
    cmd = [cfg.tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-y", *args]
    if cfg.debug:
        print("\n  ffmpeg:", redact_command(cmd))
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", creationflags=flags)
    lines: "queue.Queue[Optional[str]]" = queue.Queue()
    err_tail: List[str] = []
    t1 = threading.Thread(target=_drain, args=(proc.stdout, lines.put), daemon=True)
    t2 = threading.Thread(target=_drain, args=(proc.stderr, lambda l: (err_tail.append(l.rstrip()), err_tail.__delitem__(slice(0, -60)))), daemon=True)
    t1.start(); t2.start()

    state = {"out_us": 0, "size": 0, "speed": ""}

    def stop_gracefully():
        try:
            proc.stdin.write("q\n"); proc.stdin.flush()   # lets ffmpeg finalize the container
        except Exception:
            pass
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    try:
        while True:
            if cfg.cancel is not None and cfg.cancel.is_set():
                stop_gracefully()
                raise CancelledError("cancelled")
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                if proc.poll() is not None and not t1.is_alive():
                    break
                continue
            k, _, v = line.strip().partition("=")
            if k in ("out_time_us", "out_time_ms") and v.lstrip("-").isdigit():
                state["out_us"] = max(0, int(v))
            elif k == "total_size" and v.isdigit():
                state["size"] = int(v)
            elif k == "speed":
                state["speed"] = v.strip()
            elif k == "progress":
                out_s = state["out_us"] / 1_000_000
                pct = min(100.0, 100 * out_s / duration) if duration else None
                on_progress(label, pct, out_s, state["size"], state["speed"])
                if v.strip() == "end":
                    break
    except KeyboardInterrupt:
        stop_gracefully()
        raise CancelledError("cancelled") from None
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        raise

    try:
        rc = proc.wait(timeout=120)   # normal finish: let ffmpeg write the trailer, never kill it here
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = proc.wait()
    t2.join(timeout=2)
    sys.stdout.write("\n")
    if rc == 0:
        return
    tail = redact_text(" | ".join(x for x in err_tail[-4:] if x))
    m = _HTTP_ERR.search(" ".join(err_tail))
    if m and m.group(1) in ("401", "403", "404", "410"):
        raise AccessDeniedError(
            f"the server refused the {label} stream (HTTP {m.group(1)}). The signed link probably expired or this "
            "recording is not available to this session. Re-run to capture fresh links; nothing is bypassed.")
    if "not on whitelist" in tail.lower():
        raise FfmpegFailedError(f"ffmpeg blocked a protocol outside '{cfg.protocol_whitelist}': {tail}")
    err = FfmpegFailedError(f"ffmpeg failed on {label} (exit {rc}): {tail or 'no error text'}")
    err.transient = bool(_TRANSIENT.search(" ".join(err_tail)))  # type: ignore[attr-defined]
    raise err


def run_with_retries(cfg: PipelineConfig, args: List[str], **kw) -> None:
    attempt = 0
    while True:
        try:
            return run_ffmpeg(cfg, args, **kw)
        except FfmpegFailedError as e:
            if not getattr(e, "transient", False) or attempt >= cfg.retries:
                raise
            attempt += 1
            wait = cfg.backoff * 2 ** (attempt - 1)
            print(f"  network hiccup, retry {attempt}/{cfg.retries} in {wait:.0f}s ...")
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                if cfg.cancel is not None and cfg.cancel.is_set():
                    raise CancelledError("cancelled")
                time.sleep(0.2)


def _finish(part: str, final: str) -> None:
    os.replace(part, final)


def download_video(cfg: PipelineConfig, plan: StreamPlan, out_path: str, **kw) -> None:
    """Video playlist -> video-only MKV (stream copy). Keeps muxed audio if the stream has no separate audio."""
    part = out_path + ".part"
    maps = ["-map", "0:v:0"] + ([] if plan.audio_url else ["-map", "0:a:0?"])
    run_with_retries(cfg, [*input_args(cfg, plan.video_url), *maps, "-c", "copy", "-f", "matroska", part],
                     label="video", duration=plan.duration, **kw)
    _finish(part, out_path)


def download_audio(cfg: PipelineConfig, plan: StreamPlan, out_path: str, **kw) -> None:
    """Audio playlist -> MKA. 'copy' preserves the original AAC bit-for-bit; 'flac' is a lossless *container*
    for the already lossy AAC (a transcode: it cannot restore quality that AAC discarded)."""
    if not plan.audio_url:
        return
    part = out_path + ".part"
    codec = ["-c:a", "flac", "-compression_level", "8"] if cfg.audio_mode == "flac" else ["-c:a", "copy"]
    run_with_retries(cfg, [*input_args(cfg, plan.audio_url), "-map", "0:a:0", "-vn", *codec, "-f", "matroska", part],
                     label="audio", duration=plan.duration, **kw)
    _finish(part, out_path)
