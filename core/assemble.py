"""Final assembly — concat, image interleaving, anti-strobe buffers, luma pass."""

import random
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    print(f"  shuffling {len(chunks)} chunks...")
    chunk_paths = [c.chunk_path for c in chunks]
    rng.shuffle(chunk_paths)

    # Build a map from chunk_path -> frame_count for metadata-based frame counting
    chunk_frame_map: dict[str, int] = {}
    for c in chunks:
        chunk_frame_map[c.chunk_path] = c.frame_count

    # Prepare image segments
    image_segments = _prepare_images(image_paths, config, rng, work_dir)
    if image_segments:
        print(f"  prepared {len(image_segments)} image segments")

    # Interleave images into the chunk sequence
    sequence = _interleave_images(chunk_paths, image_segments, rng, manifest)

    # Build image frame map for expected frame counting
    image_frame_map: dict[str, int] = {}
    for img_path, duration in image_segments:
        image_frame_map[img_path] = duration

    # Insert anti-strobe buffer frames
    pre_buffer_len = len(sequence)
    buffer_path = None
    if config.antistrobe_enabled and config.antistrobe_buffer_frames > 0:
        buffer_path = _generate_buffer_frame(config, work_dir)
        sequence = _insert_buffers(sequence, buffer_path, config)
        num_buffers = pre_buffer_len - 1 if pre_buffer_len > 0 else 0
        print(f"  inserted buffer frames ({pre_buffer_len} -> {len(sequence)} entries)")

    # Shared luma cache — used by both normalize and delta pass
    luma_cache: dict[str, float] = {}

    # Optional luma normalization pass
    norm_cache: dict[str, str] = {}  # original_path -> normalized_path
    if config.antistrobe_enabled and config.antistrobe_luma_strength > 0:
        sequence, norm_cache = _luma_normalize(sequence, config, work_dir, luma_cache)

    # Build a reverse map so normalized paths resolve to original frame counts
    norm_frame_map: dict[str, int] = {}
    for orig_path, norm_path in norm_cache.items():
        if orig_path in chunk_frame_map:
            norm_frame_map[norm_path] = chunk_frame_map[orig_path]
        elif orig_path in image_frame_map:
            norm_frame_map[norm_path] = image_frame_map[orig_path]

    # Store sequence in manifest
    manifest.sequence_order = sequence

    # Compute expected frame count from metadata (no subprocess calls)
    expected = _count_expected_frames(
        sequence, chunk_frame_map, image_frame_map, norm_frame_map,
        buffer_path, config,
    )
    manifest.expected_frame_count = expected

    # Write concat manifest and run final assembly
    concat_path = work_dir / "concat_list.txt"
    _write_concat_file(sequence, concat_path)
    print(f"  concat: {len(sequence)} entries -> {output_path.name}")
    _run_concat(concat_path, output_path, len(sequence))

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
        _luma_delta_pass(sequence, config, manifest, luma_cache)

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


def _probe_luma_parallel(
    paths: list[str],
    cache: dict[str, float],
    max_workers: int,
) -> None:
    """Probe mean luma for multiple paths in parallel, populating cache."""
    # Filter to paths not already cached
    to_probe = [p for p in paths if p not in cache]
    if not to_probe:
        return

    total = len(to_probe)
    print(f"  luma probe: 0/{total}", end="", flush=True)
    done = 0

    def _probe_one(path: str) -> tuple[str, float]:
        try:
            return path, probe_mean_luma(path)
        except Exception:
            return path, 128.0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_probe_one, p): p for p in to_probe}
        for future in as_completed(futures):
            path, luma = future.result()
            cache[path] = luma
            done += 1
            if done % 500 == 0 or done == total:
                print(f"\r  luma probe: {done}/{total}", end="", flush=True)

    print()  # newline after progress


def _luma_normalize(
    sequence: list[str],
    config: SplicerConfig,
    work_dir: Path,
    luma_cache: dict[str, float],
) -> tuple[list[str], dict[str, str]]:
    """Apply luma normalization toward global mean. Returns (sequence, norm_cache)."""
    strength = config.antistrobe_luma_strength
    if strength <= 0:
        return sequence, {}

    # Probe all unique clips in parallel
    unique_paths = list(set(sequence))
    _probe_luma_parallel(unique_paths, luma_cache, config.max_workers)

    if not luma_cache:
        return sequence, {}

    global_mean = sum(luma_cache[p] for p in unique_paths) / len(unique_paths)

    ffmpeg = shutil.which("ffmpeg")
    normalized = []
    norm_dir = work_dir / "luma_normalized"
    norm_dir.mkdir(exist_ok=True)

    # Determine which unique clips need encoding (delta >= 2.0)
    encode_jobs: list[tuple[str, float]] = []
    for p in unique_paths:
        clip_luma = luma_cache.get(p, 128.0)
        delta = global_mean - clip_luma
        if abs(delta) >= 2.0:
            encode_jobs.append((p, delta))

    # Parallel luma encoding
    norm_cache: dict[str, str] = {}
    if encode_jobs:
        total_enc = len(encode_jobs)
        print(f"  luma encode: 0/{total_enc}", end="", flush=True)
        done_enc = 0

        def _encode_one(args: tuple[int, str, float]) -> tuple[str, str | None, float]:
            idx, clip_path, delta = args
            brightness = (delta / 255.0) * strength
            brightness = max(-1.0, min(1.0, brightness))
            out_path = norm_dir / f"lnorm_{idx:05d}.mp4"
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
                return clip_path, None, delta
            # Estimate new luma from adjustment
            new_luma = luma_cache.get(clip_path, 128.0) + delta * strength
            return clip_path, str(out_path), new_luma

        with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
            futures = []
            for idx, (clip_path, delta) in enumerate(encode_jobs):
                futures.append(pool.submit(_encode_one, (idx, clip_path, delta)))
            for future in as_completed(futures):
                orig_path, out_path, new_luma = future.result()
                if out_path is not None:
                    norm_cache[orig_path] = out_path
                    luma_cache[out_path] = new_luma
                done_enc += 1
                if done_enc % 200 == 0 or done_enc == total_enc:
                    print(f"\r  luma encode: {done_enc}/{total_enc}", end="", flush=True)

        print()  # newline after progress

    # Build final sequence using norm_cache
    for clip_path in sequence:
        if clip_path in norm_cache:
            normalized.append(norm_cache[clip_path])
        else:
            normalized.append(clip_path)

    return normalized, norm_cache


def _count_expected_frames(
    sequence: list[str],
    chunk_frame_map: dict[str, int],
    image_frame_map: dict[str, int],
    norm_frame_map: dict[str, int],
    buffer_path: str | None,
    config: SplicerConfig,
) -> int:
    """Compute expected frame count from metadata — no subprocess calls."""
    total = 0
    for path in sequence:
        if buffer_path is not None and path == buffer_path:
            total += config.antistrobe_buffer_frames
        elif path in chunk_frame_map:
            total += chunk_frame_map[path]
        elif path in image_frame_map:
            total += image_frame_map[path]
        elif path in norm_frame_map:
            total += norm_frame_map[path]
        else:
            # Truly unknown clip — probe as last resort
            try:
                info = probe(path)
                total += info.frame_count
            except Exception:
                pass
    return total


def _write_concat_file(sequence: list[str], concat_path: Path) -> None:
    """Write ffmpeg concat demuxer manifest.

    Uses newline="" to force Unix line endings on all platforms —
    ffmpeg's concat demuxer can choke on Windows CRLF.
    """
    with open(concat_path, "w", newline="\n") as f:
        for path in sequence:
            # Escape single quotes in paths
            safe_path = str(Path(path).resolve()).replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")


def _run_concat(concat_path: Path, output_path: Path, sequence_length: int = 0) -> None:
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

    timeout = max(600, 600 + sequence_length // 100)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-2000:]}")


def _luma_delta_pass(
    sequence: list[str],
    config: SplicerConfig,
    manifest: Manifest,
    luma_cache: dict[str, float],
) -> None:
    """Post-assembly safety pass: flag adjacent clips with high luma delta."""
    threshold = config.antistrobe_delta_threshold
    if threshold <= 0:
        return

    # Probe any paths not already in the shared cache (e.g. luma-normalized clips)
    uncached = list(set(p for p in sequence if p not in luma_cache))
    if uncached:
        _probe_luma_parallel(uncached, luma_cache, config.max_workers)

    print(f"  luma delta pass: checking {len(sequence) - 1} transitions")

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
