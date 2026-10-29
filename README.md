# Actions AK1025 Video Transcoder

Transcode arbitrary video into the exact AVI variant accepted by video
brochure players built on the Actions Semiconductor AK1025 / ATJ-family
"D-Wave" SoC — the little cardboard video-ad players (LCD + speaker,
magnetic lid trigger) that expose a ~94MB FAT volume over USB.

The stock content on these devices is a single MJPEG AVI with quirks a plain
`ffmpeg` encode doesn't reproduce — this repo documents the format and
provides the tools to generate files that actually play.

## Requirements

- **Deploying the included video**: nothing — copy `device/` contents, or run
  `deploy-video.py` (Python 3 only, no deps) / `deploy-video.sh` (Linux bash).
- **Transcoding your own video**: `ffmpeg` + `ffprobe`, Python 3, and Pillow
  (`pip install pillow` — enables the factory-table encoder; the ffmpeg-MJPEG
  path works without it).

## Quickstart

The `device/` directory mirrors the brochure's filesystem — copy its contents
to the FAT volume root (`device/Video/01.avi` → `Video/01.avi`), or use the
deploy script which finds the player by its USB identity, wipes the old media
and index caches, and copies the video:

```bash
./deploy-video.sh            # Linux: plug in a brochure, run this, unplug
./deploy-video.sh my.mp4     # any video file works — if it isn't already
                                # in the brochure's format it is transcoded
                                # automatically before copying
```

On Windows/macOS (or anywhere Python runs), use the Python deployer —
it scans mounted volumes for the brochure's signature (a ~94MB volume
with `Video/` or `VIDEO.LIB`), does the format check natively, and
deploys the same way:

```bash
python deploy-video.py
python deploy-video.py my.mp4
```

The script checks the file itself (codec, resolution, audio format, and the
factory chunk layout); a conforming AVI is copied straight over, anything else
goes through the full transcode pipeline first — including auto-cropping
baked-in letterbox/pillarbox bars. To control the conversion by hand, see
**Required video format** below.

## Hardware identification

| Property | Value |
|---|---|
| USB VID:PID | `10d6:1101` |
| Chip | Actions Semiconductor — AK1025 / ATJ-family "D-Wave" MP4 player SoC |
| USB strings | Manufacturer `ACTIONS`, Product `HS USB FlashDisk` |
| Storage | 94MB FAT16, superfloppy (no partition table), mounts as e.g. `/dev/sda` |
| Trigger | Hall/magnetic sensor — opening the lid powers on and plays |
| Display | **320x240**, 4:3, renders 1:1 at native res |

## Confirmed by testing

- Panel is exactly 320x240 (verified two ways: a 10px text label measures 10px
  on screen; a labeled 60x60px box measures 60x60).
- Photo decoder: baseline JPEG only; accepted everything tested from 320x240 up
  to 1600x1200 (decode ceiling not reached, likely ~megapixels).
- Photo scaler: downscales anything larger; upscales only up to ~1.7x —
  images under ~192px wide show centered with black bars.
- The display pipeline always resamples — no 1:1 path for non-native sizes,
  so fine 1px test patterns blur to gray on anything but exact-res images.
- Both numeric (`01.jpg`) and arbitrary (`verify.jpg`) filenames play.

## Filesystem layout

```
/
├── VIDEO.LIB     384KB proprietary blob (header "VIDEO.L"/"ALLFILE").
│                 Media index/thumbnail cache — the chip regenerates it.
│                 Safe to delete (it rebuilds on next scan).
├── Video/        Numbered .avi files, e.g. 01.avi, 02.avi — play in order
├── Photo/        JPEG slideshow images
└── System Volume Information/   Windows junk, ignore
```

## Required video format

The decoder is picky — normal MP4/H.264 will NOT play, and it also rejects
many otherwise-valid ffmpeg AVIs with "Format error".

| | |
|---|---|
| Container | AVI |
| Video codec | MJPEG (baseline), yuvj420p — **4:2:0 only** (4:2:2/4:4:4 rejected) |
| Resolution | 320x240 (native — anything else just gets resampled) |
| Frame rate | ~14.42 fps (22050/1529 — one audio block per video frame) |
| Audio codec | IMA ADPCM WAV, 22050 Hz, mono, 768-byte blocks |

### Decoder limits discovered by experiment

- **~16KB per video frame** — JPEG frames larger than ~16384 bytes are
  truncated/corrupt: shows as block-boundary garbage ("grid" of color
  transitions). Keep every frame under ~15KB.
- **Constant quant tables** — the decoder doesn't handle per-frame varying
  DQTs; frames decode with mismatched tables (flickering wrong colors).
- **Factory quant tables look best** — the stock tables (embedded in
  `reencode_frames.py`) are tuned so the hardest frames just fit the buffer.
  Finer tables only help if frames still fit the cap.
- **JPEG header layout matters** — `AVI1` APP0 marker, two DQTs, four
  standard Huffman tables (not optimized).

### Conversion — the reliable way (Python tools)

```bash
# 1. Extract frames as raw RGB (crop first if the source has baked-in bars)
ffmpeg -i input.mp4 \
  -vf "scale=320:240:force_original_aspect_ratio=decrease,pad=320:240:(ow-iw)/2:(oh-ih)/2,fps=500000/34671" \
  -f rawvideo -pix_fmt rgb24 frames.rgb

# 2. Extract audio as raw s16le mono 22050Hz
ffmpeg -i input.mp4 -ac 1 -ar 22050 -f s16le audio.pcm

# 3. Re-encode frames with the factory quant tables
#    (args: in.rgb out.mjpeg [factory.avi] [scale] [cap-bytes])
#    cap adds mild blur-rate-control for frames that would exceed it
python3 reencode_frames.py frames.rgb frames.mjpeg "" 1.0 16000

# 4. Mux into a factory-layout AVI (AVI1 JPEG headers, 768-byte IMA-ADPCM
#    blocks, 4-byte chunk alignment, idx1)
python3 mux_factory_avi.py frames.mjpeg audio.pcm 01.avi
```

Copy `01.avi` to `Video/` on the device. Deleting `VIDEO.LIB` first forces
the chip to re-index (it regenerates automatically).

**Baked-in pillarbox bars**: some sources letterbox/pillarbox content inside
the frame (e.g. the rickroll upload is 4:3 video boxed into 854x480). If you
scale-to-fit that, the bars survive and the video plays inset with black on
all sides. Crop first — detect the box with
`ffmpeg -i input.mp4 -vf cropdetect -f null -`, then prepend the crop:

```bash
-vf "crop=640:480,scale=320:240,fps=500000/34671"
```

Verify the result fills the frame before deploying (content should span
x=0..319, y=0..239).

`mux_factory_avi.py` was verified by remuxing the factory AVI's own payloads:
the result is byte-identical to the original except 5 garbage bytes in the
`avih` reserved fields.

### Quality tuning

`reencode_frames.py`'s `scale` argument multiplies the factory tables:
`1.0` = factory quality (recommended). Smaller values quantize finer but
frames grow — combined with the `cap` argument, oversized frames get mild
progressive blur instead of coarser tables (constant DQTs are required).
`0.5` with cap 15000 fills ~42MB but visibly softens busy scenes; `1.0`
is the sweet spot. Without cap, oversize frames will corrupt on-device.

### Simple one-liner (sometimes rejected)

The plain ffmpeg encode plays on some firmware but can hit "Format error":

```bash
ffmpeg -i input.mp4 \
  -vf "scale=320:240:force_original_aspect_ratio=decrease,pad=320:240:(ow-iw)/2:(oh-ih)/2,fps=15" \
  -c:v mjpeg -q:v 4 -pix_fmt yuvj420p \
  -c:a adpcm_ima_wav -ar 22050 -ac 1 \
  01.avi
```

Copy to `Video/01.avi` on the device. Use `02.avi`, `03.avi`, … for sequences.

### Photo conversion

```bash
ffmpeg -i input.jpg \
  -vf "scale=320:240:force_original_aspect_ratio=decrease,pad=320:240:(ow-iw)/2:(oh-ih)/2" \
  -q:v 3 out.jpg
```

## Capacity

~93MB free → roughly, at 14.42fps + ADPCM audio (~120 kb/s):

| Encoder | Bitrate | Max length |
|---|---|---|
| **Factory tables (recommended)** | ~1300 kb/s | ~10 min |
| ffmpeg `-q:v 2` (ceiling) | ~1700 kb/s | ~7 min |
| ffmpeg `-q:v 4` (clean) | ~1400 kb/s | ~9 min |
| ffmpeg `-q:v 10` (blocky) | ~600 kb/s | ~22 min |
| ffmpeg `-q:v 15` (ugly) | ~400 kb/s | ~33 min |

Note: ffmpeg-path bitrates are approximate and frame sizes can exceed the
~16KB decoder cap on complex content — the factory-table encoder with a cap
is the safe route.

## Working with the device

```bash
udisksctl mount -b /dev/sda        # mounts at /run/media/$USER/0000-006F
# copy files, then:
sync && udisksctl unmount -b /dev/sda
```

Always `sync` before unplugging. The device is a superfloppy — the FAT fs is
directly on `/dev/sda`, not a partition.

## Repository contents

```
device/             Drop-in brochure filesystem — copy contents to the volume root
deploy-video.sh     Linux deployer (udev detection, auto-mount, auto-transcode)
deploy-video.py     Cross-platform deployer (Windows / Linux / macOS)
mux_factory_avi.py  Factory-layout AVI muxer — verified byte-identical
                    remux of the stock file except 5 garbage header bytes
reencode_frames.py  Factory-quant-table JPEG encoder (scale / cap / blur modes)
```

## Unexplored / further work

- **ADFU mode**: Actions chips have a Device Firmware Update mode, often
  exposed as a second USB personality when a button is held during plug-in.
  Open-source flashing tools exist for the ATJ/AK family. Bricking risk —
  not needed for content changes.
- **VIDEO.LIB**: packed/encrypted resource blob, format not reverse-engineered.
- Play order with multiple `Video/*.avi` files and the photo slideshow
  interval were not characterized.
