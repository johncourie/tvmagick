#!/usr/bin/env python3
"""Gradio GUI — same splicer pipeline as cli.py, browser-based interface."""

import io
import random
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import gradio as gr

from core.config import SplicerConfig
from core.platform import platform_check, PlatformError
from core.probe import probe, ProbeError
from core.normalize import normalize_video
from core.chunk import chunk_video
from core.assemble import assemble
from core.manifest import Manifest


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".gif",
}

RESOLUTION_CHOICES = [
    "HD 1920x1080",
    "NTSC 720x480 (CRT)",
    "PAL 720x576 (CRT)",
    "Custom",
]

X264_PRESETS = [
    "ultrafast", "superfast", "veryfast", "faster",
    "fast", "medium", "slow", "slower", "veryslow",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _LogStream:
    """Thread-safe stdout replacement that accumulates text for the log panel."""

    def __init__(self, echo=None):
        self._buf = io.StringIO()
        self._lock = threading.Lock()
        self._echo = echo

    def write(self, text):
        with self._lock:
            self._buf.write(text)
        if self._echo:
            self._echo.write(text)

    def flush(self):
        if self._echo:
            self._echo.flush()

    def getvalue(self):
        with self._lock:
            return self._buf.getvalue()


def _extract_paths(files):
    """Get plain file paths from a Gradio upload result."""
    if not files:
        return []
    out = []
    for f in files:
        if isinstance(f, str):
            out.append(f)
        elif isinstance(f, dict) and "path" in f:
            out.append(f["path"])
        elif hasattr(f, "name"):
            out.append(f.name)
        else:
            out.append(str(f))
    return out


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

def _build_config(
    resolution, custom_res, aspect,
    chunk_min, chunk_max, img_min, img_max, fps,
    antistrobe, buffer_frames, luma_strength, luma_threshold,
    seed, workers, preset,
):
    """Construct SplicerConfig from GUI widget values."""
    if resolution.startswith("NTSC"):
        config = SplicerConfig.ntsc_crt()
    elif resolution.startswith("PAL"):
        config = SplicerConfig.pal_crt()
    else:
        config = SplicerConfig()

    if resolution == "Custom" and custom_res:
        parts = custom_res.strip().lower().split("x")
        if len(parts) == 2:
            try:
                config.target_resolution = (int(parts[0]), int(parts[1]))
            except ValueError:
                pass

    config.aspect_mode = aspect
    config.chunk_frames_min = int(chunk_min)
    config.chunk_frames_max = int(chunk_max)
    config.image_frames_min = int(img_min)
    config.image_frames_max = int(img_max)
    config.target_fps = int(fps)
    config.antistrobe_enabled = bool(antistrobe)
    config.antistrobe_buffer_frames = int(buffer_frames)
    config.antistrobe_luma_strength = float(luma_strength)
    config.antistrobe_delta_threshold = int(luma_threshold)
    config.preset = preset
    config.max_workers = int(workers)

    if seed is not None:
        try:
            config.rng_seed = int(seed)
        except (ValueError, TypeError):
            pass

    return config


# ---------------------------------------------------------------------------
# Pipeline (mirrors cli.py main, writes progress to current stdout)
# ---------------------------------------------------------------------------

def _run_pipeline(video_paths, image_paths, config):
    """Execute the full splicer pipeline. Returns (output_path, manifest_path)."""
    try:
        platform_check()
    except PlatformError as e:
        raise RuntimeError(f"Platform check failed: {e}")

    if config.rng_seed is None:
        config.rng_seed = random.randint(0, 2**31 - 1)
    rng = random.Random(config.rng_seed)
    print(f"RNG seed: {config.rng_seed}")

    videos = [Path(p) for p in video_paths if Path(p).suffix.lower() in VIDEO_EXTENSIONS]
    images = [Path(p) for p in image_paths if Path(p).suffix.lower() in IMAGE_EXTENSIONS]
    print(f"Inputs: {len(videos)} video(s), {len(images)} image(s)")

    if not videos and not images:
        raise ValueError("No valid input files")

    # Persistent output dir (survives temp cleanup so Gradio can serve files)
    output_dir = Path(tempfile.mkdtemp(prefix="splicer_gui_"))
    config.output_dir = str(output_dir)

    with tempfile.TemporaryDirectory(prefix="splicer_work_") as tmpdir:
        work = Path(tmpdir)
        norm_dir = work / "normalized"
        chunk_dir = work / "chunks"
        norm_dir.mkdir()
        chunk_dir.mkdir()

        # --- Phase 1: Probe & Normalize ---
        print(f"\n--- Normalizing ({config.max_workers} workers) ---")

        def _norm(idx, vid):
            try:
                info = probe(vid)
                tag = " (VFR)" if info.is_vfr else ""
                out = norm_dir / f"norm_{idx:04d}.mp4"
                normalize_video(vid, out, config, probe_result=info)
                return idx, out, f"  [{idx+1}/{len(videos)}] {vid.name}{tag}"
            except (ProbeError, RuntimeError) as e:
                return idx, None, f"  [{idx+1}/{len(videos)}] {vid.name} SKIPPED: {e}"

        with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
            futs = {pool.submit(_norm, i, v): i for i, v in enumerate(videos)}
            rows = []
            for f in as_completed(futs):
                i, o, m = f.result()
                print(m)
                rows.append((i, o, m))

        rows.sort(key=lambda r: r[0])
        norm_vids = [o for _, o, _ in rows if o is not None]

        valid_imgs = []
        for i, img in enumerate(images):
            print(f"  [img {i+1}/{len(images)}] {img.name}")
            try:
                probe(img)
                valid_imgs.append(img)
            except ProbeError as e:
                print(f"    SKIPPED: {e}")

        if not norm_vids and not valid_imgs:
            raise ValueError("No inputs survived normalization")

        # --- Phase 2: Chunk ---
        print(f"\n--- Chunking ({config.max_workers} workers) ---")
        sub_seeds = [rng.randint(0, 2**31 - 1) for _ in norm_vids]

        def _chk(idx, path, sd):
            sub_rng = random.Random(sd)
            d = chunk_dir / f"v{idx:04d}"
            d.mkdir(exist_ok=True)
            cs = chunk_video(path, d, config, sub_rng, start_index=0)
            print(f"  [{idx+1}/{len(norm_vids)}] {path.name} -> {len(cs)} chunks")
            return idx, cs

        with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
            futs = {
                pool.submit(_chk, i, p, s): i
                for i, (p, s) in enumerate(zip(norm_vids, sub_seeds))
            }
            crs = []
            for f in as_completed(futs):
                crs.append(f.result())

        crs.sort(key=lambda r: r[0])
        all_chunks = []
        ci = 0
        for _, cs in crs:
            for c in cs:
                c.chunk_index = ci
                ci += 1
            all_chunks.extend(cs)

        print(f"Total chunks: {len(all_chunks)}")

        # --- Phase 3: Assemble ---
        print("\n--- Assembling ---")
        manifest = Manifest(rng_seed=config.rng_seed, config_snapshot=config.to_dict())
        for c in all_chunks:
            manifest.add_chunk(c)

        out_video = output_dir / "splicer_output.mp4"
        assemble(
            chunks=all_chunks,
            image_paths=[str(p) for p in valid_imgs],
            config=config,
            rng=rng,
            manifest=manifest,
            work_dir=work,
            output_path=out_video,
        )

        # --- Phase 4: Manifest ---
        out_manifest = output_dir / "splicer_manifest.json"
        manifest.save(out_manifest)

    print(f"\nOutput:   {out_video}")
    print(f"Manifest: {out_manifest}")
    print(f"Frames:   {manifest.actual_frame_count} (expected {manifest.expected_frame_count})")
    if manifest.luma_flags:
        print(f"Luma warnings: {len(manifest.luma_flags)}")
    print("Done.")

    return str(out_video), str(out_manifest)


# ---------------------------------------------------------------------------
# Gradio generator (streams log updates, returns files when finished)
# ---------------------------------------------------------------------------

def run_splicer(
    video_files, image_files,
    resolution, custom_res, aspect,
    chunk_min, chunk_max, img_min, img_max, fps,
    antistrobe, buffer_frames, luma_strength, luma_threshold,
    seed, workers, preset,
):
    """Generator yielding (log, preview_video, output_file, manifest_file)."""
    log = _LogStream(echo=sys.__stdout__)
    state = {"done": False, "error": False, "output": None, "manifest": None}

    config = _build_config(
        resolution, custom_res, aspect,
        chunk_min, chunk_max, img_min, img_max, fps,
        antistrobe, buffer_frames, luma_strength, luma_threshold,
        seed, workers, preset,
    )

    vid_paths = _extract_paths(video_files)
    img_paths = _extract_paths(image_files)

    def _work():
        old = sys.stdout
        sys.stdout = log
        try:
            o, m = _run_pipeline(vid_paths, img_paths, config)
            state["output"] = o
            state["manifest"] = m
        except Exception:
            traceback.print_exc(file=log)
            state["error"] = True
        finally:
            sys.stdout = old
            state["done"] = True

    t = threading.Thread(target=_work, daemon=True)
    t.start()

    while not state["done"]:
        time.sleep(0.4)
        yield log.getvalue(), None, None, None

    t.join()

    text = log.getvalue()
    if state["error"]:
        yield text, None, None, None
    else:
        yield text, state["output"], state["output"], state["manifest"]


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def build_ui():
    with gr.Blocks(title="Splicer") as app:
        gr.Markdown(
            "# Semantic Threshold Video Splicer\n"
            "Rapid-cut assembly for perceptual disruption — same pipeline as `cli.py`."
        )

        # --- Inputs ---
        with gr.Row():
            video_files = gr.File(
                label="Source Videos",
                file_count="multiple",
                file_types=list(VIDEO_EXTENSIONS),
            )
            image_files = gr.File(
                label="Interleave Images (optional)",
                file_count="multiple",
                file_types=list(IMAGE_EXTENSIONS),
            )

        # --- Settings: three columns ---
        with gr.Row():
            with gr.Column():
                gr.Markdown("### Resolution")
                resolution = gr.Dropdown(
                    choices=RESOLUTION_CHOICES,
                    value="HD 1920x1080",
                    label="Preset",
                )
                custom_res = gr.Textbox(
                    label="Custom WxH",
                    placeholder="1280x720",
                    visible=False,
                )
                aspect = gr.Dropdown(
                    choices=["letterbox", "crop", "stretch"],
                    value="letterbox",
                    label="Aspect mode",
                )

            with gr.Column():
                gr.Markdown("### Timing (frames)")
                chunk_min = gr.Slider(
                    minimum=1, maximum=15, step=1, value=3,
                    label="Chunk min (2=liminal, 3\u20135=threshold)",
                )
                chunk_max = gr.Slider(
                    minimum=1, maximum=30, step=1, value=5,
                    label="Chunk max (>7=narrative integration)",
                )
                img_min = gr.Slider(
                    minimum=1, maximum=15, step=1, value=3,
                    label="Image min frames",
                )
                img_max = gr.Slider(
                    minimum=1, maximum=30, step=1, value=8,
                    label="Image max frames",
                )
                fps = gr.Slider(
                    minimum=12, maximum=60, step=1, value=24,
                    label="Target FPS",
                )

            with gr.Column():
                gr.Markdown("### Anti-Strobe")
                antistrobe = gr.Checkbox(label="Enable anti-strobe", value=True)
                buffer_frames = gr.Slider(
                    minimum=0, maximum=5, step=1, value=1,
                    label="Buffer frames (gray between cuts)",
                )
                luma_strength = gr.Slider(
                    minimum=0.0, maximum=1.0, step=0.05, value=0.5,
                    label="Luma normalization strength",
                )
                luma_threshold = gr.Slider(
                    minimum=0, maximum=255, step=1, value=80,
                    label="Luma delta threshold",
                )

        # --- Other settings ---
        with gr.Row():
            seed = gr.Number(
                label="RNG Seed (blank = random)",
                value=None,
                precision=0,
            )
            workers = gr.Slider(
                minimum=1, maximum=16, step=1, value=4,
                label="Workers",
            )
            preset = gr.Dropdown(
                choices=X264_PRESETS,
                value="fast",
                label="x264 Preset",
            )

        run_btn = gr.Button("Run Splicer", variant="primary", size="lg")

        # --- Output ---
        log_box = gr.Textbox(
            label="Pipeline Log",
            lines=20,
            max_lines=50,
            interactive=False,
        )

        with gr.Row():
            preview = gr.Video(label="Output Preview")
            with gr.Column():
                output_file = gr.File(label="Download Output")
                manifest_file = gr.File(label="Download Manifest")

        # --- Events ---
        resolution.change(
            fn=lambda r: gr.Textbox(visible=(r == "Custom")),
            inputs=resolution,
            outputs=custom_res,
        )

        antistrobe.change(
            fn=lambda on: (
                gr.Slider(interactive=on),
                gr.Slider(interactive=on),
                gr.Slider(interactive=on),
            ),
            inputs=antistrobe,
            outputs=[buffer_frames, luma_strength, luma_threshold],
        )

        run_btn.click(
            fn=run_splicer,
            inputs=[
                video_files, image_files,
                resolution, custom_res, aspect,
                chunk_min, chunk_max, img_min, img_max, fps,
                antistrobe, buffer_frames, luma_strength, luma_threshold,
                seed, workers, preset,
            ],
            outputs=[log_box, preview, output_file, manifest_file],
        )

    return app


if __name__ == "__main__":
    build_ui().launch()
