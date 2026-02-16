"""All pipeline parameters as a single dataclass."""

from dataclasses import dataclass, field, asdict
from typing import Optional, Tuple, Literal
import json


@dataclass
class SplicerConfig:
    # --- resolution / format ---
    target_resolution: Tuple[int, int] = (1920, 1080)
    target_fps: int = 24
    target_pix_fmt: str = "yuv420p"
    target_colorspace: str = "bt709"  # "bt709" | "smpte170m"
    aspect_mode: Literal["letterbox", "crop", "stretch"] = "letterbox"

    # --- codec ---
    codec: str = "libx264"
    preset: str = "fast"

    # --- chunk timing (in frames) ---
    chunk_frames_min: int = 3
    chunk_frames_max: int = 5

    # --- image timing (in frames) ---
    image_frames_min: int = 3
    image_frames_max: int = 8

    # --- anti-strobe ---
    antistrobe_enabled: bool = True
    antistrobe_buffer_frames: int = 1       # 0 = off, 1+ = gray frames between cuts
    antistrobe_luma_strength: float = 0.5   # 0.0 = off, 1.0 = full normalization
    antistrobe_delta_threshold: int = 80    # luma delta flag threshold (0-255)

    # --- reproducibility ---
    rng_seed: Optional[int] = None          # None = random, int = reproducible

    # --- paths ---
    output_dir: str = "output"
    temp_dir: str = ""  # empty = auto temp dir

    # --- colorspace helpers ---
    @property
    def color_primaries(self) -> str:
        return "smpte170m" if self.target_colorspace == "smpte170m" else "bt709"

    @property
    def color_trc(self) -> str:
        return "smpte170m" if self.target_colorspace == "smpte170m" else "bt709"

    @property
    def width(self) -> int:
        return self.target_resolution[0]

    @property
    def height(self) -> int:
        return self.target_resolution[1]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "SplicerConfig":
        if "target_resolution" in d and isinstance(d["target_resolution"], list):
            d["target_resolution"] = tuple(d["target_resolution"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @classmethod
    def ntsc_crt(cls, **overrides) -> "SplicerConfig":
        """Preset for NTSC CRT output."""
        defaults = dict(
            target_resolution=(720, 480),
            target_colorspace="smpte170m",
        )
        defaults.update(overrides)
        return cls(**defaults)

    @classmethod
    def pal_crt(cls, **overrides) -> "SplicerConfig":
        """Preset for PAL CRT output."""
        defaults = dict(
            target_resolution=(720, 576),
            target_colorspace="smpte170m",
        )
        defaults.update(overrides)
        return cls(**defaults)
