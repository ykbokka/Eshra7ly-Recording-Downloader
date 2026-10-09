"""Locate ffmpeg/ffprobe and probe their capabilities. Never fails silently."""
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, Optional

from .errors import FfmpegMissingError

HELP = (
    "ffmpeg/ffprobe not found.\n"
    "  Windows: winget install Gyan.FFmpeg   (then open a NEW terminal)\n"
    "  or pass --ffmpeg <folder containing ffmpeg.exe and ffprobe.exe>."
)


def _run(args, **kw):
    if sys.platform == "win32":
        kw.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    kw.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.run(args, **kw)


@dataclass
class Tools:
    ffmpeg: str
    ffprobe: str
    version: str


def find_tools(configured: Optional[str] = None) -> Tools:
    exe = ".exe" if sys.platform == "win32" else ""
    ff = fp = None
    if configured:
        d = configured if os.path.isdir(configured) else os.path.dirname(configured)
        for base in (d, os.path.join(d, "bin")):
            a, b = os.path.join(base, "ffmpeg" + exe), os.path.join(base, "ffprobe" + exe)
            if os.path.isfile(a) and os.path.isfile(b):
                ff, fp = a, b
                break
    if not ff:
        ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ff or not fp:
        raise FfmpegMissingError(HELP)
    try:
        p = _run([ff, "-hide_banner", "-version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        raise FfmpegMissingError(f"ffmpeg could not be started: {e}") from None
    if p.returncode != 0:
        raise FfmpegMissingError("ffmpeg returned an error when asked for its version")
    return Tools(ff, fp, (p.stdout.splitlines() or ["ffmpeg"])[0])


_cap_cache: Dict[tuple, bool] = {}


def http_option_supported(tools: Tools, option: str) -> bool:
    """True if this ffmpeg build's http protocol knows ``option`` (older builds lack some reconnect flags)."""
    key = (tools.ffmpeg, option)
    if key not in _cap_cache:
        try:
            p = _run([tools.ffmpeg, "-hide_banner", "-h", "protocol=http"], capture_output=True, text=True, timeout=15)
            _cap_cache[key] = f"-{option} " in (p.stdout + p.stderr)
        except (OSError, subprocess.SubprocessError):
            _cap_cache[key] = False
    return _cap_cache[key]


def probe_json(tools: Tools, target: str, extra=(), timeout=60) -> dict:
    cmd = [tools.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", *extra, target]
    p = _run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise ValueError((p.stderr or "ffprobe failed").strip().splitlines()[-1][:200])
    return json.loads(p.stdout or "{}")
