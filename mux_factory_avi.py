#!/usr/bin/env python3
"""Remux MJPEG frames + s16 PCM into a factory-style AVI for the Actions AK1025
video brochure chip. Replicates the exact chunk layout of the original ad:
minimal hdrl, 768-byte IMA-ADPCM audio blocks, 1 audio block per video frame,
alternating 00dc/01wb chunks, idx1 trailer, no INFO/JUNK/vprp."""
import struct, sys

STEP = [7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,
        73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,
        408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,
        1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,
        7132,7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,
        22385,24623,27086,29794,32767]
IDX = [-1,-1,-1,-1,2,4,6,8,-1,-1,-1,-1,2,4,6,8]
BLK = 768          # audio block align
SPB = 1529         # samples per block
RATE = 22050
USEC = 69342       # frame period us => 22050/1529 fps

def adpcm_blocks(pcm):
    """s16le mono bytes -> list of 768-byte IMA ADPCM WAV blocks."""
    import array
    s = array.array('h'); s.frombytes(pcm)
    if sys.byteorder == 'big': s.byteswap()
    out = []
    for base in range(0, len(s), SPB):
        blk_samples = s[base:base+SPB]
        pred = blk_samples[0]
        idx = 0
        data = bytearray()
        data += struct.pack('<hBB', pred, 0, 0)
        nibbles = []
        for smp in blk_samples[1:]:
            step = STEP[idx]
            diff = smp - pred
            nib = 8 if diff < 0 else 0
            d = abs(diff)
            if d >= step: nib |= 4; d -= step
            if d >= step >> 1: nib |= 2; d -= step >> 1
            if d >= step >> 2: nib |= 1
            vpd = step >> 3
            if nib & 4: vpd += step
            if nib & 2: vpd += step >> 1
            if nib & 1: vpd += step >> 2
            pred = pred - vpd if nib & 8 else pred + vpd
            pred = max(-32768, min(32767, pred))
            idx = max(0, min(88, idx + IDX[nib]))
            nibbles.append(nib)
        # pack 8 nibbles per 4-byte group: byte j = samples 2j(lo),2j+1(hi)
        for i in range(0, len(nibbles), 8):
            grp = nibbles[i:i+8]
            while len(grp) < 8: grp.append(0)
            for j in range(4):
                data.append(grp[2*j] | (grp[2*j+1] << 4))
        while len(data) < BLK: data.append(0)
        out.append(bytes(data[:BLK]))
    return out

APP0_AVI1 = b'\xff\xe0' + struct.pack('>H', 16) + b'AVI1' + b'\0' * 10

def norm_frame(jpg):
    """Rewrite ffmpeg MJPEG frame headers to factory style:
    JFIF APP0 -> AVI1 APP0, drop COM, duplicate DQT as table 1 and
    repoint chroma qt selectors, split combined DHT into per-table segs."""
    assert jpg[:2] == b'\xff\xd8'
    i = 2
    segs = []
    while True:
        m = jpg[i + 1]
        ln = struct.unpack('>H', jpg[i + 2:i + 4])[0]
        segs.append((m, jpg[i + 4:i + 2 + ln]))
        if m == 0xDA:
            tail = jpg[i + 2 + ln:]
            break
        i += 2 + ln
    dqts, dhts, sof, sos = [], [], None, None
    for m, seg in segs:
        if m in (0xFE, 0xE0):
            continue
        if m == 0xDB:
            j = 0
            while j < len(seg):
                tlen = 65 if seg[j] < 16 else 129
                dqts.append(seg[j:j + tlen])
                j += tlen
        elif m == 0xC4:
            j = 0
            while j < len(seg):
                n = sum(seg[j + 1:j + 17])
                dhts.append(seg[j:j + 17 + n])
                j += 17 + n
        elif m == 0xC0:
            seg = bytearray(seg)
            for c in range(1, seg[5]):
                seg[8 + c * 3] = 1
            sof = bytes(seg)
        elif m == 0xDA:
            sos = seg
    out = bytearray(b'\xff\xd8' + APP0_AVI1)
    emit = lambda mk, pl: b'\xff' + bytes([mk]) + struct.pack('>H', len(pl) + 2) + pl
    for t in dqts:
        out += emit(0xDB, t)
    if len(dqts) == 1:
        out += emit(0xDB, b'\x01' + dqts[0][1:])
    if sof:
        out += emit(0xC0, sof)
    dhts.sort(key=lambda t: (t[0] & 0x0F, t[0] >> 4))
    for t in dhts:
        out += emit(0xC4, t)
    if sos:
        out += emit(0xDA, sos)
    return bytes(out) + tail

def pad4(n):
    return (4 - n % 4) % 4

def chunk(cid, body):
    b = cid + struct.pack('<I', len(body)) + body
    return b + b'\0' * pad4(len(body))

def list_chunk(tag, body):
    return b'LIST' + struct.pack('<I', 4 + len(body)) + tag + body

def main(mjpeg_path, pcm_path, out_path):
    raw = open(mjpeg_path, 'rb').read()
    frames = []
    i = 0
    while True:
        s = raw.find(b'\xff\xd8', i)
        if s < 0: break
        e = raw.find(b'\xff\xd9', s) + 2
        frames.append(norm_frame(raw[s:e])); i = e
    nframes = len(frames)
    blocks = adpcm_blocks(open(pcm_path, 'rb').read())
    nblocks = len(blocks)
    print(f"{nframes} video frames, {nblocks} audio blocks")

    avih = struct.pack('<14I', USEC, 262144, 0, 0x10010, nframes, 0, 2, 262144,
                       320, 240, 0, 0, 0, 0)
    strh_v = struct.pack('<4s4sIHHIIIIIIIIhhhh', b'vids', b'MJPG', 0, 0, 0, 0,
                         USEC, 1000000, 0, 2, 0, 0, 0, 0, 0, 320, 240)
    strf_v = struct.pack('<IiiHH4sIiiII', 40, 320, 240, 1, 24, b'MJPG',
                         230400, 0, 0, 0, 0)
    strh_a = struct.pack('<4s4sIHHIIIIIIIIhhhh', b'auds', b'\0\0\0\0', 0, 0, 0, 0,
                         BLK, 11075, 0, nblocks * 386, 386, 0, BLK, 0, 0, 0, 0)
    strf_a = struct.pack('<HHIIHHH', 0x11, 1, RATE, 11075, BLK, 4, 2) + struct.pack('<H', SPB)
    hdrl = list_chunk(b'hdrl',
                      chunk(b'avih', avih)
                      + list_chunk(b'strl', chunk(b'strh', strh_v) + chunk(b'strf', strf_v))
                      + list_chunk(b'strl', chunk(b'strh', strh_a) + chunk(b'strf', strf_a)))

    movi = bytearray(b'movi')
    idx = bytearray()
    off = 4
    pairs = min(nframes, nblocks)
    for k in range(pairs):
        for cid, dat in ((b'00dc', frames[k]), (b'01wb', blocks[k])):
            c = chunk(cid, dat)
            movi += c
            idx += struct.pack('<4sIII', cid, 0x10, off, len(dat) + pad4(len(dat)))
            off += len(c)

    body = hdrl + b'LIST' + struct.pack('<I', len(movi)) + bytes(movi) \
           + b'idx1' + struct.pack('<I', len(idx)) + bytes(idx)
    out = b'RIFF' + struct.pack('<I', 4 + len(body)) + b'AVI ' + body
    open(out_path, 'wb').write(out)
    print(f"wrote {out_path}: {len(out)} bytes")

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3])
