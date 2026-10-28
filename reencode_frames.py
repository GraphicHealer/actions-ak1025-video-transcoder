#!/usr/bin/env python3
"""Re-encode raw RGB frames to MJPEG using a factory AVI's quant tables.
Usage: reencode_frames.py frames.rgb out.mjpeg [factory.avi] [scale] [cap]

scale (default 1.0) multiplies the factory table values — smaller values mean
finer quantization, higher quality, larger files.

cap (bytes, optional) enables blur rate-control mode: every frame is encoded
with the SAME scaled tables (the AK1025 decoder mis-renders per-frame varying
DQTs), but frames whose output exceeds cap are re-encoded after progressive
Gaussian blur until they fit. The decoder also corrupts frames larger than
~16KB, so cap=15000 with scale=0.5 is a good quality/size balance —
easy frames get 2x finer quantization than factory while complex frames
just get slightly softened."""
import sys, io, os, struct
from PIL import Image

# Factory quant tables (zigzag order) lifted from an AK1025 stock ad AVI.
# Chroma table is nearly flat at 0x32 — coarse chroma is part of why the
# decoder renders these cleanly.
EMBEDDED_QT0 = ('080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a'
                '1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27'
                '393d38323c2e333432')
EMBEDDED_QT1 = ('0909090c0b0c180d0d1832211c21' + '32' * 50)

ZIG = [0,1,8,16,9,2,3,10,17,24,32,25,18,11,4,5,12,19,26,33,40,48,41,34,27,
       20,13,6,7,14,21,28,35,42,49,56,57,50,43,36,29,22,15,23,30,37,44,51,
       58,59,52,45,38,31,39,46,53,60,61,54,47,55,62,63]

def unzigzag(stored):
    nat = [0]*64
    for k, v in enumerate(stored):
        nat[ZIG[k]] = v
    return nat

def factory_tables(avi_path=None):
    """Factory quant tables, extracted from the brochure's stock AVI.
    Pass the path to a factory AVI to re-extract them; otherwise returns
    the embedded copy captured from an AK1025 unit's stock ad."""
    if avi_path is None:
        avi_path = os.environ.get('FACTORY_AVI')
    if avi_path and os.path.exists(avi_path):
        d = open(avi_path, 'rb').read()
        mi = d.find(b'movi')
        fsz = struct.unpack('<I', d[mi+8:mi+12])[0]
        f = d[mi+12:mi+12+fsz]
        i = 2; tabs = {}
        while i < len(f)-1:
            m = f[i+1]
            if m == 0xDA: break
            ln = struct.unpack('>H', f[i+2:i+4])[0]
            seg = f[i+4:i+2+ln]
            if m == 0xDB:
                j = 0
                while j < len(seg):
                    tlen = 65 if seg[j] < 16 else 129
                    tabs[seg[j] & 15] = seg[j+1:j+tlen]
                    j += tlen
            i += 2 + ln
        return [unzigzag(tabs[0]), unzigzag(tabs[1])]
    return [unzigzag(bytes.fromhex(EMBEDDED_QT0)),
            unzigzag(bytes.fromhex(EMBEDDED_QT1))]

from PIL import ImageFilter

BLURS = [0, 0.4, 0.6, 0.9, 1.3, 1.8, 2.5, 3.5, 5.0]
SCALE_FALLBACK = [1.0, 1.5, 2.5, 4.0]

def enc(im, tab):
    b = io.BytesIO()
    # quality=50 -> Pillow scale factor 100 -> qtables emitted verbatim
    # (quality=100 scales to 0 -> all-1 tables, near-lossless + huge)
    im.save(b, 'JPEG', quality=50, qtables=tab, subsampling=2, optimize=False)
    return b.getvalue()

def scaled(qt, s):
    return [[max(1, round(v * s)) for v in t] for t in qt]

def main():
    rgb_path, out_path = sys.argv[1], sys.argv[2]
    qt = factory_tables(sys.argv[3] if len(sys.argv) > 3 else None)
    scale = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
    cap = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    W, H = 320, 240
    fin = open(rgb_path, 'rb')
    fout = open(out_path, 'wb')
    n = blurred = 0
    stats = {}
    while True:
        buf = fin.read(W*H*3)
        if len(buf) < W*H*3: break
        im = Image.frombytes('RGB', (W, H), buf)
        tab = scaled(qt, scale)
        if cap:
            for r in BLURS:
                im2 = im if r == 0 else im.filter(ImageFilter.GaussianBlur(r))
                data = enc(im2, tab)
                if len(data) <= cap: break
            else:
                data = b''
            if not data or len(data) > cap:
                for fs in SCALE_FALLBACK:      # extreme frames: coarser tables
                    data = enc(im2, scaled(qt, scale * fs))
                    if len(data) <= cap: break
                blurred += 1
            stats[r] = stats.get(r, 0) + 1
        else:
            data = enc(im, tab)
        fout.write(data)
        n += 1
        if n % 500 == 0: print(n, 'frames', flush=True)
    print('total', n, 'blur histogram:', dict(sorted(stats.items())),
          'fallbacks:', blurred)

if __name__ == '__main__':
    main()
