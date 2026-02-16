"""Preprocessing — coarse-cut and transform raw source material before the splicer pipeline."""

import shutil
import subprocess
from pathlib import Path

from .config import SplicerConfig
from .probe import probe, ProbeError


VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg", ".ts"}


def grain_video(input_path: Path, output_dir: Path, config: SplicerConfig) -> list[Path]:
    """Split a long video into segments of ~grain_duration seconds.

    Uses ffmpeg segment muxer with codec copy (no re-encode) for speed.
    Skips videos already shorter than grain_duration.
    Returns list of output segment paths.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)

    try:
        info = probe(input_path)
    except ProbeError as e:
        print(f"    SKIPPED (probe failed): {e}")
        return []

    if info.duration <= config.grain_duration:
        print(f"    {input_path.name}: {info.duration:.1f}s — already under {config.grain_duration}s, copying")
        dest = output_dir / f"{input_path.stem}_grain_000{input_path.suffix}"
        _copy_file(input_path, dest)
        return [dest]

    print(f"    {input_path.name}: {info.duration:.1f}s — splitting into ~{config.grain_duration}s segments")

    ffmpeg = _get_ffmpeg()
    pattern = str(output_dir / f"{input_path.stem}_grain_%03d.mp4")

    cmd = [
        ffmpeg, "-y",
        "-i", str(input_path),
        "-c", "copy",
        "-f", "segment",
        "-segment_time", str(config.grain_duration),
        "-reset_timestamps", "1",
        "-an",
        pattern,
    ]

    _run_ffmpeg(cmd, f"graining {input_path.name}")

    # Collect output files (segment muxer creates _000, _001, etc.)
    segments = sorted(output_dir.glob(f"{input_path.stem}_grain_*.mp4"))
    print(f"    -> {len(segments)} segments")
    return segments


def greyscale_video(input_path: Path, output_dir: Path, config: SplicerConfig) -> Path:
    """Re-encode a video to greyscale using hue=s=0 filter.

    Returns path to the greyscale output file.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)

    output_path = output_dir / f"{input_path.stem}_grey.mp4"
    print(f"    {input_path.name} -> {output_path.name}")

    ffmpeg = _get_ffmpeg()
    cmd = [
        ffmpeg, "-y",
        "-i", str(input_path),
        "-vf", "hue=s=0",
        "-c:v", config.codec,
        "-preset", config.preset,
        "-pix_fmt", "yuv420p",
        "-an",
        str(output_path),
    ]

    _run_ffmpeg(cmd, f"greyscale {input_path.name}")
    return output_path


def collect_videos(paths: list[str]) -> list[Path]:
    """Expand directories and filter to supported video types."""
    result: list[Path] = []
    for p_str in paths:
        p = Path(p_str)
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS:
                    result.append(child)
        elif p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
            result.append(p)
        else:
            print(f"  skipping: {p} (not a supported video file or directory)")
    return result


def _get_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found on PATH")
    return ffmpeg


def _copy_file(src: Path, dst: Path) -> None:
    shutil.copy2(src, dst)


def _run_ffmpeg(cmd: list[str], description: str = "") -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed ({description}):\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stderr: {result.stderr[-2000:]}"
        )
    return result
