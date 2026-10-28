#!/usr/bin/env bash
# Deploy a video to an Actions AK1025 video brochure.
# Finds the player by its USB identity (works regardless of which block
# device or FAT label the unit gets), wipes old media + index caches,
# and copies the video.
#
# If the input file isn't already in the brochure's AVI format, it is
# transcoded automatically (ffmpeg + mux_factory_avi.py).
#
# Usage:
#   ./deploy-rickroll.sh                # deploys device/Video/01.avi
#   ./deploy-rickroll.sh some.mp4|avi   # transcodes if needed, then deploys
set -euo pipefail

DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SRC="${1:-$DIR/device/Video/01.avi}"

[[ -f "$SRC" ]] || { echo "source not found: $SRC" >&2; exit 1; }

# --- does this file already match the brochure's format? ---
# codec-level: AVI with mjpeg 320x240 video + adpcm_ima_wav 22050Hz mono audio
# structural:  alternating 00dc/01wb chunks, 768-byte audio blocks,
#              4-byte-aligned chunks (the quirks plain ffmpeg AVIs lack)
is_compatible() {
    local f="$1" v a
    v=$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=codec_name,width,height -of csv=p=0 "$f" 2>/dev/null || true)
    a=$(ffprobe -v error -select_streams a:0 \
        -show_entries stream=codec_name,sample_rate,channels -of csv=p=0 "$f" 2>/dev/null || true)
    [[ $v == mjpeg,320,240* ]] || return 1
    [[ $a == adpcm_ima_wav,22050,1* ]] || return 1
    python3 - "$f" <<'PY'
import struct, sys
try:
    d = open(sys.argv[1], 'rb').read()
    assert d[:4] == b'RIFF' and d[8:12] == b'AVI '
    mi = d.find(b'movi')
    assert mi > 0
    pos, ids = mi + 4, (b'00dc', b'01wb')
    for k in range(10):
        cid = d[pos:pos + 4]
        sz = struct.unpack('<I', d[pos + 4:pos + 8])[0]
        assert cid == ids[k % 2]
        if cid == b'01wb':
            assert sz == 768
        pos += 8 + sz + (-sz % 4)
        assert d[pos:pos + 4] in ids
except Exception:
    sys.exit(1)
PY
}

# --- transcode arbitrary video -> brochure AVI ---
transcode() {
    local src="$1" out="$2" tmp crop vf has_audio dur
    tmp=$(mktemp -d); trap 'rm -rf "$tmp"' RETURN
    # auto-crop baked-in letterbox/pillarbox bars
    crop=$(ffmpeg -t 15 -i "$src" -vf "cropdetect=limit=24:round=2:reset=0" \
           -f null - 2>&1 | grep -oE 'crop=[0-9]+:[0-9]+:[0-9]+:[0-9]+' \
           | tail -1 | cut -d= -f2 || true)
    vf="scale=320:240:force_original_aspect_ratio=decrease,pad=320:240:(ow-iw)/2:(oh-ih)/2,fps=500000/34671"
    [[ -n $crop ]] && vf="crop=$crop,$vf"
    [[ -n $crop ]] && echo "  cropping baked-in bars: $crop"
    ffmpeg -v error -i "$src" -vf "$vf" \
        -c:v mjpeg -q:v 4 -pix_fmt yuvj420p -huffman default \
        -f mjpeg "$tmp/frames.mjpeg"
    has_audio=$(ffprobe -v error -select_streams a:0 \
                -show_entries stream=index -of csv=p=0 "$src" 2>/dev/null || true)
    if [[ -n $has_audio ]]; then
        ffmpeg -v error -i "$src" -ac 1 -ar 22050 -f s16le "$tmp/audio.pcm"
    else
        echo "  no audio stream — synthesizing silence"
        dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$src")
        ffmpeg -v error -f lavfi -i "anullsrc=r=22050:cl=mono" \
            -t "$dur" -f s16le "$tmp/audio.pcm"
    fi
    python3 "$DIR/mux_factory_avi.py" "$tmp/frames.mjpeg" "$tmp/audio.pcm" "$out"
}

# --- locate the device via udev by-id symlinks (serial/label independent) ---
shopt -s nullglob
DEVS=(/dev/disk/by-id/usb-ACTIONS*)
if (( ${#DEVS[@]} == 0 )); then
    # fallback: scan sdX udev properties for VID 10d6 / PID 1101
    for d in /dev/sd[a-z]; do
        [[ -b $d ]] || continue
        props=$(udevadm info --query=property --name="$d" 2>/dev/null || true)
        if grep -q '^ID_MODEL_ID=1101$' <<<"$props" \
           && grep -q '^ID_VENDOR_ID=10d6$' <<<"$props"; then
            DEVS=("$d"); break
        fi
    done
fi
(( ${#DEVS[@]} >= 1 )) || { echo "no Actions video brochure found — is it plugged in?" >&2; exit 1; }
DEV=$(readlink -f "${DEVS[0]}")
echo "device: $DEV"

# --- mount if needed ---
MNT=$(findmnt -n -o TARGET "$DEV" || true)
if [[ -z $MNT ]]; then
    MNT=$(udisksctl mount -b "$DEV" | sed -E 's/.* at (.+)\.?$/\1/')
    echo "mounted at $MNT"
else
    echo "already mounted at $MNT"
fi

# --- sanity check: this should look like a video brochure ---
if [[ ! -d "$MNT/Video" && ! -f "$MNT/VIDEO.LIB" ]]; then
    echo "warning: $MNT has no Video/ dir or VIDEO.LIB — creating Video/ anyway"
fi
mkdir -p "$MNT/Video" "$MNT/Photo"

# --- transcode if the input isn't already brochure-compatible ---
WORK="$SRC"
if ! is_compatible "$SRC"; then
    command -v ffmpeg >/dev/null || { echo "ffmpeg needed to transcode $SRC" >&2; exit 1; }
    echo "input is not in brochure format — transcoding..."
    WORK=$(mktemp --suffix=.avi)
    transcode "$SRC" "$WORK"
    trap 'rm -f "$WORK"' EXIT
fi

# --- wipe old media + index caches, deploy ---
rm -f "$MNT/VIDEO.LIB" "$MNT/PICTURE.LIB"
rm -f "$MNT/Video/"*.avi "$MNT/Video/"*.mp4 2>/dev/null || true
cp "$WORK" "$MNT/Video/01.avi"
sync
echo "deployed $(basename "$SRC") -> $MNT/Video/01.avi"
echo "safe to unplug."
