"""CLI entry point — argparse interface wiring the full splicer pipeline."""

import argparse
import random
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core.config import SplicerConfig
from core.platform import platform_check, PlatformError
from core.probe import probe, ProbeError
from core.normalize import normalize_video, normalize_image
from core.chunk import chunk_video
from core.assemble import assemble
from core.manifest import Manifest
from core.prep import grain_video, greyscale_video, collect_videos


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg", ".ts", ".gif"}


def main() -> None:
    args = parse_args()
    config = build_config(args)

    # Verify platform and tool availability
    try:
        platform_check()
    except PlatformError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    # Prep mode — preprocess and exit
    if args.prep:
        run_prep(args, config)
        return

    # Resolve RNG seed
    if config.rng_seed is None:
        config.rng_seed = random.randint(0, 2**31 - 1)
    rng = random.Random(config.rng_seed)
    print(f"RNG seed: {config.rng_seed}")

    # Collect input files
    input_paths = collect_inputs(args.inputs)
    if not input_paths:
        print("error: no valid input files found", file=sys.stderr)
        sys.exit(1)

    videos = [p for p in input_paths if p.suffix.lower() in VIDEO_EXTENSIONS]
    images = [p for p in input_paths if p.suffix.lower() in IMAGE_EXTENSIONS]
    print(f"Inputs: {len(videos)} video(s), {len(images)} image(s)")

    # Setup output
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="splicer_") as tmpdir:
        work_dir = Path(tmpdir)
        norm_dir = work_dir / "normalized"
        chunk_dir = work_dir / "chunks"
        norm_dir.mkdir()
        chunk_dir.mkdir()

        # --- Phase 1: Probe & Normalize ---
        print(f"\n--- Probing and normalizing inputs ({config.max_workers} workers) ---")
        normalized_videos: list[Path] = []

        def _normalize_one(idx: int, vid: Path) -> tuple[int, Path | None, str]:
            """Probe and normalize a single video. Returns (index, output_path, status_msg)."""
            try:
                info = probe(vid)
                vfr_note = f" (VFR->CFR {config.target_fps}fps)" if info.is_vfr else ""
                out = norm_dir / f"norm_{idx:04d}.mp4"
                normalize_video(vid, out, config, probe_result=info)
                return idx, out, f"  [{idx+1}/{len(videos)}] {vid.name}{vfr_note}"
            except (ProbeError, RuntimeError) as e:
                return idx, None, f"  [{idx+1}/{len(videos)}] {vid.name} SKIPPED: {e}"

        with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
            futures = {pool.submit(_normalize_one, i, v): i for i, v in enumerate(videos)}
            results: list[tuple[int, Path | None, str]] = []
            for future in as_completed(futures):
                idx, out, msg = future.result()
                print(msg)
                results.append((idx, out, msg))

        # Sort by original index for deterministic ordering
        results.sort(key=lambda r: r[0])
        normalized_videos = [out for _, out, _ in results if out is not None]

        for i, img in enumerate(images):
            print(f"  [img {i+1}/{len(images)}] {img.name}")
            # Images get normalized during assembly, just validate here
            try:
                probe(img)
            except ProbeError as e:
                print(f"    SKIPPED: {e}")
                images = [p for p in images if p != img]

        if not normalized_videos and not images:
            print("error: no inputs survived normalization", file=sys.stderr)
            sys.exit(1)

        # --- Phase 2: Chunk ---
        print(f"\n--- Chunking normalized videos ({config.max_workers} workers) ---")

        # Pre-generate deterministic per-video sub-RNGs sequentially
        sub_seeds = [rng.randint(0, 2**31 - 1) for _ in normalized_videos]

        def _chunk_one(idx: int, norm_path: Path, seed: int) -> tuple[int, list]:
            """Chunk a single normalized video with its own sub-RNG."""
            sub_rng = random.Random(seed)
            # Each video gets its own subdir to avoid filename collisions
            vid_chunk_dir = chunk_dir / f"v{idx:04d}"
            vid_chunk_dir.mkdir(exist_ok=True)
            chunks = chunk_video(norm_path, vid_chunk_dir, config, sub_rng, start_index=0)
            print(f"  [{idx+1}/{len(normalized_videos)}] {norm_path.name} -> {len(chunks)} chunks")
            return idx, chunks

        with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
            futures = {
                pool.submit(_chunk_one, i, p, s): i
                for i, (p, s) in enumerate(zip(normalized_videos, sub_seeds))
            }
            chunk_results: list[tuple[int, list]] = []
            for future in as_completed(futures):
                chunk_results.append(future.result())

        # Reassemble in original order and re-index chunks
        chunk_results.sort(key=lambda r: r[0])
        all_chunks = []
        chunk_idx = 0
        for _, chunks in chunk_results:
            for c in chunks:
                c.chunk_index = chunk_idx
                chunk_idx += 1
            all_chunks.extend(chunks)

        print(f"Total chunks: {len(all_chunks)}")

        # --- Phase 3: Assemble ---
        print("\n--- Assembling final output ---")
        manifest = Manifest(
            rng_seed=config.rng_seed,
            config_snapshot=config.to_dict(),
        )

        # Record chunks in manifest
        for c in all_chunks:
            manifest.add_chunk(c)

        output_path = output_dir / "splicer_output.mp4"
        assemble(
            chunks=all_chunks,
            image_paths=[str(p) for p in images],
            config=config,
            rng=rng,
            manifest=manifest,
            work_dir=work_dir,
            output_path=output_path,
        )

        # --- Phase 4: Write manifest ---
        manifest_path = output_dir / "splicer_manifest.json"
        manifest.save(manifest_path)
        print(f"\nManifest: {manifest_path}")

    print(f"Output:   {output_path}")
    print(f"Frames:   {manifest.actual_frame_count} (expected {manifest.expected_frame_count})")
    if manifest.luma_flags:
        print(f"Luma warnings: {len(manifest.luma_flags)}")
    print("Done.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="splicer",
        description="Semantic threshold video splicer — rapid-cut assembly for perceptual disruption",
    )

    p.add_argument(
        "inputs", nargs="+",
        help="Input video/image files or directories containing them",
    )
    p.add_argument("-o", "--output-dir", default="output", help="Output directory (default: output)")
    p.add_argument("--seed", type=int, default=None, help="RNG seed for reproducibility")

    # Resolution
    res = p.add_mutually_exclusive_group()
    res.add_argument("--hd", action="store_true", help="1920x1080 output (default)")
    res.add_argument("--ntsc", action="store_true", help="720x480 NTSC CRT output")
    res.add_argument("--pal", action="store_true", help="720x576 PAL CRT output")
    res.add_argument("--resolution", type=str, default=None, help="Custom WxH (e.g. 1280x720)")

    # Aspect
    p.add_argument("--aspect", choices=["letterbox", "crop", "stretch"], default="letterbox")

    # Timing
    p.add_argument("--chunk-min", type=int, default=None, help="Min chunk frames (default: 3)")
    p.add_argument("--chunk-max", type=int, default=None, help="Max chunk frames (default: 5)")
    p.add_argument("--fps", type=int, default=None, help="Target framerate (default: 24)")

    # Anti-strobe
    p.add_argument("--no-antistrobe", action="store_true", help="Disable all anti-strobe processing")
    p.add_argument("--buffer-frames", type=int, default=None, help="Gray buffer frames between cuts (default: 1)")
    p.add_argument("--luma-strength", type=float, default=None, help="Luma normalization strength 0.0-1.0 (default: 0.5)")
    p.add_argument("--luma-threshold", type=int, default=None, help="Luma delta warning threshold 0-255 (default: 80)")

    # Codec
    p.add_argument("--preset", default=None, help="x264 preset (default: fast)")

    # Parallelism
    p.add_argument("--workers", type=int, default=None, help="Thread pool size for parallel operations (default: 4)")

    # Prep mode
    p.add_argument("--prep", action="store_true", help="Preprocessing mode — grain/greyscale sources, then exit (no splicer pipeline)")
    p.add_argument("--grain", action="store_true", help="(prep) Split long videos into segments")
    p.add_argument("--greyscale", action="store_true", help="(prep) Re-encode videos to greyscale")
    p.add_argument("--grain-duration", type=int, default=None, help="(prep) Grain segment length in seconds (default: 60)")

    return p.parse_args()


def build_config(args: argparse.Namespace) -> SplicerConfig:
    """Build SplicerConfig from parsed CLI args."""
    if args.ntsc:
        config = SplicerConfig.ntsc_crt()
    elif args.pal:
        config = SplicerConfig.pal_crt()
    else:
        config = SplicerConfig()

    if args.resolution:
        parts = args.resolution.lower().split("x")
        if len(parts) == 2:
            config.target_resolution = (int(parts[0]), int(parts[1]))

    config.output_dir = args.output_dir
    config.aspect_mode = args.aspect

    if args.seed is not None:
        config.rng_seed = args.seed
    if args.chunk_min is not None:
        config.chunk_frames_min = args.chunk_min
    if args.chunk_max is not None:
        config.chunk_frames_max = args.chunk_max
    if args.fps is not None:
        config.target_fps = args.fps
    if args.no_antistrobe:
        config.antistrobe_enabled = False
    if args.buffer_frames is not None:
        config.antistrobe_buffer_frames = args.buffer_frames
    if args.luma_strength is not None:
        config.antistrobe_luma_strength = args.luma_strength
    if args.luma_threshold is not None:
        config.antistrobe_delta_threshold = args.luma_threshold
    if args.preset is not None:
        config.preset = args.preset
    if args.grain_duration is not None:
        config.grain_duration = args.grain_duration
    if args.workers is not None:
        config.max_workers = args.workers

    return config


def run_prep(args: argparse.Namespace, config: SplicerConfig) -> None:
    """Run preprocessing pipeline: grain and/or greyscale, then exit."""
    if not args.grain and not args.greyscale:
        print("error: --prep requires at least one of --grain or --greyscale", file=sys.stderr)
        sys.exit(1)

    videos = collect_videos(args.inputs)
    if not videos:
        print("error: no video files found in inputs", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Prep mode: {len(videos)} video(s)")

    working_files = videos

    if args.grain:
        print(f"\n--- Grain: splitting into ~{config.grain_duration}s segments ---")
        grain_dir = output_dir / "_grain_tmp" if args.greyscale else output_dir
        grain_dir.mkdir(parents=True, exist_ok=True)

        grained: list[Path] = []
        for vid in working_files:
            segments = grain_video(vid, grain_dir, config)
            grained.extend(segments)

        print(f"Grain complete: {len(grained)} segment(s)")
        working_files = grained

    if args.greyscale:
        print("\n--- Greyscale ---")
        grey_dir = output_dir
        grey_dir.mkdir(parents=True, exist_ok=True)

        greyed: list[Path] = []
        for vid in working_files:
            out = greyscale_video(vid, grey_dir, config)
            greyed.append(out)

        print(f"Greyscale complete: {len(greyed)} file(s)")

        # Clean up intermediate grain files if both modes ran
        if args.grain:
            grain_dir = output_dir / "_grain_tmp"
            if grain_dir.exists():
                shutil.rmtree(grain_dir)

    print("\nPrep done.")
    print(f"Output: {output_dir}")


def collect_inputs(paths: list[str]) -> list[Path]:
    """Expand directories and filter to supported file types."""
    valid_ext = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
    result: list[Path] = []

    for p_str in paths:
        p = Path(p_str)
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file() and child.suffix.lower() in valid_ext:
                    result.append(child)
        elif p.is_file() and p.suffix.lower() in valid_ext:
            result.append(p)
        else:
            print(f"  skipping: {p} (not a supported file or directory)")

    return result


if __name__ == "__main__":
    main()
