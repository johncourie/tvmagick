# tvmagick

Rapid-cut video assembler for perceptual disruption. Takes heterogeneous video and image sources, normalizes them, chops them into frame-accurate chunks at the threshold of semantic recognition, shuffles them, and concatenates them into a single output. Designed for CRT television display arrays.

Built on the cut-up technique tradition. Each shot lands at the threshold of recognition but below narrative integration — the viewer experiences meaning without story.

## Installation

### Requirements

- macOS, Linux, or Windows 10/11
- Python 3.10+
- ffmpeg/ffprobe on PATH

### Install ffmpeg

**macOS** (Homebrew):
```bash
brew install ffmpeg
```

**Linux** (Debian/Ubuntu):
```bash
sudo apt install ffmpeg
```

**Linux** (Fedora/RHEL):
```bash
sudo dnf install ffmpeg
```

**Windows** (winget):
```powershell
winget install ffmpeg
```

**Windows** (Chocolatey):
```powershell
choco install ffmpeg
```

### Setup

```bash
git clone <repo-url> && cd splicer
```

No pip dependencies. The entire pipeline uses Python standard library + ffmpeg subprocess calls.

Verify the install:

```bash
python3 cli.py --help
```

## Usage

### Basic

```bash
# Process a directory of videos and images
python3 cli.py ./my_footage/ -o output

# Process specific files
python3 cli.py clip1.mp4 clip2.mov photo.jpg -o output

# Mix files and directories
python3 cli.py ./videos/ ./photos/ extra_clip.mp4 -o output
```

### Output Targets

```bash
# HD (default) — 1920x1080, BT.709
python3 cli.py ./footage/

# NTSC CRT — 720x480, BT.601
python3 cli.py ./footage/ --ntsc

# PAL CRT — 720x576, BT.601
python3 cli.py ./footage/ --pal

# Custom resolution
python3 cli.py ./footage/ --resolution 1280x720
```

### Chunk Timing

Chunk duration is defined in frames, not seconds. At 24fps:

| Frames | Duration | Perceptual effect |
|--------|----------|-------------------|
| 2 | 83ms | Liminal — gist-only recognition |
| 3-5 | 100-167ms | Semantic threshold — recognized but unlinked (default) |
| 7+ | 292ms+ | Narrative integration begins |

```bash
# Default: 3-5 frame chunks at 24fps
python3 cli.py ./footage/

# Aggressive: 2-3 frames, gist-only
python3 cli.py ./footage/ --chunk-min 2 --chunk-max 3

# Slower: 5-7 frames, more readable
python3 cli.py ./footage/ --chunk-min 5 --chunk-max 7

# Change framerate
python3 cli.py ./footage/ --fps 30
```

### Anti-Strobe Controls

The anti-strobe system is an artistic parameter, not just a safety feature. All controls are tunable.

```bash
# Default: buffer frames on, luma normalization at 50%, delta threshold 80
python3 cli.py ./footage/

# Raw output — no anti-strobe processing at all
python3 cli.py ./footage/ --no-antistrobe

# More buffer frames for deliberate pacing
python3 cli.py ./footage/ --buffer-frames 3

# No buffer frames, but keep luma normalization
python3 cli.py ./footage/ --buffer-frames 0

# Full luma normalization (flatten brightness across cuts)
python3 cli.py ./footage/ --luma-strength 1.0

# No luma normalization (keep raw brightness differences)
python3 cli.py ./footage/ --luma-strength 0.0

# Lower delta threshold to flag more cuts as high-contrast
python3 cli.py ./footage/ --luma-threshold 40
```

### Aspect Ratio Handling

```bash
# Letterbox (default) — black bars, no cropping
python3 cli.py ./footage/ --aspect letterbox

# Center crop — fill frame, lose edges
python3 cli.py ./footage/ --aspect crop

# Stretch — distort to fill
python3 cli.py ./footage/ --aspect stretch
```

### Reproducibility

```bash
# Seeded run — same inputs + same seed = identical output
python3 cli.py ./footage/ --seed 42

# The seed is printed at the start of every run and recorded in the manifest
# If no seed is provided, a random one is chosen and logged
```

### Codec

```bash
# Change x264 encoding speed (ultrafast/superfast/veryfast/faster/fast/medium/slow/slower/veryslow)
python3 cli.py ./footage/ --preset medium
```

### Prep Mode

Preprocessing for raw source material. Runs standalone — produces files in the output directory, then exits. Feed the output into a normal splicer run.

```bash
# Grain: split long videos into ~60s segments (codec copy, fast)
python3 cli.py --prep --grain ./raw_footage/ -o ./prepped/

# Grain with custom segment length
python3 cli.py --prep --grain --grain-duration 90 ./raw_footage/ -o ./prepped/

# Greyscale: re-encode all videos to greyscale
python3 cli.py --prep --greyscale ./videos/ -o ./greyscale/

# Both: grain first, then greyscale the segments
python3 cli.py --prep --grain --greyscale ./raw_footage/ -o ./prepped/

# Then feed prepped output into the splicer pipeline
python3 cli.py ./prepped/ --seed 42 --ntsc -o output_final
```

**Grain** uses ffmpeg's segment muxer with codec copy (no re-encode) for speed. Videos shorter than the grain duration are copied through unchanged. The main pipeline handles normalization later.

**Greyscale** re-encodes with `hue=s=0` and strips audio. When combined with grain, intermediate grain segments are cleaned up automatically.

## Output

Each run produces two files in the output directory:

### `splicer_output.mp4`
The assembled video. All inputs normalized to identical specs (resolution, fps, pixel format, colorspace, codec), chunked, shuffled, and concatenated. Audio is always stripped.

### `splicer_manifest.json`
A complete build log for reproducibility:

```json
{
  "rng_seed": 42,
  "config_snapshot": { ... },
  "chunks": [
    {"source_file": "...", "start_frame": 0, "frame_count": 5, "chunk_index": 0},
    ...
  ],
  "image_insertions": [
    {"source_file": "...", "position": 13, "duration_frames": 4},
    ...
  ],
  "luma_flags": [
    {"position": 7, "delta": 85.3, "threshold": 80},
    ...
  ],
  "expected_frame_count": 283,
  "actual_frame_count": 283,
  "output_checksum": "sha256:..."
}
```

## Supported Formats

### Video
`.mp4` `.mov` `.avi` `.mkv` `.webm` `.m4v` `.mpg` `.mpeg` `.ts`

### Images
`.png` `.jpg` `.jpeg` `.bmp` `.tiff` `.tif` `.webp` `.gif`

Images are converted to video segments at the target resolution and fps. Their display duration is randomized within the configured image frame range (default 3-8 frames). Insertion position in the final sequence is randomized (seeded).

## Pipeline

```
input files
    │
    ▼
  probe ──── ffprobe each input, extract metadata, validate
    │
    ▼
  normalize ── conform resolution, fps, pix_fmt, colorspace, codec
    │           (letterbox/crop/stretch, CRT-aware, all keyframes)
    ▼
  chunk ────── frame-accurate trim into 3-5 frame segments
    │           (seeded random duration per chunk)
    ▼
  assemble ─── shuffle chunks, interleave images,
    │           insert anti-strobe buffers, luma normalize,
    │           concat via ffmpeg demuxer, luma delta safety pass
    ▼
  manifest ─── JSON build log with full config, chunk map,
    │           checksums, and luma warnings
    ▼
  output.mp4 + manifest.json
```

## Architecture

```
splicer/
├── cli.py               # argparse entry point
├── core/
│   ├── config.py        # SplicerConfig dataclass — all parameters
│   ├── probe.py         # ffprobe wrapper, input validation
│   ├── normalize.py     # resolution/fps/colorspace conforming
│   ├── chunk.py         # frame-accurate extraction
│   ├── assemble.py      # concat, anti-strobe, luma normalization
│   ├── manifest.py      # reproducible JSON build log
│   └── prep.py          # preprocessing — grain (segment) and greyscale
└── *.bash               # legacy scripts (reference only)
```

Zero pip dependencies. Python is orchestration; ffmpeg is the workhorse.
