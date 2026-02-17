"""ffprobe wrapper — structured input validation and metadata extraction."""

import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Optional


@dataclass
class ProbeResult:
    path: str
    codec_name: str
    width: int
    height: int
    pix_fmt: str
    fps: float
    duration: float          # seconds
    frame_count: int         # estimated if not exact
    colorspace: str
    color_primaries: str
    color_trc: str
    is_image: bool
    is_vfr: bool             # variable frame rate detected
    rotation: int
    format_name: str

    @property
    def resolution(self) -> tuple[int, int]:
        return (self.width, self.height)

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width


class ProbeError(Exception):
    pass


def ensure_ffprobe() -> str:
    """Return ffprobe path or raise."""
    path = shutil.which("ffprobe")
    if path is None:
        raise ProbeError("ffprobe not found on PATH. Install via: brew install ffmpeg")
    return path


def probe(filepath: str | Path) -> ProbeResult:
    """Run ffprobe on a file and return structured metadata."""
    filepath = Path(filepath)
    if not filepath.exists():
        raise ProbeError(f"File not found: {filepath}")

    ffprobe = ensure_ffprobe()
    cmd = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(filepath),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ProbeError(f"ffprobe failed on {filepath}: {result.stderr}")

    data = json.loads(result.stdout)

    # Find the video stream
    video_stream = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            video_stream = s
            break

    if video_stream is None:
        raise ProbeError(f"No video stream found in {filepath}")

    fmt = data.get("format", {})
    format_name = fmt.get("format_name", "")

    # Detect if this is a still image
    image_formats = {"image2", "png_pipe", "jpeg_pipe", "webp_pipe", "bmp_pipe",
                     "tiff_pipe", "svg_pipe"}
    image_codecs = {"png", "mjpeg", "jpeg2000", "webp", "bmp", "tiff"}
    is_image = (
        bool(set(format_name.split(",")) & image_formats)
        or video_stream.get("codec_name", "") in image_codecs
    )

    # FPS parsing
    fps = _parse_fps(video_stream)

    # VFR detection: avg_frame_rate != r_frame_rate
    avg_fps = _parse_rate(video_stream.get("avg_frame_rate", "0/1"))
    r_fps = _parse_rate(video_stream.get("r_frame_rate", "0/1"))
    is_vfr = abs(avg_fps - r_fps) > 0.5 if (avg_fps > 0 and r_fps > 0) else False

    # Duration
    duration = float(video_stream.get("duration", fmt.get("duration", 0)))

    # Frame count
    frame_count = _get_frame_count(video_stream, duration, fps)

    # Rotation (from side_data_list or tags)
    rotation = _get_rotation(video_stream)

    # Dimensions (swap if rotated 90/270)
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    if rotation in (90, 270):
        width, height = height, width

    return ProbeResult(
        path=str(filepath),
        codec_name=video_stream.get("codec_name", "unknown"),
        width=width,
        height=height,
        pix_fmt=video_stream.get("pix_fmt", "unknown"),
        fps=fps,
        duration=duration,
        frame_count=frame_count,
        colorspace=video_stream.get("color_space", "unknown"),
        color_primaries=video_stream.get("color_primaries", "unknown"),
        color_trc=video_stream.get("color_transfer", "unknown"),
        is_image=is_image,
        is_vfr=is_vfr,
        rotation=rotation,
        format_name=format_name,
    )


def probe_mean_luma(filepath: str | Path) -> float:
    """Compute mean luma (0-255) of an entire file using ffmpeg signalstats."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ProbeError("ffmpeg not found on PATH")

    cmd = [
        ffmpeg,
        "-i", str(filepath),
        "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    # Parse YAVG values from stderr
    values = []
    for line in result.stderr.splitlines():
        if "lavfi.signalstats.YAVG" in line:
            try:
                val = float(line.split("=")[-1].strip())
                values.append(val)
            except ValueError:
                continue

    if not values:
        raise ProbeError(f"Could not compute luma for {filepath}")

    return sum(values) / len(values)


def _parse_fps(stream: dict) -> float:
    """Extract fps from stream, preferring r_frame_rate."""
    rate_str = stream.get("r_frame_rate", stream.get("avg_frame_rate", "24/1"))
    return _parse_rate(rate_str)


def _parse_rate(rate_str: str) -> float:
    """Parse a fractional rate string like '24000/1001' to float."""
    try:
        frac = Fraction(rate_str)
        return float(frac) if frac > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _get_frame_count(stream: dict, duration: float, fps: float) -> int:
    """Get frame count: prefer nb_frames, fall back to duration * fps."""
    nb = stream.get("nb_frames")
    if nb and nb != "N/A":
        try:
            return int(nb)
        except ValueError:
            pass
    # Estimate
    if duration > 0 and fps > 0:
        return max(1, round(duration * fps))
    return 0


def _get_rotation(stream: dict) -> int:
    """Extract rotation from stream side data or tags."""
    # Check side_data_list (newer ffprobe)
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            try:
                return abs(int(sd["rotation"]))
            except (ValueError, TypeError):
                pass
    # Check tags
    tags = stream.get("tags", {})
    rotate = tags.get("rotate", "0")
    try:
        return abs(int(rotate))
    except ValueError:
        return 0
