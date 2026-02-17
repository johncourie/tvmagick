"""Benchmark — generate a synthetic clip and time each pipeline stage for calibration."""

import json
import platform
import random
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import SplicerConfig
from .platform import platform_check

CALIBRATION_FILENAME = ".splicer_calibration.json"

# Synthetic test clip: 3 segments of different luma character
_SEGMENTS = [
    ("bright", "color=c=white:s={w}x{h}:r={fps}:d={d}"),
    ("dark", "color=c=0x333333:s={w}x{h}:r={fps}:d={d}"),
    ("motion", "mandelbrot=s={w}x{h}:r={fps}"),
]
_SEGMENT_DURATION = 3.34  # ~80 frames at 24fps, 3 segments -> ~240 frames
_TEST_CLIP_FRAMES = 240
_TEST_CLIP_DURATION = 10.0


@dataclass
class CalibrationData:
    splicer_calibration_version: int = 1
    timestamp: str = ""
    platform_os: str = ""
    ffmpeg_version: str = ""
    resolution: tuple[int, int] = (1920, 1080)
    fps: int = 24
    preset: str = "fast"
    codec: str = "libx264"
    normalize_fps: float = 0.0
    chunk_fps: float = 0.0
    assemble_concat_fps: float = 0.0
    luma_probe_fps: float = 0.0
    luma_encode_fps: float = 0.0
    bytes_per_frame: float = 0.0
    test_clip_frames: int = _TEST_CLIP_FRAMES
    test_clip_duration: float = _TEST_CLIP_DURATION
    timings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["resolution"] = list(self.resolution)
        return d

    def save(self, path: Path) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        print(f"Calibration saved: {path}")

    @classmethod
    def from_dict(cls, d: dict) -> "CalibrationData":
        if "resolution" in d and isinstance(d["resolution"], list):
            d["resolution"] = tuple(d["resolution"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def run_benchmark(config: SplicerConfig, verbose: bool = True) -> CalibrationData:
    """Run the full benchmark pipeline on a synthetic clip.

    Generates lavfi test content, then times probe, normalize, chunk, and assemble
    stages. Returns CalibrationData with per-frame rates for each stage.
    """
    pinfo = platform_check(verbose=False)

    cal = CalibrationData(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        platform_os=pinfo.os_name,
        ffmpeg_version=pinfo.ffmpeg_version,
        resolution=config.target_resolution,
        fps=config.target_fps,
        preset=config.preset,
        codec=config.codec,
    )

    with tempfile.TemporaryDirectory(prefix="splicer_bench_") as tmpdir:
        work = Path(tmpdir)

        # --- Generate synthetic clip ---
        if verbose:
            print("\n--- Generating synthetic test clip ---")
        synthetic = _generate_synthetic_clip(config, work, verbose)
        if verbose:
            print(f"  synthetic clip: {synthetic.name}")

        # --- Stage 1: Probe ---
        if verbose:
            print("\n--- Benchmark: probe ---")
        from .probe import probe
        t0 = time.perf_counter()
        info = probe(synthetic)
        t_probe = time.perf_counter() - t0
        actual_frames = info.frame_count
        if verbose:
            print(f"  probe: {t_probe:.3f}s ({actual_frames} frames detected)")

        # --- Stage 2: Normalize ---
        if verbose:
            print("\n--- Benchmark: normalize ---")
        from .normalize import normalize_video
        norm_out = work / "norm_bench.mp4"
        t0 = time.perf_counter()
        normalize_video(synthetic, norm_out, config, probe_result=info)
        t_normalize = time.perf_counter() - t0
        norm_fps = actual_frames / t_normalize if t_normalize > 0 else 0
        cal.normalize_fps = round(norm_fps, 1)
        if verbose:
            print(f"  normalize: {t_normalize:.3f}s ({norm_fps:.1f} fps)")

        # --- Stage 3: Chunk ---
        if verbose:
            print("\n--- Benchmark: chunk ---")
        from .chunk import chunk_video
        chunk_dir = work / "chunks"
        chunk_dir.mkdir()
        rng = random.Random(42)
        t0 = time.perf_counter()
        chunks = chunk_video(norm_out, chunk_dir, config, rng, start_index=0)
        t_chunk = time.perf_counter() - t0
        chunk_fps = actual_frames / t_chunk if t_chunk > 0 else 0
        cal.chunk_fps = round(chunk_fps, 1)
        if verbose:
            print(f"  chunk: {t_chunk:.3f}s ({len(chunks)} chunks, {chunk_fps:.1f} fps)")

        # --- Stage 4: Assemble ---
        if verbose:
            print("\n--- Benchmark: assemble ---")
        from .assemble import assemble
        from .manifest import Manifest

        manifest = Manifest(rng_seed=42, config_snapshot=config.to_dict())
        for c in chunks:
            manifest.add_chunk(c)

        rng2 = random.Random(42)
        output = work / "bench_output.mp4"

        t0 = time.perf_counter()
        assemble(
            chunks=chunks,
            image_paths=[],
            config=config,
            rng=rng2,
            manifest=manifest,
            work_dir=work,
            output_path=output,
        )
        t_assemble = time.perf_counter() - t0

        # Derive sub-stage rates from assemble timing
        # assemble internally does: luma probe + luma encode + concat
        # We time these individually with small extra calls
        if verbose:
            print(f"  assemble: {t_assemble:.3f}s")

        # Standalone luma probe timing
        from .probe import probe_mean_luma
        sample_chunk = chunks[0].chunk_path if chunks else str(norm_out)
        t0 = time.perf_counter()
        try:
            probe_mean_luma(sample_chunk)
        except Exception:
            pass
        t_luma_probe_single = time.perf_counter() - t0
        sample_info = probe(sample_chunk)
        sample_frames = sample_info.frame_count
        luma_probe_fps = sample_frames / t_luma_probe_single if t_luma_probe_single > 0 else 450.0
        cal.luma_probe_fps = round(luma_probe_fps, 1)

        # Standalone luma encode timing (eq filter on single chunk)
        ffmpeg = shutil.which("ffmpeg")
        luma_enc_out = work / "luma_enc_bench.mp4"
        t0 = time.perf_counter()
        cmd = [
            ffmpeg, "-y",
            "-i", sample_chunk,
            "-vf", "eq=brightness=0.05",
            "-c:v", config.codec,
            "-preset", config.preset,
            "-g", "1", "-bf", "0", "-an",
            str(luma_enc_out),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        t_luma_encode_single = time.perf_counter() - t0
        luma_encode_fps = sample_frames / t_luma_encode_single if t_luma_encode_single > 0 else 280.0
        cal.luma_encode_fps = round(luma_encode_fps, 1)

        # Concat fps — from assemble minus luma work, rough estimate
        total_output_frames = manifest.actual_frame_count or actual_frames
        assemble_concat_fps = total_output_frames / max(t_assemble * 0.1, 0.001)
        cal.assemble_concat_fps = round(assemble_concat_fps, 1)

        if verbose:
            print(f"  luma probe: {luma_probe_fps:.1f} fps")
            print(f"  luma encode: {luma_encode_fps:.1f} fps")
            print(f"  concat: {assemble_concat_fps:.1f} fps")

        # Bytes per frame from output
        if output.exists():
            file_size = output.stat().st_size
            out_frames = manifest.actual_frame_count or total_output_frames
            cal.bytes_per_frame = round(file_size / out_frames, 1) if out_frames > 0 else 18500.0
        else:
            cal.bytes_per_frame = 18500.0

        t_total = t_probe + t_normalize + t_chunk + t_assemble
        cal.timings = {
            "probe": round(t_probe, 3),
            "normalize": round(t_normalize, 3),
            "chunk": round(t_chunk, 3),
            "assemble": round(t_assemble, 3),
            "total": round(t_total, 3),
        }
        cal.test_clip_frames = actual_frames
        cal.test_clip_duration = round(actual_frames / config.target_fps, 2)

        if verbose:
            print(f"\n--- Benchmark complete: {t_total:.2f}s total ---")

    return cal


def _generate_synthetic_clip(
    config: SplicerConfig, work_dir: Path, verbose: bool = True
) -> Path:
    """Generate a ~10s synthetic clip with varied luma segments via lavfi."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found on PATH")

    w, h = config.width, config.height
    fps = config.target_fps
    seg_duration = _SEGMENT_DURATION

    segment_paths = []
    for name, template in _SEGMENTS:
        seg_path = work_dir / f"bench_seg_{name}.mp4"

        if "mandelbrot" in template:
            lavfi = template.format(w=w, h=h, fps=fps)
            input_args = ["-f", "lavfi", "-i", lavfi, "-t", str(seg_duration)]
        else:
            lavfi = template.format(w=w, h=h, fps=fps, d=seg_duration)
            input_args = ["-f", "lavfi", "-i", lavfi]

        cmd = [
            ffmpeg, "-y",
            *input_args,
            "-vf", f"format={config.target_pix_fmt}",
            "-colorspace", config.target_colorspace,
            "-color_primaries", config.color_primaries,
            "-color_trc", config.color_trc,
            "-c:v", config.codec,
            "-preset", config.preset,
            "-g", "1", "-bf", "0", "-an",
            str(seg_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to generate {name} segment: {result.stderr[-500:]}")
        segment_paths.append(seg_path)
        if verbose:
            print(f"  segment: {name}")

    # Concat segments
    concat_list = work_dir / "bench_concat.txt"
    with open(concat_list, "w", newline="\n") as f:
        for p in segment_paths:
            safe = str(p.resolve()).replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    output = work_dir / "bench_synthetic.mp4"
    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to concat benchmark segments: {result.stderr[-500:]}")

    return output
