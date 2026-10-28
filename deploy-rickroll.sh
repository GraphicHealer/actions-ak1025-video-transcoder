#!/usr/bin/env bash
# Deploy the rickroll to an Actions AK1025 video brochure.
# Works regardless of which block device / FAT label the unit gets —
# finds it by the ACTIONS USB manufacturer string.
#
# Usage:
#   ./deploy-rickroll.sh              # deploys 01-std-fixed.avi
#   ./deploy-rickroll.sh some.avi     # deploys a different file
set -euo pipefail

DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SRC="${1:-$DIR/device/Video/01.avi}"

[[ -f "$SRC" ]] || { echo "source not found: $SRC" >&2; exit 1; }

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

# --- wipe old media + index caches, deploy ---
rm -f "$MNT/VIDEO.LIB" "$MNT/PICTURE.LIB"
rm -f "$MNT/Video/"*.avi "$MNT/Video/"*.mp4 2>/dev/null || true
cp "$SRC" "$MNT/Video/01.avi"
sync
echo "deployed $(basename "$SRC") -> $MNT/Video/01.avi"
echo "safe to unplug."
