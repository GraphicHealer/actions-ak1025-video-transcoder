#!/usr/bin/env python3
"""Cross-platform deployer for the Actions AK1025 video brochure.

Finds the player by scanning mounted volumes for its signature (a ~94MB
volume with a Video/ dir or VIDEO.LIB), transcodes the input through the
factory pipeline if it isn't already in brochure format, wipes old media
+ index caches, and copies the video. Works on Windows, Linux, macOS.

Usage:
    deploy-video.py                # deploys device/Video/01.avi
    deploy-video.py some.mp4|avi   # transcodes if needed, then deploys

Transcoding requires ffmpeg + ffprobe on PATH (and Pillow for the
higher-quality factory-table encoder; falls back to ffmpeg MJPEG).
"""
import glob, os, platform, re, shutil, string, struct, subprocess, sys, tempfile

DIR = os.path.dirname(os.path.realpath(__file__))
DEFAULT_SRC = os.path.join(DIR, 'device', 'Video', '01.avi')
MAX_VOL = 250 * 1024 * 1024          # brochure volume is ~94MB
WAVEFMT = bytes.fromhex('11000100' + '22560000' + '432b0000' + '0003' + '0400')
                                     # IMA-ADPCM mono 22050Hz, 768B blocks


# ---------- brochure-format detection (pure python, no ffprobe) ----------

def is_compatible(path):
    """True if the file already matches the brochure's AVI format:
    AVI, MJPEG 320x240 4:2:0, IMA-ADPCM 22050 mono 768B blocks,
    alternating 00dc/01wb chunks, 4-byte-aligned."""
    try:
        d = open(path, 'rb').read()
    except OSError:
        return False
    if len(d) < 4096 or d[:4] != b'RIFF' or d[8:12] != b'AVI ':
        return False
    if WAVEFMT not in d[:2048]:        # audio WAVEFORMATEX in hdrl
        return False
    mi = d.find(b'movi')
    if mi < 0:
        return False
    pos = mi + 4
    ids = (b'00dc', b'01wb')
    for k in range(10):
        cid = d[pos:pos + 4]
        if cid != ids[k % 2]:
            return False
        sz = struct.unpack('<I', d[pos + 4:pos + 8])[0]
        if cid == b'01wb' and sz != 768:
            return False
        if k == 0:                     # verify first JPEG: 320x240, 4:2:0
            fr = d[pos + 8:pos + 8 + sz]
            if not _is_420_320(fr):
                return False
        pos += 8 + sz + (-sz % 4)
        if d[pos:pos + 4] not in ids:
            return False
    return True


def _is_420_320(jpg):
    if jpg[:2] != b'\xff\xd8':
        return False
    i = 2
    while i < len(jpg) - 4:
        m = jpg[i + 1]
        ln = struct.unpack('>H', jpg[i + 2:i + 4])[0]
        if m == 0xDA:
            return False
        if m == 0xC0:
            seg = jpg[i + 4:i + 2 + ln]
            h, w = seg[1] * 256 + seg[2], seg[3] * 256 + seg[4]
            sampl = [seg[7 + 3 * c] for c in range(seg[5])]
            return (w, h) == (320, 240) and sampl == [0x22, 0x11, 0x11]
        i += 2 + ln
    return False


# ---------- device discovery ----------

def is_brochure_root(path):
    try:
        total = shutil.disk_usage(path).total
    except OSError:
        return False
    if not 1_000_000 < total <= MAX_VOL:
        return False
    return (os.path.isdir(os.path.join(path, 'Video'))
            or os.path.isfile(os.path.join(path, 'VIDEO.LIB')))


def candidate_roots():
    if os.name == 'nt':
        import ctypes
        roots = []
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            if mask & (1 << i):
                roots.append(letter + ':\\')
        return roots
    roots = ['/Volumes']
    user = os.environ.get('USER') or os.environ.get('LOGNAME') or ''
    for base in ('/run/media', '/media', '/mnt'):
        roots += [os.path.join(base, user), base]
    roots += ['/proc/mounts']          # handled specially below
    out = []
    for r in roots:
        if r == '/proc/mounts':
            continue
        if os.path.isdir(r):
            out += [os.path.join(r, d) for d in sorted(os.listdir(r))]
    try:
        for line in open('/proc/mounts'):
            out.append(line.split()[1])
    except OSError:
        pass
    return out


def find_device():
    # Linux fast path: udev by-id symlink (mount-independent)
    for p in sorted(glob.glob('/dev/disk/by-id/usb-ACTIONS*')):
        dev = os.path.realpath(p)
        mnt = mounted_at(dev)
        if mnt is None:
            mnt = mount(dev)
        if mnt:
            return mnt
    # scan all mounted volumes for the brochure signature
    for r in candidate_roots():
        if is_brochure_root(r):
            return r
    return None


def mounted_at(dev):
    try:
        for line in open('/proc/mounts'):
            if line.split()[0] == dev:
                return line.split()[1]
    except OSError:
        pass
    return None


def mount(dev):
    try:
        out = subprocess.run(['udisksctl', 'mount', '-b', dev],
                             capture_output=True, text=True, check=True)
        m = re.search(r' at (.+?)\.?$', out.stdout.strip())
        return m.group(1) if m else None
    except (OSError, subprocess.CalledProcessError):
        return None


# ---------- transcode ----------

def ffmpeg_bin(name):
    exe = shutil.which(name)
    if not exe:
        sys.exit(f"{name} not found on PATH — install ffmpeg to transcode")
    return exe


def transcode(src, dst, tmp):
    ffmpeg, ffprobe = ffmpeg_bin('ffmpeg'), ffmpeg_bin('ffprobe')
    # auto-crop baked-in letterbox/pillarbox bars
    out = subprocess.run(
        [ffmpeg, '-t', '15', '-i', src, '-vf',
         'cropdetect=limit=24:round=2:reset=0', '-f', 'null', os.devnull],
        capture_output=True, text=True).stderr
    crops = re.findall(r'crop=(\d+:\d+:\d+:\d+)', out)
    vf = ('scale=320:240:force_original_aspect_ratio=decrease,'
          'pad=320:240:(ow-iw)/2:(oh-ih)/2,fps=500000/34671')
    if crops:
        vf = f'crop={crops[-1]},' + vf
        print(f'  cropping baked-in bars: {crops[-1]}')

    frames = os.path.join(tmp, 'frames.mjpeg')
    try:
        import PIL  # noqa: F401
        rgb = os.path.join(tmp, 'frames.rgb')
        subprocess.run([ffmpeg, '-v', 'error', '-i', src, '-vf', vf,
                        '-f', 'rawvideo', '-pix_fmt', 'rgb24', rgb], check=True)
        subprocess.run([sys.executable,
                        os.path.join(DIR, 'reencode_frames.py'),
                        rgb, frames, '', '1.0', '16000'], check=True)
    except ImportError:
        print('  (Pillow not installed — using ffmpeg MJPEG path;')
        print('   pip install pillow enables the factory-table encoder)')
        subprocess.run([ffmpeg, '-v', 'error', '-i', src, '-vf', vf,
                        '-c:v', 'mjpeg', '-q:v', '2', '-pix_fmt', 'yuvj420p',
                        '-huffman', 'default', '-f', 'mjpeg', frames], check=True)

    pcm = os.path.join(tmp, 'audio.pcm')
    probe = subprocess.run(
        [ffprobe, '-v', 'error', '-select_streams', 'a:0',
         '-show_entries', 'stream=index', '-of', 'csv=p=0', src],
        capture_output=True, text=True)
    if probe.stdout.strip():
        subprocess.run([ffmpeg, '-v', 'error', '-i', src, '-ac', '1',
                        '-ar', '22050', '-f', 's16le', pcm], check=True)
    else:
        print('  no audio stream — synthesizing silence')
        dur = subprocess.run(
            [ffprobe, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', src], capture_output=True, text=True).stdout.strip()
        subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                        'anullsrc=r=22050:cl=mono', '-t', dur or '10',
                        '-f', 's16le', pcm], check=True)
    subprocess.run([sys.executable, os.path.join(DIR, 'mux_factory_avi.py'),
                    frames, pcm, dst], check=True)


# ---------- deploy ----------

def deploy(src, root):
    video_dir = os.path.join(root, 'Video')
    os.makedirs(video_dir, exist_ok=True)
    os.makedirs(os.path.join(root, 'Photo'), exist_ok=True)
    for name in ('VIDEO.LIB', 'PICTURE.LIB'):
        p = os.path.join(root, name)
        if os.path.exists(p):
            os.remove(p)
    for pat in ('*.avi', '*.mp4', '*.AVI', '*.MP4'):
        for p in glob.glob(os.path.join(video_dir, pat)):
            os.remove(p)
    dst = os.path.join(video_dir, '01.avi')
    shutil.copyfile(src, dst)
    with open(dst, 'rb') as f:                # flush to the device
        os.fsync(f.fileno())
    if hasattr(os, 'sync'):
        os.sync()
    print(f'deployed {os.path.basename(src)} -> {dst}')
    print('wait a few seconds, then safely eject/unplug.')


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC
    if not os.path.isfile(src):
        sys.exit(f'source not found: {src}')

    root = find_device()
    if not root:
        sys.exit('no Actions video brochure found — is it plugged in?')
    print(f'device: {root}')

    work = src
    tmp = None
    try:
        if not is_compatible(src):
            print('input is not in brochure format — transcoding...')
            tmp = tempfile.mkdtemp()
            work = os.path.join(tmp, '01.avi')
            transcode(src, work, tmp)
        deploy(work, root)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
