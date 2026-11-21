"""Minimal UBI + UBIFS extractor (pure Python, LZO/zlib/none).
Usage: python3 -I ubifs_extract.py <image> <ubi_offset> <ubi_size> <outdir>
Scans all UBIFS nodes linearly and keeps the newest (highest sqnum) of each.
Symlinks and device nodes are listed in <outdir>/_special.txt, not created."""
import sys, os, struct, zlib, stat

PEB = 0x20000

def lzo1x_decompress(src, out_len):
    out = bytearray()
    ip = 0
    state = 0
    n = len(src)

    def copy_match(dist, length):
        start = len(out) - dist
        if start < 0:
            raise ValueError("lookbehind")
        if dist >= length:
            out.extend(out[start:start + length])
        else:
            for i in range(length):
                out.append(out[start + i])

    def run_len(t, base):
        nonlocal ip
        while src[ip] == 0:
            t += 255
            ip += 1
        t += base + src[ip]
        ip += 1
        return t

    if src[0] > 17:
        t = src[0] - 17
        ip = 1
        if t < 4:
            out.extend(src[ip:ip + t]); ip += t; state = t
        else:
            out.extend(src[ip:ip + t]); ip += t; state = 4
    while ip < n:
        t = src[ip]; ip += 1
        if t < 16:
            if state == 0:
                if t == 0:
                    t = run_len(0, 15)
                t += 3
                out.extend(src[ip:ip + t]); ip += t
                state = 4
                continue
            elif state != 4:
                nxt = t & 3
                dist = 1 + (t >> 2) + (src[ip] << 2); ip += 1
                copy_match(dist, 2)
            else:
                nxt = t & 3
                dist = 1 + 0x800 + (t >> 2) + (src[ip] << 2); ip += 1
                copy_match(dist, 3)
        elif t >= 64:
            nxt = t & 3
            dist = 1 + ((t >> 2) & 7) + (src[ip] << 3); ip += 1
            copy_match(dist, (t >> 5) + 1)
        elif t >= 32:
            length = (t & 31) + 2
            if length == 2:
                length = run_len(2, 31)
            v = src[ip] | (src[ip + 1] << 8); ip += 2
            copy_match(1 + (v >> 2), length)
            nxt = v & 3
        else:
            hi = (t & 8) << 11
            length = (t & 7) + 2
            if length == 2:
                length = run_len(2, 7)
            v = src[ip] | (src[ip + 1] << 8); ip += 2
            dist = hi + (v >> 2)
            if dist == 0:
                break  # EOF marker
            copy_match(dist + 0x4000, length)
            nxt = v & 3
        state = nxt
        if nxt:
            out.extend(src[ip:ip + nxt]); ip += nxt
    return bytes(out[:out_len])


def decompress(ct, data, size):
    if ct == 0:
        return data[:size]
    if ct == 1:
        return lzo1x_decompress(data, size)
    if ct == 2:
        return zlib.decompressobj(-15).decompress(data)[:size]
    raise ValueError("compr %d" % ct)


def ubi_volumes(img, off, size):
    vols = {}  # vol_id -> {lnum: (sqnum, data)}
    for p in range(off, off + size, PEB):
        if img[p:p + 4] != b'UBI#':
            continue
        vid_off = struct.unpack('>I', img[p + 16:p + 20])[0]
        data_off = struct.unpack('>I', img[p + 20:p + 24])[0]
        v = img[p + vid_off:p + vid_off + 64]
        if v[:4] != b'UBI!':
            continue
        vol_id, lnum = struct.unpack('>II', v[8:16])
        sqnum = struct.unpack('>Q', v[40:48])[0]
        data = img[p + data_off:p + PEB]
        cur = vols.setdefault(vol_id, {}).get(lnum)
        if cur is None or cur[0] < sqnum:
            vols[vol_id][lnum] = (sqnum, data)
    names = {}
    if 0x7fffefff in vols:
        vt = vols[0x7fffefff][0][1]
        for i in range(128):
            r = vt[i * 172:(i + 1) * 172]
            nl = struct.unpack('>H', r[14:16])[0]
            if nl:
                names[i] = r[16:16 + nl].decode(errors='replace')
    return vols, names


def scan_ubifs(lebs):
    inos, dents, datas = {}, {}, {}
    for lnum in sorted(lebs):
        buf = lebs[lnum][1]
        pos = 0
        while pos + 24 <= len(buf):
            if buf[pos:pos + 4] != b'\x31\x18\x10\x06':
                pos += 8
                continue
            sq, ln, nt = struct.unpack('<QIB', buf[pos + 8:pos + 21])
            if ln < 24 or pos + ln > len(buf):
                pos += 8
                continue
            node = buf[pos:pos + ln]
            if nt in (0, 1, 2):
                inum, kv = struct.unpack('<II', node[24:32])
                if nt == 0:
                    if inum not in inos or inos[inum][0] < sq:
                        inos[inum] = (sq, node)
                elif nt == 1:
                    blk = kv & 0x1fffffff
                    k = (inum, blk)
                    if k not in datas or datas[k][0] < sq:
                        datas[k] = (sq, node)
                else:
                    nlen = struct.unpack('<H', node[50:52])[0]
                    name = node[56:56 + nlen]
                    k = (inum, name)
                    if k not in dents or dents[k][0] < sq:
                        dents[k] = (sq, node)
            pos += (ln + 7) & ~7
    return inos, dents, datas


def safe_name(n):
    s = n.decode('utf-8', 'replace')
    if s in ('', '.', '..') or '/' in s or '\0' in s:
        return None
    return s


def extract(img, off, size, outdir):
    vols, names = ubi_volumes(img, off, size)
    for vid, lebs in vols.items():
        if vid == 0x7fffefff:
            continue
        vname = names.get(vid, 'vol%d' % vid)
        vdir = os.path.join(outdir, vname)
        os.makedirs(vdir, exist_ok=True)
        inos, dents, datas = scan_ubifs(lebs)
        children = {}
        for (parent, name), (sq, node) in dents.items():
            tgt = struct.unpack('<Q', node[40:48])[0]
            if tgt == 0:
                continue  # deletion entry
            children.setdefault(parent, []).append((name, tgt))
        special = []
        nfiles = 0

        def file_data(inum, fsize):
            out = bytearray()
            blk = 0
            while len(out) < fsize:
                dn = datas.get((inum, blk))
                if dn is None:
                    out.extend(b'\0' * min(4096, fsize - len(out)))
                else:
                    node = dn[1]
                    usize, ct = struct.unpack('<IH', node[40:46])
                    try:
                        chunk = decompress(ct, node[48:], usize)
                    except Exception as e:
                        chunk = b'\0' * usize
                        special.append('DECOMP-ERROR ino %d blk %d: %s' % (inum, blk, e))
                    out.extend(chunk.ljust(4096, b'\0'))
                blk += 1
            return bytes(out[:fsize])

        def walk(inum, path, depth=0):
            nonlocal nfiles
            if depth > 40:
                return
            for name, tgt in sorted(children.get(inum, [])):
                sn = safe_name(name)
                if sn is None:
                    continue
                p = os.path.join(path, sn)
                ino = inos.get(tgt)
                if ino is None:
                    continue
                node = ino[1]
                fsize = struct.unpack('<Q', node[48:56])[0]
                mode = struct.unpack('<I', node[104:108])[0]
                dlen = struct.unpack('<I', node[112:116])[0]
                rel = os.path.relpath(p, vdir)
                if stat.S_ISDIR(mode):
                    os.makedirs(p, exist_ok=True)
                    walk(tgt, p, depth + 1)
                elif stat.S_ISREG(mode):
                    with open(p, 'wb') as f:
                        f.write(file_data(tgt, fsize))
                    nfiles += 1
                elif stat.S_ISLNK(mode):
                    special.append('LINK %s -> %s' % (rel, node[160:160 + dlen].decode(errors='replace')))
                else:
                    special.append('NODE %s mode %o' % (rel, mode))
        walk(1, vdir)
        with open(os.path.join(vdir, '_special.txt'), 'w') as f:
            f.write('\n'.join(special))
        print('volume %d (%s): %d files, %d specials' % (vid, vname, nfiles, len(special)))


if __name__ == '__main__':
    img = open(sys.argv[1], 'rb').read()
    extract(img, int(sys.argv[2], 0), int(sys.argv[3], 0), sys.argv[4])
