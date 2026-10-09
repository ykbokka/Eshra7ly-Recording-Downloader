import argparse
import ctypes
import os
import shutil
import sys
import tempfile
from pathlib import Path

import config
from core.errors import ArchiverError, CancelledError
from core.ffmpeg_pipeline import PipelineConfig, download_audio, download_video
from core.filenames import sanitize_filename
from core.mux import mux_streams, validate_output
from core.stream_classifier import build_plan, describe_plan
from core.tools import find_tools, probe_json
from platforms.eshra7ly import eshra7lyplatform


def make_probe(tools, headers, cfg):
    from core.ffmpeg_pipeline import header_blob

    def probe(url):
        extra = ["-protocol_whitelist", cfg.protocol_whitelist]
        blob = header_blob(headers)
        if blob:
            extra += ["-headers", blob]
        return probe_json(tools, url, extra=extra)
    return probe


def _set_hidden(path):
    """Mark app-managed temporary paths hidden on Windows; harmless elsewhere."""
    if os.name == "nt":
        try:
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            if attrs != -1:
                ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs | 0x2)
        except Exception:
            pass


def _format_bytes(size):
    if size is None:
        return "size unavailable"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def run_pipeline(plan, cfg, out_dir, name, *, keep_temp=False, force=False,
                 on_progress=None, on_status=None):
    def status(message):
        print(message)
        if on_status:
            try:
                on_status(message)
            except Exception:
                pass

    progress_args = {"on_progress": on_progress} if on_progress else {}
    os.makedirs(out_dir, exist_ok=True)
    final = os.path.join(out_dir, name + ".mkv")
    estimate = getattr(plan, "estimated_size_bytes", None)
    if estimate:
        # Soft-reserve the expected space by preflighting with a safety margin.
        required = int(estimate * 1.15) + 256 * 1024 * 1024
        free = shutil.disk_usage(out_dir).free
        status(f"Estimated final size: about {_format_bytes(estimate)} · checking disk space…")
        if free < required:
            raise ArchiverError(
                f"Not enough free disk space for this recording. Estimated size: {_format_bytes(estimate)}; "
                f"free: {_format_bytes(free)}. Keep at least {_format_bytes(required)} free and retry."
            )
        status(f"Space check passed · about {_format_bytes(estimate)} expected, {_format_bytes(free)} free.")
    else:
        status("Recording size estimate unavailable for this stream; no reliable bitrate was provided.")
    if os.path.exists(final) and not force:
        try:
            status("Checking existing output file…")
            info = validate_output(cfg, final, plan.duration, need_audio=True)
            status(f"Already downloaded and valid · {os.path.basename(final)}")
            print(f"already downloaded and valid: {final}  ({info['duration']:.0f}s)")
            return final
        except ArchiverError as e:
            print(f"existing file is not valid ({e}); downloading again")

    # Keep intermediate .part files inside a hidden app-local staging directory.
    temp_root = Path(__file__).resolve().parent / ".eshra7ly_temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    _set_hidden(temp_root)
    tmp = tempfile.mkdtemp(prefix="job_", dir=str(temp_root))
    _set_hidden(tmp)
    video, audio = os.path.join(tmp, "video.mkv"), os.path.join(tmp, "audio.mka")
    try:
        status("Downloading video stream…")
        print("\n[1/3] video (ffmpeg stream copy)")
        download_video(cfg, plan, video, **progress_args)
        if plan.audio_url:
            how = "FLAC transcode of the AAC source" if cfg.audio_mode == "flac" else "original AAC kept as-is"
            status("Downloading audio track…")
            print(f"[2/3] audio ({how})")
            download_audio(cfg, plan, audio, **progress_args)
        else:
            status("Audio is already included in the video stream.")
            print("[2/3] audio: already inside the video stream")
        status("Muxing and validating the final file…")
        print("[3/3] muxing + validating")
        mux_streams(cfg, video, audio if plan.audio_url else None, final, plan.duration)
        info = validate_output(cfg, final, plan.duration, need_audio=True)
    except CancelledError:
        print("\ncancelled. Partial files remain in the hidden app temp folder.")
        raise
    except ArchiverError:
        print("\nfailed. Temporary files remain in the hidden app temp folder for inspection.")
        raise
    if not keep_temp:
        shutil.rmtree(tmp, ignore_errors=True)
    status(f"Completed · {final}")
    print(f"\ndone: {final}")
    print(f"  {info['video']} {info['width']}x{info['height']} + {info['audio']}, {info['duration']:.0f}s, {info['size']/1048576:.1f} MB")
    return final


def main(argv=None):
    ap = argparse.ArgumentParser(description="Course archiver - Stage 1 media pipeline")
    ap.add_argument("url", nargs="?", help="course URL (asked interactively if omitted)")
    ap.add_argument("--quality", default="best", help="best, 1440, 1080, 720, 480 (falls back gracefully)")
    ap.add_argument("--audio", choices=["copy", "flac"], default="copy",
                    help="copy = keep original AAC (default). flac = transcode AAC to FLAC (no quality gain)")
    ap.add_argument("--out", default=config.DEFAULT_OUTPUT_DIR)
    ap.add_argument("--name", help="output file name without extension (default: page title)")
    ap.add_argument("--ffmpeg", help="folder with ffmpeg + ffprobe (default: PATH)")
    ap.add_argument("--capture-timeout", type=int, default=config.CAPTURE_TIMEOUT)
    ap.add_argument("--probe", action="store_true", help="detect and show streams, then stop")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--keep-temp", action="store_true")
    ap.add_argument("--debug", action="store_true", help="print redacted ffmpeg commands")
    args = ap.parse_args(argv)

    try:
        tools = find_tools(args.ffmpeg)
        print("ffmpeg:", tools.version)
        url = args.url or "https://eshra7ly.net/student/recordings"
        info = eshra7lyplatform(capture_timeout=args.capture_timeout).extract_media_info(url)
        cfg = PipelineConfig(tools, info["headers"], audio_mode=args.audio, debug=args.debug)
        plan = build_plan(info["captures"], args.quality, headers=info["headers"], probe=make_probe(tools, info["headers"], cfg))
        print("\n=== detected ===")
        print("\n".join(describe_plan(plan)))
        if args.probe:
            return 0
        name = sanitize_filename(args.name or info.get("title") or "recording")
        run_pipeline(plan, cfg, args.out, name, keep_temp=args.keep_temp, force=args.force)
        return 0
    except CancelledError:
        return 130
    except ArchiverError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
