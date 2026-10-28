#!/usr/bin/env python3
"""Re-encode raw RGB frames to MJPEG using a factory AVI's exact quant tables.
Usage: reencode_frames.py frames.rgb out.mjpeg [factory.avi]"""
import sys, io, struct
from PIL import Image

ZIG = [0,1,8,16,9,2,3,10,17,24,32,25,18,11,4,5,12,19,26,33,40,48,41,34,27,
       20,13,6,7,14,21,28,35,42,49,56,57,50,43,36,29,22,15,23,30,37,44,51,
       58,59,52,45,38,31,39,46,53,60,61,54,47,55,62,63]

def unzigzag(stored):
    nat = [0]*64
    for k, v in enumerate(stored):
        nat[ZIG[k]] = v
    return nat

def factory_tables(avi_path):
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

def main():
    rgb_path, out_path = sys.argv[1], sys.argv[2]
    qt = factory_tables(sys.argv[3] if len(sys.argv) > 3 else 'factory.avi')
    W, H = 320, 240
    fin = open(rgb_path, 'rb')
    fout = open(out_path, 'wb')
    n = 0
    while True:
        buf = fin.read(W*H*3)
        if len(buf) < W*H*3: break
        im = Image.frombytes('RGB', (W, H), buf)
        b = io.BytesIO()
        im.save(b, 'JPEG', quality=100, qtables=qt, subsampling=2, optimize=False)
        fout.write(b.getvalue())
        n += 1
        if n % 500 == 0: print(n, 'frames', flush=True)
    print('total', n)

if __name__ == '__main__':
    main()
