"""Frame-accurate chunk extraction with seeded random durations."""

import random
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import SplicerConfig
from .probe import probe


@dataclass
class ChunkInfo:
    """Metadata for a single extracted chunk."""
    source_file: str
    start_frame: int
    frame_count: int
    chunk_path: str
    chunk_index: int


def chunk_video(
    normalized_path: str | Path,
    output_dir: str | Path,
    config: SplicerConfig,
    rng: random.Random,
    start_index: int = 0,
) -> list[ChunkInfo]:
    """Split a normalized video into frame-accurate chunks.

    Returns a list of ChunkInfo describing each extracted chunk.
    start_index offsets chunk numbering (for multi-source pipelines).
    """
    normalized_path = Path(normalized_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    info = probe(normalized_path)
    total_frames = info.frame_count

    if total_frames <= 0:
        print(f"  warning: {normalized_path.name} has 0 frames, skipping")
        return []

    chunks: list[ChunkInfo] = []
    current_frame = 0
    chunk_idx = start_index

    while current_frame < total_frames:
        # Random duration within configured range
        duration = rng.randint(config.chunk_frames_min, config.chunk_frames_max)

        # Clamp to remaining frames
        remaining = total_frames - current_frame
        if remaining < config.chunk_frames_min:
            break  # Not enough frames for a valid chunk
        duration = min(duration, remaining)

        end_frame = current_frame + duration
        chunk_path = output_dir / f"chunk_{chunk_idx:05d}.mp4"

        _extract_chunk(
            input_path=normalized_path,
            output_path=chunk_path,
            start_frame=current_frame,
            end_frame=end_frame,
            config=config,
        )

        chunks.append(ChunkInfo(
            source_file=str(normalized_path),
            start_frame=current_frame,
            frame_count=duration,
            chunk_path=str(chunk_path),
            chunk_index=chunk_idx,
        ))

        current_frame = end_frame
        chunk_idx += 1

    return chunks


def _extract_chunk(
    input_path: Path,
    output_path: Path,
    start_frame: int,
    end_frame: int,
    config: SplicerConfig,
) -> None:
    """Extract a frame-accurate chunk using the trim filter."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found on PATH")

    vf = f"trim=start_frame={start_frame}:end_frame={end_frame},setpts=PTS-STARTPTS"

    cmd = [
        ffmpeg, "-y",
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", config.codec,
        "-preset", config.preset,
        "-g", "1",
        "-bf", "0",
        "-an",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg chunk extraction failed:\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stderr: {result.stderr[-2000:]}"
        )
