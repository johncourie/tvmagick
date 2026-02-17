"""Platform validation — verify tools and report environment at startup."""

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional


@dataclass
class PlatformInfo:
    os_name: str           # e.g. "Darwin", "Linux", "Windows"
    os_version: str        # e.g. "14.5", "10.0.22631"
    python_version: str    # e.g. "3.12.1"
    ffmpeg_path: str
    ffmpeg_version: str    # e.g. "6.1.1"
    ffprobe_path: str
    ffprobe_version: str


class PlatformError(Exception):
    pass


def platform_check(verbose: bool = True) -> PlatformInfo:
    """Validate ffmpeg/ffprobe availability and report environment.

    Raises PlatformError if required tools are missing.
    """
    os_name = platform.system()
    os_version = platform.release()
    python_version = platform.python_version()

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        hint = _install_hint(os_name, "ffmpeg")
        raise PlatformError(f"ffmpeg not found on PATH. {hint}")

    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path is None:
        hint = _install_hint(os_name, "ffprobe")
        raise PlatformError(f"ffprobe not found on PATH. {hint}")

    ffmpeg_version = _get_tool_version(ffmpeg_path)
    ffprobe_version = _get_tool_version(ffprobe_path)

    info = PlatformInfo(
        os_name=os_name,
        os_version=os_version,
        python_version=python_version,
        ffmpeg_path=ffmpeg_path,
        ffmpeg_version=ffmpeg_version,
        ffprobe_path=ffprobe_path,
        ffprobe_version=ffprobe_version,
    )

    if verbose:
        print(f"Platform: {os_name} {os_version}, Python {python_version}")
        print(f"ffmpeg:   {ffmpeg_version} ({ffmpeg_path})")
        print(f"ffprobe:  {ffprobe_version} ({ffprobe_path})")

    return info


def _get_tool_version(tool_path: str) -> str:
    """Extract version string from ffmpeg/ffprobe -version output."""
    try:
        result = subprocess.run(
            [tool_path, "-version"],
            capture_output=True, text=True, timeout=10,
        )
        # First line is like "ffmpeg version 6.1.1 Copyright ..."
        first_line = result.stdout.split("\n", 1)[0]
        parts = first_line.split()
        if len(parts) >= 3:
            return parts[2]
        return first_line
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"


def _install_hint(os_name: str, tool: str) -> str:
    """Return a platform-appropriate install hint."""
    if os_name == "Darwin":
        return f"Install via: brew install {tool}"
    elif os_name == "Linux":
        return f"Install via your package manager (e.g. apt install {tool})"
    elif os_name == "Windows":
        return f"Download from https://ffmpeg.org/download.html and add to PATH"
    return f"Ensure {tool} is installed and on your PATH"
