"""Final stream-copy mux of the video-only and audio-only files, plus validation with ffprobe."""
import os
from typing import Optional

from .errors import FfmpegFailedError, ValidationFailedError
from .ffmpeg_pipeline import PipelineConfig, run_ffmpeg
from .tools import probe_json


def mux_streams(cfg: PipelineConfig, video_path: str, audio_path: Optional[str], output_path: str,
                duration: Optional[float] = None) -> None:
    part = output_path + ".part"
    if audio_path:
        args = ["-i", video_path, "-i", audio_path, "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-f", "matroska", part]
    else:
        args = ["-i", video_path, "-map", "0", "-c", "copy", "-f", "matroska", part]
    run_ffmpeg(cfg, args, label="mux", duration=duration)
    os.replace(part, output_path)


def validate_output(cfg: PipelineConfig, path: str, expected_duration: Optional[float], need_audio: bool = True) -> dict:
    """Raise ValidationFailedError unless the file is a complete, playable-looking recording."""
    try:
        info = probe_json(cfg.tools, path)
    except (ValueError, OSError) as e:
        raise ValidationFailedError(f"ffprobe rejected the output: {e}") from None
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise ValidationFailedError("output has no video stream")
    if need_audio and a is None:
        raise ValidationFailedError("output has no audio stream")
    try:
        dur = float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        raise ValidationFailedError("output has no readable duration") from None
    if expected_duration and abs(dur - expected_duration) > max(3.0, 0.02 * expected_duration):
        raise ValidationFailedError(f"duration {dur:.1f}s does not match the expected {expected_duration:.1f}s (incomplete download?)")
    return {"duration": dur, "video": v.get("codec_name"), "width": v.get("width"), "height": v.get("height"),
            "audio": a.get("codec_name") if a else None, "size": os.path.getsize(path)}
