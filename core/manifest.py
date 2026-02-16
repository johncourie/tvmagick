"""Reproducible build manifest — JSON log of every decision in an assembly."""

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .chunk import ChunkInfo


@dataclass
class ImageEntry:
    source_file: str
    position: int       # index in final sequence
    duration_frames: int


@dataclass
class LumaFlag:
    position: int       # index in final sequence
    delta: float
    threshold: float


@dataclass
class Manifest:
    rng_seed: int
    config_snapshot: dict
    chunks: list[dict] = field(default_factory=list)
    image_insertions: list[dict] = field(default_factory=list)
    luma_flags: list[dict] = field(default_factory=list)
    sequence_order: list[str] = field(default_factory=list)  # ordered paths
    expected_frame_count: int = 0
    actual_frame_count: int = 0
    output_checksum: str = ""
    output_path: str = ""

    def add_chunk(self, chunk: ChunkInfo) -> None:
        self.chunks.append({
            "source_file": chunk.source_file,
            "start_frame": chunk.start_frame,
            "frame_count": chunk.frame_count,
            "chunk_path": chunk.chunk_path,
            "chunk_index": chunk.chunk_index,
        })

    def add_image(self, entry: ImageEntry) -> None:
        self.image_insertions.append(asdict(entry))

    def add_luma_flag(self, flag: LumaFlag) -> None:
        self.luma_flags.append(asdict(flag))

    def set_output(self, path: str | Path, frame_count: int) -> None:
        self.output_path = str(path)
        self.actual_frame_count = frame_count
        self.output_checksum = _checksum_file(path)

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Manifest":
        with open(path) as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _checksum_file(path: str | Path, algorithm: str = "sha256") -> str:
    """Compute file checksum."""
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            h.update(block)
    return f"{algorithm}:{h.hexdigest()}"
