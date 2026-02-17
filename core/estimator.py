"""Dry-run estimator — probe inputs and predict pipeline time and output size."""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import SplicerConfig
from .probe import probe, ProbeError

# Conservative default rates (pessimistic — used when no calibration file exists)
DEFAULT_RATES = {
    "normalize_fps": 40.0,
    "chunk_fps": 100.0,
    "assemble_concat_fps": 2000.0,
    "luma_probe_fps": 200.0,
    "luma_encode_fps": 120.0,
    "bytes_per_frame": 25000.0,
    "resolution": [1920, 1080],
}

# Fixed overhead estimates (seconds)
_PROBE_TIME_VIDEO = 0.15
_PROBE_TIME_IMAGE = 0.10

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".gif",
}


@dataclass
class InputStats:
    video_count: int = 0
    image_count: int = 0
    total_video_frames: int = 0
    total_video_duration: float = 0.0
    video_details: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class Estimate:
    inputs: InputStats = field(default_factory=InputStats)
    config_summary: dict = field(default_factory=dict)

    # Output estimates
    estimated_chunks: int = 0
    estimated_content_frames: int = 0
    estimated_image_frames: int = 0
    estimated_buffer_frames: int = 0
    estimated_total_frames: int = 0
    estimated_output_duration: float = 0.0
    estimated_output_size_bytes: int = 0

    # Time estimates (seconds)
    time_probe: float = 0.0
    time_normalize: float = 0.0
    time_chunk: float = 0.0
    time_assemble: float = 0.0
    time_total: float = 0.0

    calibration_source: str = "defaults"

    def print_summary(self) -> str:
        """Format and print estimation summary. Returns the formatted text."""
        lines = []
        lines.append("=" * 60)
        lines.append("DRY RUN — Pipeline Estimate")
        lines.append("=" * 60)

        # Input stats
        inp = self.inputs
        lines.append(f"\nInputs:")
        lines.append(f"  Videos: {inp.video_count}  ({inp.total_video_frames} frames, {inp.total_video_duration:.1f}s)")
        lines.append(f"  Images: {inp.image_count}")
        if inp.skipped:
            lines.append(f"  Skipped: {len(inp.skipped)}")
            for s in inp.skipped[:5]:
                lines.append(f"    - {s}")

        # Config summary
        cs = self.config_summary
        lines.append(f"\nConfig:")
        lines.append(f"  Resolution: {cs.get('resolution', '?')}")
        lines.append(f"  FPS: {cs.get('fps', '?')}")
        lines.append(f"  Chunk range: {cs.get('chunk_min', '?')}-{cs.get('chunk_max', '?')} frames")
        lines.append(f"  Anti-strobe: {'on' if cs.get('antistrobe') else 'off'}"
                      f"  (buffer={cs.get('buffer_frames', 0)}, luma={cs.get('luma_strength', 0):.2f})")
        lines.append(f"  Workers: {cs.get('workers', '?')}")

        # Output estimates
        lines.append(f"\nEstimated output:")
        lines.append(f"  Chunks: ~{self.estimated_chunks}")
        lines.append(f"  Content frames: ~{self.estimated_content_frames}")
        if self.estimated_image_frames:
            lines.append(f"  Image frames: ~{self.estimated_image_frames}")
        if self.estimated_buffer_frames:
            lines.append(f"  Buffer frames: ~{self.estimated_buffer_frames}")
        lines.append(f"  Total frames: ~{self.estimated_total_frames}")
        lines.append(f"  Duration: ~{self.estimated_output_duration:.1f}s")
        lines.append(f"  File size: ~{_format_bytes(self.estimated_output_size_bytes)}")

        # Time estimates
        lines.append(f"\nEstimated time:")
        lines.append(f"  Probe:     {_format_time(self.time_probe)}")
        lines.append(f"  Normalize: {_format_time(self.time_normalize)}")
        lines.append(f"  Chunk:     {_format_time(self.time_chunk)}")
        lines.append(f"  Assemble:  {_format_time(self.time_assemble)}")
        lines.append(f"  --------------------------------")
        lines.append(f"  Total:     {_format_time(self.time_total)}")

        lines.append(f"\nCalibration: {self.calibration_source}")
        lines.append("=" * 60)

        text = "\n".join(lines)
        print(text)
        return text


def load_calibration(path: Optional[Path] = None) -> tuple[dict, str]:
    """Load calibration data from disk. Returns (rates_dict, source_description).

    Falls back to DEFAULT_RATES if file is missing or invalid.
    """
    if path is None:
        path = Path(CALIBRATION_FILENAME)
    else:
        path = Path(path)

    # Also check current directory alias
    from .bench import CALIBRATION_FILENAME as _CF
    search_paths = [path] if path.name != _CF else [path, Path.cwd() / _CF]

    for p in search_paths:
        if p.exists():
            try:
                with open(p) as f:
                    data = json.load(f)
                if data.get("splicer_calibration_version"):
                    source = f"calibration file: {p}"
                    return data, source
            except (json.JSONDecodeError, OSError):
                pass

    return dict(DEFAULT_RATES), "conservative defaults (no calibration file — run --benchmark)"


# Shared calibration filename for cross-module reference
CALIBRATION_FILENAME = ".splicer_calibration.json"


def estimate(
    input_paths: list[Path],
    config: SplicerConfig,
    calibration_path: Optional[Path] = None,
) -> Estimate:
    """Probe all inputs and produce a pipeline estimate."""
    cal_data, cal_source = load_calibration(calibration_path)

    # Resolution scaling factor
    cal_res = cal_data.get("resolution", DEFAULT_RATES["resolution"])
    if isinstance(cal_res, list) and len(cal_res) == 2:
        cal_pixels = cal_res[0] * cal_res[1]
    else:
        cal_pixels = 1920 * 1080
    target_pixels = config.width * config.height
    res_scale = target_pixels / cal_pixels if cal_pixels > 0 else 1.0

    # Extract rates, applying resolution scaling to per-frame rates
    normalize_fps = cal_data.get("normalize_fps", DEFAULT_RATES["normalize_fps"]) / res_scale
    chunk_fps = cal_data.get("chunk_fps", DEFAULT_RATES["chunk_fps"]) / res_scale
    concat_fps = cal_data.get("assemble_concat_fps", DEFAULT_RATES["assemble_concat_fps"])
    luma_probe_fps = cal_data.get("luma_probe_fps", DEFAULT_RATES["luma_probe_fps"]) / res_scale
    luma_encode_fps = cal_data.get("luma_encode_fps", DEFAULT_RATES["luma_encode_fps"]) / res_scale
    bytes_per_frame = cal_data.get("bytes_per_frame", DEFAULT_RATES["bytes_per_frame"]) * res_scale

    # Probe all inputs
    stats = _probe_inputs(input_paths)

    avg_chunk_frames = (config.chunk_frames_min + config.chunk_frames_max) / 2.0
    avg_image_frames = (config.image_frames_min + config.image_frames_max) / 2.0
    workers = config.max_workers

    # Chunk count estimate
    estimated_chunks = int(math.ceil(stats.total_video_frames / avg_chunk_frames)) if avg_chunk_frames > 0 else 0

    # Frame counts
    content_frames = stats.total_video_frames
    image_frames = int(stats.image_count * avg_image_frames)
    total_content = estimated_chunks + stats.image_count  # number of segments for buffer calc

    buffer_frames = 0
    if config.antistrobe_enabled and config.antistrobe_buffer_frames > 0 and total_content > 1:
        buffer_frames = (total_content - 1) * config.antistrobe_buffer_frames

    total_frames = content_frames + image_frames + buffer_frames

    # Time estimates
    t_probe = stats.video_count * _PROBE_TIME_VIDEO + stats.image_count * _PROBE_TIME_IMAGE
    t_normalize = stats.total_video_frames / (normalize_fps * min(workers, max(stats.video_count, 1)))
    t_chunk = stats.total_video_frames / (chunk_fps * min(workers, max(stats.video_count, 1)))

    # Assemble sub-stages
    unique_clips = estimated_chunks + stats.image_count
    t_luma_probe = 0.0
    t_luma_encode = 0.0
    if config.antistrobe_enabled and config.antistrobe_luma_strength > 0:
        luma_frames = content_frames + image_frames
        t_luma_probe = luma_frames / (luma_probe_fps * workers) if luma_probe_fps > 0 else 0
        t_luma_encode = luma_frames / (luma_encode_fps * workers) if luma_encode_fps > 0 else 0
    t_concat = total_frames / concat_fps if concat_fps > 0 else 0
    t_assemble = t_luma_probe + t_luma_encode + t_concat

    t_total = t_probe + t_normalize + t_chunk + t_assemble

    # Output size
    output_size = int(total_frames * bytes_per_frame)
    output_duration = total_frames / config.target_fps if config.target_fps > 0 else 0

    est = Estimate(
        inputs=stats,
        config_summary={
            "resolution": f"{config.width}x{config.height}",
            "fps": config.target_fps,
            "chunk_min": config.chunk_frames_min,
            "chunk_max": config.chunk_frames_max,
            "antistrobe": config.antistrobe_enabled,
            "buffer_frames": config.antistrobe_buffer_frames,
            "luma_strength": config.antistrobe_luma_strength,
            "workers": config.max_workers,
            "preset": config.preset,
        },
        estimated_chunks=estimated_chunks,
        estimated_content_frames=content_frames,
        estimated_image_frames=image_frames,
        estimated_buffer_frames=buffer_frames,
        estimated_total_frames=total_frames,
        estimated_output_duration=round(output_duration, 1),
        estimated_output_size_bytes=output_size,
        time_probe=round(t_probe, 2),
        time_normalize=round(t_normalize, 2),
        time_chunk=round(t_chunk, 2),
        time_assemble=round(t_assemble, 2),
        time_total=round(t_total, 2),
        calibration_source=cal_source,
    )

    return est


def _probe_inputs(input_paths: list[Path]) -> InputStats:
    """Probe each input file and collect statistics."""
    stats = InputStats()

    for p in input_paths:
        ext = p.suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            try:
                probe(p)
                stats.image_count += 1
            except ProbeError as e:
                stats.skipped.append(f"{p.name}: {e}")
        elif ext in VIDEO_EXTENSIONS:
            try:
                info = probe(p)
                stats.video_count += 1
                stats.total_video_frames += info.frame_count
                stats.total_video_duration += info.duration
                stats.video_details.append({
                    "name": p.name,
                    "frames": info.frame_count,
                    "duration": round(info.duration, 2),
                    "resolution": f"{info.width}x{info.height}",
                    "fps": info.fps,
                })
            except ProbeError as e:
                stats.skipped.append(f"{p.name}: {e}")
        else:
            stats.skipped.append(f"{p.name}: unsupported extension")

    return stats


def _format_bytes(n: int) -> str:
    """Human-readable byte size."""
    if n < 1024:
        return f"{n} B"
    elif n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 ** 3:
        return f"{n / (1024 ** 2):.1f} MB"
    else:
        return f"{n / (1024 ** 3):.2f} GB"


def _format_time(seconds: float) -> str:
    """Human-readable time duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = seconds % 60
        return f"{m}m {s:.0f}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"
