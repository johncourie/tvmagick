"""Input normalization — conform all sources to uniform resolution, fps, pix_fmt, colorspace."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import SplicerConfig
from .probe import ProbeResult, probe


def normalize_video(
    input_path: str | Path,
    output_path: str | Path,
    config: SplicerConfig,
    probe_result: ProbeResult | None = None,
) -> Path:
    """Normalize a video file to target specs. Returns output path."""
    input_path = Path(input_path)
    output_path = Path(output_path)

    if probe_result is None:
        probe_result = probe(input_path)

    if probe_result.is_vfr:
        print(f"  warning: {input_path.name} is variable frame rate, forcing CFR")

    vf = _build_video_filter(config)
    cmd = _build_encode_cmd(
        input_args=["-i", str(input_path)],
        vf=vf,
        config=config,
        output_path=output_path,
    )

    _run_ffmpeg(cmd, f"normalizing {input_path.name}")
    return output_path


def normalize_image(
    input_path: str | Path,
    output_path: str | Path,
    config: SplicerConfig,
    duration_frames: int | None = None,
) -> Path:
    """Convert a static image to a normalized video segment. Returns output path."""
    input_path = Path(input_path)
    output_path = Path(output_path)

    if duration_frames is None:
        duration_frames = config.image_frames_max

    duration_sec = duration_frames / config.target_fps

    vf = _build_video_filter(config)
    cmd = _build_encode_cmd(
        input_args=["-loop", "1", "-i", str(input_path)],
        vf=vf,
        config=config,
        output_path=output_path,
        extra_output_args=["-t", str(duration_sec)],
    )

    _run_ffmpeg(cmd, f"normalizing image {input_path.name}")
    return output_path


def _build_video_filter(config: SplicerConfig) -> str:
    """Build the -vf filter string for normalization."""
    w, h = config.width, config.height

    if config.aspect_mode == "letterbox":
        scale = f"scale={w}:{h}:force_original_aspect_ratio=decrease"
        pad = f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
        vf_parts = [scale, pad]
    elif config.aspect_mode == "crop":
        scale = f"scale={w}:{h}:force_original_aspect_ratio=increase"
        crop = f"crop={w}:{h}"
        vf_parts = [scale, crop]
    else:  # stretch
        vf_parts = [f"scale={w}:{h}"]

    vf_parts.append(f"fps={config.target_fps}")
    vf_parts.append(f"format={config.target_pix_fmt}")

    return ",".join(vf_parts)


def _build_encode_cmd(
    input_args: list[str],
    vf: str,
    config: SplicerConfig,
    output_path: Path,
    extra_output_args: list[str] | None = None,
) -> list[str]:
    """Build a full ffmpeg encode command."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found on PATH")

    cmd = [ffmpeg, "-y"]
    cmd.extend(input_args)
    cmd.extend(["-vf", vf])
    cmd.extend([
        "-colorspace", config.target_colorspace,
        "-color_primaries", config.color_primaries,
        "-color_trc", config.color_trc,
        "-c:v", config.codec,
        "-preset", config.preset,
        "-g", "1",
        "-bf", "0",
        "-an",
    ])
    if extra_output_args:
        cmd.extend(extra_output_args)
    cmd.append(str(output_path))

    return cmd


def _run_ffmpeg(cmd: list[str], description: str = "") -> subprocess.CompletedProcess:
    """Run an ffmpeg command, raising on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed ({description}):\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stderr: {result.stderr[-2000:]}"
        )
    return result
