# Semantic Threshold Video Splicer

## Intent
Art project in the lineage of TOPY television magick and cut-up technique. Rapid-cut video as a tool for perceptual disruption — each shot lands at the threshold of semantic recognition but below narrative integration, producing a stream of recognized-but-unlinked contexts. The viewer experiences meaning without story.

This is not safety-critical video production. The anti-strobe and normalization systems exist as **tunable artistic parameters**, not just protective defaults. Sometimes the strobe is the point. The tool should make it easy to move between perceptual comfort and deliberate disruption.

Final output is displayed on an array of CRT televisions (Panasonic Ranger and similar vintage sets). The CRT chain is handled separately — this tool produces standard digital video files — but the display context informs resolution and aesthetic decisions.

## Core Perceptual Parameters
- **Target chunk duration**: 3–5 frames at 24fps (100–167ms) — semantic categorization threshold
- **Floor**: 2 frames (83ms) — liminal/gist-only recognition, audience perceptual split
- **Ceiling**: 7 frames (292ms) — begins allowing narrative integration, avoid unless intentional
- These are content frames; anti-strobe buffers (if enabled) are additional
- Parameters should be exposed as ranges with random variation per chunk (seeded RNG)

## Architecture
```
splicer/
├── CLAUDE.md
├── legacy_v0.1.sh      # original bash script for reference
├── cli.py               # argparse entry point
├── gui.py               # Gradio interface (future, same core functions)
├── core/
│   ├── __init__.py
│   ├── config.py        # all parameters as a dataclass, consumed by CLI and future GUI
│   ├── probe.py         # ffprobe JSON wrapper, input validation, rejection logging
│   ├── normalize.py     # resolution, pix_fmt, fps, colorspace, luma normalization
│   ├── chunk.py         # frame-accurate trim, chunk extraction, metadata tagging
│   ├── assemble.py      # concat, image interleaving, anti-strobe insertion
│   └── manifest.py      # reproducible build log (JSON)
├── test_assets/         # sample videos and images for pipeline validation
└── output/              # final assembled output
```

## Environment
- **Platform**: macOS (primary development and runtime)
- **Python**: 3.10+
- **ffmpeg/ffprobe**: System install via Homebrew (`brew install ffmpeg`), must be on PATH
- **No pip dependencies for core pipeline** — subprocess + json + random + dataclasses
- Optional: numpy for luma analysis if ffmpeg filter approach is insufficient
- Future: gradio for GUI
- ffmpeg is called via `subprocess`, not as a library. This is intentional — keeps Python as orchestration, ffmpeg as the workhorse, avoids compiled binding issues on Mac.

## Origin
Porting from a v0.1 bash+ffmpeg script (`legacy_v0.1.sh`). Known issues in v0.1:
- Strobing on high-contrast cuts between heterogeneous sources
- Resolution/format mismatches breaking concat
- No luminance normalization
- Imprecise temporal cuts (seconds not frames)
- No reproducibility (unseeded randomization)
- No image handling normalization

## Platform Support
- macOS, Linux, Windows 10/11
- All paths via pathlib. No hardcoded separators.
- ffmpeg/ffprobe located via shutil.which() — user is responsible for having them on PATH
- No shell=True in subprocess calls
- No /tmp literals — use tempfile module

## Technical Requirements

### Input Normalization (normalize.py)
All inputs must be conformed before chunking:
- **Resolution**: Force to single target (default 1920x1080). Configurable aspect ratio handling: letterbox (default), center-crop, or stretch
- **CRT-aware option**: Optional output resolution of 720x480 (NTSC) or 720x576 (PAL) for direct CRT-chain input, with overscan-safe framing (action-safe 90% area)
- **Pixel format**: `yuv420p` — no exceptions, mixed pix_fmt breaks concat silently
- **Framerate**: Constant target fps (default 24) via `fps` filter, applied before chunking
- **Colorspace**: Force consistent `colorspace`, `color_primaries`, `color_trc` (default BT.709; BT.601 option for SD/CRT output)
- **Odd dimensions**: Always use `scale=w=trunc(iw/2)*2:h=trunc(ih/2)*2` or force target res
- **Codec**: Re-encode all intermediates to same codec/profile/level (default h264 high)
- **GOP**: `-g 1` on all intermediates so every frame is a keyframe

### Anti-Strobe System (assemble.py)
This is an **artistic control**, not just a safety measure. All parameters tunable in config:
- **Luma normalization**: Compute mean luma per chunk; optionally normalize toward global mean using `eq` filter. Strength is a float 0.0 (off) to 1.0 (full normalization)
- **Buffer frames**: Optional mid-gray (50% luma) frames between cuts. Count configurable: 0 (off/raw strobe), 1 (default softening), 2+ (deliberate pacing)
- **Luma delta limit**: Post-assembly safety pass flags adjacent-frame luma deltas exceeding a threshold. Threshold is configurable — set high for aggressive cuts, low for smooth
- **Bypass mode**: Config flag to disable all anti-strobe processing for raw output

### Image Handling
- Static images require explicit duration: `-loop 1 -t [duration]` at target fps
- Must match video resolution exactly (even 1px off breaks concat)
- Force `yuv420p` — images default to `rgb24` or `rgba`
- Random insertion points in final sequence (seeded RNG)
- Image display duration configurable separately from video chunk duration

### Temporal Precision (chunk.py)
- Define chunk duration in **frames**, not seconds — avoid float precision drift
- Use `trim` filter for frame-accurate cuts, not `-ss`/`-t`
- Decode from compressed sources before cutting (B-frame reordering safety)
- `-bf 0` on intermediate encodes
- Chunk duration should vary randomly within configured range (e.g., 3–5 frames), seeded for reproducibility

### Probing & Validation (probe.py)
- `ffprobe -print_format json -show_streams -show_format` on every input before processing
- Reject or flag files that don't meet minimum specs (configurable)
- Extract: resolution, pix_fmt, fps, codec, duration, frame_count, colorspace, rotation
- Return structured data (dataclass or typed dict), not raw JSON downstream
- Handle variable-framerate sources: detect and warn, force CFR during normalization

### Manifest (manifest.py)
- Log every chunk: source file, start frame, frame count, position in final sequence
- Log every image insertion: source file, position, duration
- Store RNG seed for full reproducibility
- Store complete config snapshot used for the assembly
- Checksum final output; verify frame count against expected total
- Manifest format: JSON, human-readable, diffable

## Audio
- Strip audio from all sources during chunking (`-an` on all ffmpeg calls)
- Audio design handled separately — ambient/drone layer recommended for perceptual anchoring
- If audio pass-through is ever added, normalize sample rate/channels/codec independently

## ffmpeg Reference Commands
These are known-good patterns. Prefer these over improvised filter chains.

```bash
# Normalize an input (HD)
ffmpeg -i input.mp4 \
  -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=24,format=yuv420p" \
  -colorspace bt709 -color_primaries bt709 -color_trc bt709 \
  -c:v libx264 -preset fast -g 1 -bf 0 -an normalized.mp4

# Normalize an input (NTSC CRT target)
ffmpeg -i input.mp4 \
  -vf "scale=720:480:force_original_aspect_ratio=decrease,pad=720:480:(ow-iw)/2:(oh-ih)/2,fps=24,format=yuv420p" \
  -colorspace smpte170m -color_primaries smpte170m -color_trc smpte170m \
  -c:v libx264 -preset fast -g 1 -bf 0 -an normalized_ntsc.mp4

# Frame-accurate chunk extraction (frames 100-104, 5 frames)
ffmpeg -i normalized.mp4 \
  -vf "trim=start_frame=100:end_frame=105,setpts=PTS-STARTPTS" \
  -c:v libx264 -preset fast -g 1 -bf 0 -an chunk_042.mp4

# Image to video segment (5 frames at 24fps = 0.208s)
ffmpeg -loop 1 -i image.jpg \
  -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=24,format=yuv420p" \
  -t 0.208 -c:v libx264 -preset fast -g 1 -bf 0 -an img_segment.mp4

# Concat via demuxer
ffmpeg -f concat -safe 0 -i concat_manifest.txt -c copy output.mp4

# Probe an input
ffprobe -v quiet -print_format json -show_streams -show_format input.mp4
```

## Config Defaults (config.py)
```python
target_resolution = (1920, 1080)    # or (720, 480) for NTSC CRT
target_fps = 24
target_pix_fmt = "yuv420p"
target_colorspace = "bt709"         # or "smpte170m" for SD/CRT
aspect_mode = "letterbox"           # letterbox | crop | stretch
chunk_frames_min = 3
chunk_frames_max = 5
image_frames_min = 3
image_frames_max = 8
antistroke_enabled = True
antistroke_buffer_frames = 1        # 0 = off, 1+ = gray frame count
antistroke_luma_strength = 0.5      # 0.0 = off, 1.0 = full normalization
antistroke_delta_threshold = 80     # luma delta flag threshold (0-255)
rng_seed = None                     # None = random, int = reproducible
codec = "libx264"
preset = "fast"
```

## Dev Notes
- Test with heterogeneous inputs: phone video (variable fps, 1080p vertical), DSLR (24fps, 4K), screenshots (PNG, arbitrary res), GIFs, scanned images
- All config flows through `config.py` as a dataclass — CLI and future GUI both consume it
- Prefer ffmpeg filter chains over Python pixel manipulation — keep Python as orchestration
- Every function should be independently testable with a single file input
- macOS: verify ffmpeg is accessible via `shutil.which("ffmpeg")` at startup
- Do not introduce pip dependencies without explicit justification — subprocess + stdlib is the default