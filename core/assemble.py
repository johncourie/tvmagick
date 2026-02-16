"""Final assembly — concat, image interleaving, anti-strobe buffers, luma pass."""

import random
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import SplicerConfig
from .chunk import ChunkInfo
from .manifest import Manifest, ImageEntry, LumaFlag
from .normalize import normalize_image
from .probe import probe, probe_mean_luma


def assemble(
    chunks: list[ChunkInfo],
    image_paths: list[str | Path],
    config: SplicerConfig,
    rng: random.Random,
    manifest: Manifest,
    work_dir: Path,
    output_path: str | Path,
) -> Path:
    """Assemble chunks and images into final output.

    1. Shuffle chunks (seeded)
    2. Interleave image segments at random positions
    3. Insert anti-strobe buffer frames between cuts (if enabled)
    4. Optionally apply luma normalization
    5. Concat everything via ffmpeg demuxer
    6. Run luma delta safety pass
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Collect chunk file paths in randomized order
    chunk_paths = [c.chunk_path for c in chunks]
    rng.shuffle(chunk_paths)

    # Prepare image segments
    image_segments = _prepare_images(image_paths, config, rng, work_dir)

    # Record image entries in manifest
    # We'll insert them after building the sequence

    # Interleave images into the chunk sequence
    sequence = _interleave_images(chunk_paths, image_segments, rng, manifest)

    # Insert anti-strobe buffer frames
    if config.antistrobe_enabled and config.antistrobe_buffer_frames > 0:
        buffer_path = _generate_buffer_frame(config, work_dir)
        sequence = _insert_buffers(sequence, buffer_path, config)

    # Optional luma normalization pass
    if config.antistrobe_enabled and config.antistrobe_luma_strength > 0:
        sequence = _luma_normalize(sequence, config, work_dir)

    # Store sequence in manifest
    manifest.sequence_order = sequence

    # Compute expected frame count
    expected = _count_expected_frames(sequence)
    manifest.expected_frame_count = expected

    # Write concat manifest and run final assembly
    concat_path = work_dir / "concat_list.txt"
    _write_concat_file(sequence, concat_path)
    _run_concat(concat_path, output_path)

    # Verify output
    out_info = probe(output_path)
    manifest.set_output(output_path, out_info.frame_count)

    if out_info.frame_count != expected:
        print(
            f"  warning: expected {expected} frames, got {out_info.frame_count} "
            f"(delta: {out_info.frame_count - expected})"
        )

    # Luma delta safety pass
    if config.antistrobe_enabled:
        _luma_delta_pass(sequence, config, manifest)

    return output_path


def _prepare_images(
    image_paths: list[str | Path],
    config: SplicerConfig,
    rng: random.Random,
    work_dir: Path,
) -> list[str]:
    """Normalize images to video segments, return paths."""
    segments = []
    for i, img in enumerate(image_paths):
        duration = rng.randint(config.image_frames_min, config.image_frames_max)
        out = work_dir / f"img_segment_{i:04d}.mp4"
        normalize_image(img, out, config, duration_frames=duration)
        segments.append((str(out), duration))

    return segments


def _interleave_images(
    chunk_paths: list[str],
    image_segments: list[tuple[str, int]],
    rng: random.Random,
    manifest: Manifest,
) -> list[str]:
    """Insert image segments at random positions in the chunk sequence."""
    sequence = list(chunk_paths)

    for img_path, duration in image_segments:
        pos = rng.randint(0, len(sequence))
        sequence.insert(pos, img_path)
        manifest.add_image(ImageEntry(
            source_file=img_path,
            position=pos,
            duration_frames=duration,
        ))

    return sequence


def _generate_buffer_frame(config: SplicerConfig, work_dir: Path) -> str:
    """Generate a mid-gray buffer frame video (single frame)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found on PATH")

    w, h = config.width, config.height
    buf_path = work_dir / "buffer_gray.mp4"

    # Generate 1 frame of 50% gray at target resolution
    # Using lavfi color source
    frames = config.antistrobe_buffer_frames
    duration = frames / config.target_fps

    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x808080:s={w}x{h}:r={config.target_fps}:d={duration}",
        "-vf", f"format={config.target_pix_fmt}",
        "-colorspace", config.target_colorspace,
        "-color_primaries", config.color_primaries,
        "-color_trc", config.color_trc,
        "-c:v", config.codec,
        "-preset", config.preset,
        "-g", "1",
        "-bf", "0",
        "-an",
        str(buf_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to generate buffer frame: {result.stderr[-1000:]}")

    return str(buf_path)


def _insert_buffers(
    sequence: list[str],
    buffer_path: str,
    config: SplicerConfig,
) -> list[str]:
    """Insert buffer frame(s) between every pair of clips."""
    if not sequence:
        return sequence

    buffered = [sequence[0]]
    for clip in sequence[1:]:
        buffered.append(buffer_path)
        buffered.append(clip)

    return buffered


def _luma_normalize(
    sequence: list[str],
    config: SplicerConfig,
    work_dir: Path,
) -> list[str]:
    """Apply luma normalization toward global mean. Returns updated sequence."""
    strength = config.antistrobe_luma_strength
    if strength <= 0:
        return sequence

    # Compute global mean luma across all unique clips
    unique_paths = list(set(sequence))
    luma_map: dict[str, float] = {}
    for p in unique_paths:
        try:
            luma_map[p] = probe_mean_luma(p)
        except Exception:
            luma_map[p] = 128.0  # default to mid-gray on failure

    if not luma_map:
        return sequence

    global_mean = sum(luma_map.values()) / len(luma_map)

    ffmpeg = shutil.which("ffmpeg")
    normalized = []
    norm_dir = work_dir / "luma_normalized"
    norm_dir.mkdir(exist_ok=True)

    # Cache: don't re-normalize the same file twice
    norm_cache: dict[str, str] = {}

    for i, clip_path in enumerate(sequence):
        clip_luma = luma_map.get(clip_path, 128.0)
        delta = global_mean - clip_luma

        # If delta is negligible, skip
        if abs(delta) < 2.0:
            normalized.append(clip_path)
            continue

        if clip_path in norm_cache:
            normalized.append(norm_cache[clip_path])
            continue

        # Apply eq filter: brightness adjustment scaled by strength
        # eq brightness is in range -1.0 to 1.0, maps to -255 to 255
        brightness = (delta / 255.0) * strength
        brightness = max(-1.0, min(1.0, brightness))

        out_path = norm_dir / f"lnorm_{i:05d}.mp4"
        cmd = [
            ffmpeg, "-y",
            "-i", clip_path,
            "-vf", f"eq=brightness={brightness:.4f}",
            "-c:v", config.codec,
            "-preset", config.preset,
            "-g", "1",
            "-bf", "0",
            "-an",
            str(out_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            # Fall back to original on failure
            normalized.append(clip_path)
            continue

        norm_cache[clip_path] = str(out_path)
        normalized.append(str(out_path))

    return normalized


def _count_expected_frames(sequence: list[str]) -> int:
    """Sum frame counts across the sequence."""
    total = 0
    # Cache probes since buffer frames repeat
    cache: dict[str, int] = {}
    for path in sequence:
        if path not in cache:
            try:
                info = probe(path)
                cache[path] = info.frame_count
            except Exception:
                cache[path] = 0
        total += cache[path]
    return total


def _write_concat_file(sequence: list[str], concat_path: Path) -> None:
    """Write ffmpeg concat demuxer manifest."""
    with open(concat_path, "w") as f:
        for path in sequence:
            # Escape single quotes in paths
            safe_path = str(Path(path).resolve()).replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")


def _run_concat(concat_path: Path, output_path: Path) -> None:
    """Run ffmpeg concat demuxer to produce final output."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found on PATH")

    cmd = [
        ffmpeg, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_path),
        "-c", "copy",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-2000:]}")


def _luma_delta_pass(
    sequence: list[str],
    config: SplicerConfig,
    manifest: Manifest,
) -> None:
    """Post-assembly safety pass: flag adjacent clips with high luma delta."""
    threshold = config.antistrobe_delta_threshold
    if threshold <= 0:
        return

    # Build luma map (reuse cache)
    luma_cache: dict[str, float] = {}
    for path in sequence:
        if path not in luma_cache:
            try:
                luma_cache[path] = probe_mean_luma(path)
            except Exception:
                luma_cache[path] = 128.0

    for i in range(len(sequence) - 1):
        luma_a = luma_cache.get(sequence[i], 128.0)
        luma_b = luma_cache.get(sequence[i + 1], 128.0)
        delta = abs(luma_a - luma_b)

        if delta > threshold:
            manifest.add_luma_flag(LumaFlag(
                position=i,
                delta=delta,
                threshold=threshold,
            ))
            print(f"  luma warning: position {i}->{i+1} delta={delta:.1f} (threshold={threshold})")
