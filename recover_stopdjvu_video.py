#!/usr/bin/env python3
r"""
recover_stopdjvu_video.py — Recover MP4 / MOV / 3GP videos encrypted by
STOP/Djvu ransomware (new variant, post-2019, ".maas" and similar extensions).

How it works
------------
This ransomware variant only Salsa20-encrypts the FIRST 153,600 bytes of a
file (plus stores a 5-byte prefix in plaintext). Everything after byte
153,605 is untouched original data.

Video files are typically megabytes large, so ~all of the media survives.
If the MP4 index (the "moov" atom) is NOT inside the destroyed first 150 KB
— which is the common layout, since phones/cameras usually write it at the
END of the file — we can rebuild a playable file:

    new ftyp header  +  intact media data  +  original index (offsets fixed)

What you lose: only the first ~1 second of the clip.

Output layout
-------------
Outputs always go NEXT TO THE PROGRAM (the folder containing the exe, or
the source folder when run from Python), never near the selected folder:

    <exe folder>\output\
        _manifest.json        <- one shared skip-state manifest
        <SEL folder name>\
            recovered\  <- H.264 version of every input: legacy-codec
                            videos are converted, .maas files are recovered
                            and converted, other videos are copied as-is
            upscaled\   <- AI-upscaled versions (when enabled)

File names: <name>_H264.mp4 for conversions, the original name for copies;
duplicates get _2, _3... Existing outputs are never overwritten (the file
is skipped instead). When upscaling is enabled the recovered\ folder is
NOT written - conversion happens silently in a temp file and only the
upscaled\ output is kept.

Usage
-----
  python recover_stopdjvu_video.py file1.maas [file2.maas ...]
  python recover_stopdjvu_video.py --folder "C:\\Users\\you\\Downloads"
  python recover_stopdjvu_video.py --folder "D:\\" --recursive
  python recover_stopdjvu_video.py --folder "C:\\Videos" --convert
      ^--convert also re-encodes results to H.264 + AAC (web/social ready);
        needs ffmpeg on PATH (or set FFMPEG=C:\\path\\to\\ffmpeg.exe)
"""

import argparse
import hashlib
import json
import os
import re
import struct
import shutil
import subprocess
import sys
import threading
import time

# Spawn child processes (ffmpeg) without a console window on Windows
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ------------------------------------------------- external process tracking
# Long-running external child processes (ffmpeg, Real-ESRGAN, the SeedVR2
# venv python) are registered here so terminate_all() can kill them -
# including their whole process tree - when the app window is closed.
_active_procs = []
_active_lock = threading.Lock()


def _track(p):
    with _active_lock:
        _active_procs.append(p)


def _untrack(p):
    with _active_lock:
        try:
            _active_procs.remove(p)
        except ValueError:
            pass


def _run_tracked(cmd, **kw):
    """Drop-in for subprocess.run() that registers the child in
    _active_procs so terminate_all() can kill it if the app is closed
    mid-run. Returns a CompletedProcess exactly like subprocess.run()."""
    if kw.pop("capture_output", False):
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.PIPE)
    kw.setdefault("creationflags", NO_WINDOW)
    p = subprocess.Popen(cmd, **kw)
    _track(p)
    try:
        out, err = p.communicate()
        return subprocess.CompletedProcess(cmd, p.returncode, out, err)
    finally:
        _untrack(p)


def terminate_all():
    """Force-kill every tracked external child process (whole process
    tree). Called by the GUI when the window is closed. Safe to call
    from any thread and multiple times; never raises."""
    with _active_lock:
        procs = list(_active_procs)
        _active_procs.clear()
    killed = 0
    for p in procs:
        if p.poll() is not None:
            continue
        try:
            # /F /T kills the entire tree: the SeedVR2 venv python and its
            # spawned children, ffmpeg, the ESRGAN binary, ...
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, creationflags=NO_WINDOW,
                           timeout=15)
            killed += 1
        except Exception:
            try:
                p.kill()
                killed += 1
            except Exception:
                pass
        try:
            p.wait(timeout=10)
        except Exception:
            pass
    if killed:
        log("cleanup: terminated %d external process(es) on app close"
            % killed)


# ------------------------------------------------------------- logging
LOG_NAME = "recovery_log.txt"
_log_enabled = False
_log_fh = None
_log_path = None


def project_root():
    """Folder of the running program (exe folder when frozen, source
    folder when run from Python). The log file lives here."""
    if getattr(sys, "frozen", False):  # PyInstaller
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def setup_logging(enabled=True):
    """Open (append) the log file in the project folder root. Returns the
    path, or None when logging is disabled or the file cannot be opened."""
    global _log_enabled, _log_fh, _log_path
    _log_enabled = bool(enabled)
    if _log_fh is not None:
        try:
            _log_fh.close()
        except Exception:
            pass
        _log_fh = None
    _log_path = None
    if not _log_enabled:
        return None
    _log_path = os.path.join(project_root(), LOG_NAME)
    try:
        _log_fh = open(_log_path, "a", encoding="utf-8", buffering=1)
    except OSError:
        _log_fh = None
        _log_enabled = False
        _log_path = None
    return _log_path


def log(msg):
    """Append a timestamped line to the log file (no-op when disabled)."""
    if not _log_enabled or _log_fh is None:
        return
    try:
        _log_fh.write("[%s] %s\n"
                      % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def current_log_path():
    """Path of the active log file, or None when logging is off."""
    return _log_path if _log_enabled else None

MARKER = b"{36A698B9-D67C-4E07-BE82-0EC5B14B4DF5}"   # 38-byte STOP/Djvu signature
FOOTER = 256 + 40 + 38                               # RSA + PID + marker = 334
PREFIX = 5                                           # plaintext prefix bytes
ENC_LEN = 153600                                     # encrypted region length
INTACT_START = PREFIX + ENC_LEN                      # 153605

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl",
              b"edts", b"dinf", b"mvex", b"udta"}

FTYP_ISOM = struct.pack(">I", 28) + b"ftypisom" + struct.pack(">I", 2) + b"isomiso2mp41"
FTYP_3GP = struct.pack(">I", 28) + b"ftyp3gp1" + struct.pack(">I", 0) + b"3gp13gp2mp41"

RAW_SUFFIX = "_RECOVERED"
H264_SUFFIX = "_H264"
H264_EXT = ".mp4"
VIDEO_EXTS = (".mp4", ".3gp", ".mov")
NORMAL_VIDEO_EXTS = (".mp4",)   # regular files scanned as videos (for now)
LEGACY_CODECS = ("mpeg4",)      # old codecs that get H.264-converted
                                # (MPEG-4 Part 2 / Xvid / DivX - the codec
                                # recovered .maas files carry)


def ftyp_for(ext):
    """ftyp box matching the container (3GP gets a 3gp1 brand)."""
    return FTYP_3GP if ext == ".3gp" else FTYP_ISOM


# ---------------------------------------------------------------- paths
def _source_subfolder(selected_folder):
    """Name of the per-source subfolder under output\\. Uses the
    selected folder's name; a drive root (e.g. D:\\) falls back to the
    drive letter."""
    sel = os.path.normpath(os.path.abspath(selected_folder))
    name = os.path.basename(sel)
    if name:
        return name
    drive = os.path.splitdrive(sel)[0]          # e.g. "D:\\"
    return drive[:-1] if drive else sel         # "D"


def get_output_dirs(selected_folder):
    """Return (recovered_dir, upscaled_dir) - inside
    <program folder>\\output\\<SEL>\\. The recovered subfolder holds the
    H.264 version of every input (converted, copied, or .maas-recovered);
    the upscaled subfolder holds the AI-upscaled versions. The program's
    own folder (exe folder when frozen, source folder when run from
    Python) is always the anchor, so outputs never depend on where the
    selected folder lives."""
    sub = os.path.join(project_root(), "output", _source_subfolder(selected_folder))
    return os.path.join(sub, "recovered"), os.path.join(sub, "upscaled")


def ensure_output_dirs(selected_folder, with_upscaled=False):
    rec, up = get_output_dirs(selected_folder)
    os.makedirs(rec, exist_ok=True)
    if with_upscaled:
        os.makedirs(up, exist_ok=True)
    return rec, up


MANIFEST_NAME = "_manifest.json"


def _manifest_path(selected_folder=None):
    """One shared manifest for all source folders:
    <exe folder>\\output\\_manifest.json."""
    return os.path.join(project_root(), "output", MANIFEST_NAME)


def load_manifest(selected_folder):
    """Map of source-file MD5 -> {'recovered': path, 'upscaled': {...}}."""
    p = _manifest_path(selected_folder)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_manifest(selected_folder, manifest):
    p = _manifest_path(selected_folder)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)


def file_fingerprint(path):
    """MD5 of the file content (fast even for large files)."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def base_stem(path):
    """'a.mp4.maas' -> ('a', '.mp4')  (folder stripped, .maas removed,
    original video extension detected, .mp4 as fallback)"""
    n = os.path.basename(path)
    if n.lower().endswith(".maas"):
        n = n[:-5]
    stem, ext = os.path.splitext(n)
    ext = ext.lower()
    if ext not in VIDEO_EXTS:
        ext = H264_EXT
    return stem, ext


def find_existing(directory, stem, ext):
    """Return an existing <stem><ext> / <stem>_N<ext> in directory, else None."""
    if not os.path.isdir(directory):
        return None
    pat = re.compile(re.escape(stem) + r"(_\d+)?" + re.escape(ext))
    for n in sorted(os.listdir(directory)):
        if pat.fullmatch(n):
            return os.path.join(directory, n)
    return None


def unique_path(directory, stem, ext):
    """Non-colliding path: stem<ext>, stem_2<ext>, stem_3<ext> ..."""
    cand = os.path.join(directory, stem + ext)
    n = 2
    while os.path.exists(cand):
        cand = os.path.join(directory, "%s_%d%s" % (stem, n, ext))
        n += 1
    return cand


# ---------------------------------------------------------------- engine
def find_boxes(buf, target):
    """Offsets (within buf) of every box of type `target`, recursing into
    container boxes."""
    out = []

    def walk(b, base):
        i = 0
        while i + 8 <= len(b):
            sz = struct.unpack(">I", b[i:i + 4])[0]
            typ = b[i + 4:i + 8]
            if sz < 8 or i + sz > len(b):
                return
            if typ == target:
                out.append(base + i)
            elif typ in CONTAINERS:
                walk(b[i + 8:i + sz], base + i + 8)
            i += sz

    walk(buf, 0)
    return out


def find_chunk_tables(moov):
    """Return [(offset_in_moov, kind)] for every stco/co64 box inside moov."""
    return ([(o, b"stco") for o in find_boxes(moov, b"stco")] +
            [(o, b"co64") for o in find_boxes(moov, b"co64")])


def patch_chunk_offsets(moov, tables, delta, floor):
    """Shift every chunk offset by delta (clamped to floor). Returns new bytes."""
    mo = bytearray(moov)
    for off, kind in tables:
        count = struct.unpack(">I", mo[off + 12:off + 16])[0]
        if kind == b"stco":
            for k in range(count):
                p = off + 16 + 4 * k
                v = struct.unpack(">I", mo[p:p + 4])[0]
                struct.pack_into(">I", mo, p, max(floor, v + delta))
        else:  # co64
            for k in range(count):
                p = off + 16 + 8 * k
                v = struct.unpack(">Q", mo[p:p + 8])[0]
                struct.pack_into(">Q", mo, p, max(floor, v + delta))
    return bytes(mo)


def locate_moov(tail):
    """Find a valid moov box in the intact tail. Returns (start, size) or None."""
    pos = 0
    while True:
        i = tail.find(b"moov", pos)
        if i < 4:
            return None
        size = struct.unpack(">I", tail[i - 4:i])[0]
        if 8 <= size <= len(tail) - (i - 4):
            return i - 4, size
        pos = i + 1


def _rebuild_box(buf, off, size, replace_by_off, replace_by_type):
    """Rebuild the box at buf[off:off+size], substituting new bytes for
    children matched by offset (within buf) or by type. Container children
    are recursed into. The box size field is recomputed."""
    out = bytearray(buf[off:off + 8])
    i, end = off + 8, off + size
    while i < end:
        csz = struct.unpack(">I", buf[i:i + 4])[0]
        ctyp = buf[i + 4:i + 8]
        if csz < 8 or i + csz > end:
            break
        if i in replace_by_off:
            out += replace_by_off[i]
        elif ctyp in replace_by_type:
            out += replace_by_type[ctyp]
        elif ctyp in CONTAINERS:
            out += _rebuild_box(buf, i, csz, replace_by_off, replace_by_type)
        else:
            out += buf[i:i + csz]
        i += csz
    struct.pack_into(">I", out, 0, len(out))
    return bytes(out)


def rebuild_trimmed(src, dst):
    """Last-resort rebuild, straight from the .maas file. The intact mdat
    data starts in the MIDDLE of a frame, and many streams (H.264 with
    reference frames) cannot decode from there. This trims the media to the
    first COMPLETE surviving KEYFRAME (via the stss sync-sample table) and
    rewrites the index tables (stco/stsc/stsz/stts/ctts/stss + box sizes)
    to match, producing a file whose stream starts cleanly. Fails clearly
    if no keyframe survived - such a stream is undecodable."""
    try:
        data = open(src, "rb").read()
        if data[-38:] != MARKER:
            return False, "not a STOP/Djvu file"
        orig_size = len(data) - FOOTER
        tail = data[INTACT_START:orig_size]
        found = locate_moov(tail)
        if found is None:
            return False, "no moov in intact data"
        moov_start, moov_size = found
        moov = tail[moov_start:moov_start + moov_size]
        mdat_data = tail[:moov_start]
        trailing = tail[moov_start + moov_size:]

        tracks = []
        for toff in find_boxes(moov, b"trak"):
            tsize = struct.unpack(">I", moov[toff:toff + 4])[0]
            t = moov[toff:toff + tsize]
            h = find_boxes(t, b"hdlr")
            kind = t[h[0] + 16:h[0] + 20].decode("latin-1") if h else ""
            stco, stsc, stsz = (find_boxes(t, b"stco"),
                                find_boxes(t, b"stsc"),
                                find_boxes(t, b"stsz"))
            if stco and stsc and stsz:
                tracks.append({"toff": toff, "tsize": tsize, "kind": kind,
                               "buf": t, "stco": stco[0], "stsc": stsc[0],
                               "stsz": stsz[0]})
        video = next((t for t in tracks if t["kind"] == "vide"), None)
        if video is None:
            return False, "no video track with index tables"

        def tables(t):
            """chunks, per-sample offsets, per-sample sizes, per-chunk sample
            counts, stsz entry count. Offsets are ORIGINAL (absolute in the
            pre-encryption file)."""
            buf = t["buf"]
            stco, stsc, stsz = t["stco"], t["stsc"], t["stsz"]
            cc = struct.unpack(">I", buf[stco + 12:stco + 16])[0]
            chunks = list(struct.unpack(">%dI" % cc, buf[stco + 16:stco + 16 + 4 * cc]))
            e = struct.unpack(">I", buf[stsc + 12:stsc + 16])[0]
            spc = [struct.unpack(">II", buf[stsc + 16 + i * 12:stsc + 24 + i * 12])
                   for i in range(e)]
            per_chunk = []
            for ci in range(cc):
                sp = spc[0][1] if spc else 1
                for fc, n in spc:
                    if fc <= ci + 1:
                        sp = n
                per_chunk.append(sp)
            sc = struct.unpack(">I", buf[stsz + 16:stsz + 20])[0]
            if sc:
                sizes = list(struct.unpack(">%dI" % sc,
                                           buf[stsz + 20:stsz + 20 + 4 * sc]))
            else:  # default-sample-size form (all samples same size)
                default = struct.unpack(">I", buf[stsz + 12:stsz + 16])[0]
                sizes = [default] * sum(per_chunk)
            offs, si = [], 0
            for ci in range(cc):
                pos = chunks[ci]
                for _ in range(per_chunk[ci]):
                    offs.append(pos)
                    if si < len(sizes):
                        pos += sizes[si]
                    si += 1
            return chunks, offs, sizes, per_chunk, sc

        v_chunks, v_offs, v_sizes, v_per_chunk, v_sc = tables(video)
        if v_sc == 0:
            return False, "video uses a default-size sample table - trim rebuild unsupported"

        # Cut point: first COMPLETE surviving KEYFRAME (stss list); if the
        # file has no stss box every sample is a sync sample, so the first
        # complete surviving sample is fine.
        vbuf = video["buf"]
        stss_offs = find_boxes(vbuf, b"stss")
        if stss_offs:
            o = stss_offs[0]
            n = struct.unpack(">I", vbuf[o + 12:o + 16])[0]
            sync = set(struct.unpack(">%dI" % n, vbuf[o + 16:o + 16 + 4 * n]))
            cands = [i for i in range(len(v_offs))
                     if (i + 1) in sync and v_offs[i] >= INTACT_START]
            if not cands:
                return False, ("no surviving keyframe - the destroyed header held "
                               "the stream's only sync point, it cannot be decoded")
            k = cands[0]
        else:
            cands = [i for i in range(len(v_offs)) if v_offs[i] >= INTACT_START]
            if not cands:
                return False, "no complete video sample survived"
            k = cands[0]
        S = v_offs[k]                          # original offset of cut point

        new_data = mdat_data[S - INTACT_START:]

        # ---- rewrite the index tables ----
        mo2 = bytearray(moov)
        # stco: shift every chunk of every track
        for t in tracks:
            base = t["toff"]
            chunks, _o, _s, _pc, _sc = tables(t)
            for ci, c in enumerate(chunks):
                p = base + t["stco"] + 16 + 4 * ci
                struct.pack_into(">I", mo2, p, 36 if c < S else c - S + 36)

        replacements = {}
        # stsz: drop the k leading samples
        new_sizes = v_sizes[k:]
        replacements[b"stsz"] = (struct.pack(">I", 20 + 4 * len(new_sizes)) + b"stsz"
                                 + struct.pack(">II", 0, len(new_sizes))
                                 + struct.pack(">%dI" % len(new_sizes), *new_sizes))
        # stss: drop entries <= k, renumber the rest
        if stss_offs:
            o = stss_offs[0]
            n = struct.unpack(">I", vbuf[o + 12:o + 16])[0]
            nums = list(struct.unpack(">%dI" % n, vbuf[o + 16:o + 16 + 4 * n]))
            keep = [x - k for x in nums if x > k]
            replacements[b"stss"] = (struct.pack(">I", 16 + 4 * len(keep)) + b"stss"
                                     + struct.pack(">II", 0, len(keep))
                                     + struct.pack(">%dI" % len(keep), *keep))
        # stts / ctts: subtract k from the first (count, value) entry
        for boxname in (b"stts", b"ctts"):
            boffs = find_boxes(vbuf, boxname)
            if not boffs:
                continue
            o = boffs[0]
            n = struct.unpack(">I", vbuf[o + 12:o + 16])[0]
            ent = [struct.unpack(">II", vbuf[o + 16 + i * 8:o + 24 + i * 8])
                   for i in range(n)]
            ent[0] = (max(0, ent[0][0] - k), ent[0][1])
            if ent[0][0] == 0:
                ent = ent[1:]
            body = b"".join(struct.pack(">II", a, b) for a, b in ent)
            replacements[boxname] = (struct.pack(">I", 16 + len(body)) + boxname
                                     + struct.pack(">II", 0, len(ent)) + body)
        # stsc: chunk 1 loses the samples dropped from it (0 is valid -
        # the chunk simply carries no samples)
        dropped_c1 = min(k, v_per_chunk[0]) if v_per_chunk else 0
        if dropped_c1:
            o = video["stsc"]
            e = struct.unpack(">I", vbuf[o + 12:o + 16])[0]
            entries = [struct.unpack(">III", vbuf[o + 16 + i * 12:o + 28 + i * 12])
                       for i in range(e)]
            entries[0] = (entries[0][0], max(0, entries[0][1] - dropped_c1),
                          entries[0][2])
            body = b"".join(struct.pack(">III", *x) for x in entries)
            replacements[b"stsc"] = (struct.pack(">I", 16 + 12 * len(entries))
                                     + b"stsc" + struct.pack(">II", 0, len(entries))
                                     + body)

        # Reassemble stbl -> trak -> moov with corrected box sizes
        vbuf2 = bytes(mo2[video["toff"]:video["toff"] + video["tsize"]])
        stbl_off = find_boxes(vbuf2, b"stbl")[0]
        stbl_sz = struct.unpack(">I", vbuf2[stbl_off:stbl_off + 4])[0]
        new_stbl = _rebuild_box(vbuf2, stbl_off, stbl_sz, {}, replacements)
        new_trak = _rebuild_box(bytes(mo2), video["toff"], video["tsize"],
                                {stbl_off: new_stbl}, {})
        new_moov = _rebuild_box(bytes(mo2), 0, moov_size,
                                {video["toff"]: new_trak}, {})

        with open(dst, "wb") as f:
            f.write(FTYP_ISOM)
            f.write(struct.pack(">I", 8 + len(new_data)) + b"mdat")
            f.write(new_data)
            f.write(new_moov)
            f.write(trailing)
        return True, ("trimmed to first surviving keyframe (sample %d, "
                      "%d bytes dropped)" % (k + 1, S - INTACT_START))
    except Exception as e:
        return False, "trim rebuild error: %s" % e


def recover_one(src, raw_dir):
    """Recover one STOP/Djvu-encrypted video into raw_dir (flat).
    Does NOT skip on its own - the caller decides via the manifest.
    Returns a result dict. Never raises."""
    res = {"file": src, "name": os.path.basename(src), "ok": False,
           "reason": "", "raw_out": None, "skipped_raw": False}
    try:
        size = os.path.getsize(src)
        log("recover %s: size=%d B" % (res["name"], size))
        data = open(src, "rb").read()

        if data[-38:] != MARKER:
            res["reason"] = "not a STOP/Djvu file (signature missing)"
            log("recover %s: FAIL: %s" % (res["name"], res["reason"]))
            return res
        if size < INTACT_START + FOOTER + 1000:
            res["reason"] = "file too small (%d B) - no intact data survived" % size
            log("recover %s: FAIL: %s" % (res["name"], res["reason"]))
            return res

        orig_size = size - FOOTER
        tail = data[INTACT_START:orig_size]
        log("recover %s: intact tail=%d B, original size=%d B"
            % (res["name"], len(tail), orig_size))

        found = locate_moov(tail)
        if found is None:
            res["reason"] = ("index (moov) is inside the destroyed first 150 KB "
                             "- cannot be rebuilt this way")
            log("recover %s: FAIL: %s" % (res["name"], res["reason"]))
            return res

        moov_start, moov_size = found
        log("recover %s: moov found at tail+%d (size=%d B)"
            % (res["name"], moov_start, moov_size))
        mdat_data = tail[:moov_start]
        moov = tail[moov_start:moov_start + moov_size]
        trailing = tail[moov_start + moov_size:]

        tables = find_chunk_tables(moov)
        if not tables:
            res["reason"] = "index found but contains no chunk offsets - incomplete"
            log("recover %s: FAIL: %s" % (res["name"], res["reason"]))
            return res

        stem, ext = base_stem(src)
        ftyp = ftyp_for(ext)
        new_data_start = len(ftyp) + 8
        delta = new_data_start - INTACT_START
        moov_fixed = patch_chunk_offsets(moov, tables, delta, new_data_start)

        out = unique_path(raw_dir, stem + RAW_SUFFIX, ext)
        mdat_hdr = struct.pack(">I", 8 + len(mdat_data)) + b"mdat"
        with open(out, "wb") as f:
            f.write(ftyp)
            f.write(mdat_hdr)
            f.write(mdat_data)
            f.write(moov_fixed)
            f.write(trailing)

        res.update(ok=True, raw_out=out,
                   reason="recovered (%d chunk offsets fixed, first ~1 s lost)"
                          % sum(struct.unpack(">I", moov[o + 12:o + 16])[0]
                                for o, _k in tables))
        log("recover %s: OK -> %s (%s)" % (res["name"], out, res["reason"]))
    except Exception as e:  # never let one bad file kill a batch
        res["reason"] = "error: %s" % e
        log("recover %s: FAIL: exception: %s" % (res["name"], e))
    return res


def find_ffmpeg():
    """Bundled copy first, then PATH. Returns path or None."""
    cand = os.environ.get("FFMPEG")
    if cand and os.path.exists(cand):
        return cand
    if getattr(sys, "_MEIPASS", None):  # PyInstaller onedir
        p = os.path.join(sys._MEIPASS, "ffmpeg", "ffmpeg.exe")
        if os.path.exists(p):
            return p
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg", "ffmpeg.exe")
    if os.path.exists(p):
        return p
    return shutil.which("ffmpeg")


def convert_h264(src, out, maas_src=None):
    """Re-encode to H.264 + AAC in an MP4 container (web compatible).
    Works from any source codec/container (MPEG-4, H.263, AMR, ...).

    NOTE: the recovered file's first ~1 s is torn, so ffmpeg may report
    decode errors at the start and exit non-zero while still producing a
    perfectly playable (slightly shorter) output. Success is therefore
    defined as "output exists and is non-empty", not the exit code.

    If the H.264 parser cannot resync at all (0-byte output), a fallback
    rebuilds the file with the data trimmed to the first COMPLETE video
    sample (needs the original .maas file via maas_src)."""
    log("h264: %s -> %s" % (src, out))
    ff = find_ffmpeg()
    if not ff:
        log("h264: FAIL: ffmpeg not found")
        return False, "ffmpeg not found (put ffmpeg.exe in the app's ffmpeg\\ folder or on PATH)"

    def attempt(inp):
        cmd = [ff, "-y", "-v", "error", "-i", inp,
               "-c:v", "libx264", "-preset", "medium", "-crf", "23",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", out]
        log("h264: ffmpeg attempt: %s" % inp)
        r = _run_tracked(cmd, capture_output=True, text=True)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            log("h264: attempt OK (%d B)" % os.path.getsize(out))
            return True, ""
        try:
            if os.path.exists(out):
                os.remove(out)
        except OSError:
            pass
        log("h264: attempt FAILED: %s" % (r.stderr or "").strip()[:200])
        return False, (r.stderr or "").strip()[:300]

    ok, msg = attempt(src)
    if ok:
        log("h264: OK -> %s" % out)
        return True, ""

    if maas_src:
        tmp = out + "_trim.mp4"
        log("h264: trying keyframe-trim fallback from %s" % maas_src)
        tok, tmsg = rebuild_trimmed(maas_src, tmp)
        if tok:
            ok2, msg2 = attempt(tmp)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            if ok2:
                log("h264: OK via trim fallback -> %s" % out)
                return True, ""
            msg = "%s; fallback: %s" % (tmsg, msg2[:150])
        else:
            msg = "%s; fallback unavailable: %s" % (msg[:150], tmsg)
    log("h264: FAIL: %s" % msg[:300])
    return False, msg


# ------------------------------------------------- AI upscaling (ESRGAN)
ESRGAN_EXE = "realesrgan-ncnn-vulkan.exe"
UP_TAGS = {"animevideov3": "anime", "x4plus": "x4",
           "seedvr2_3b": "seedvr3b", "seedvr2_3b_gguf": "seedvr3bq8",
           "seedvr2_7b": "seedvr7b", "seedvr2_7b_gguf": "seedvr7bq4"}
# Internal keys -> the exact model names the binary expects with -n.
# (Passing a name it does not know makes the binary crash with an access
# violation instead of a clean error - this bit us once.)
UP_MODELS = {"animevideov3": "realesr-animevideov3",
             "x4plus": "realesrgan-x4plus"}


def find_esrgan():
    r"""Bundled Real-ESRGAN-ncnn-vulkan binary (esrgan\ folder), else PATH."""
    if getattr(sys, "_MEIPASS", None):  # PyInstaller onedir
        p = os.path.join(sys._MEIPASS, "esrgan", ESRGAN_EXE)
        if os.path.exists(p):
            return p
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "esrgan", ESRGAN_EXE)
    if os.path.exists(p):
        return p
    return shutil.which(ESRGAN_EXE)


def list_gpus():
    """[(index, name, vram_mb, util_pct, mem_used_mb)] for every NVIDIA GPU,
    via nvidia-smi. Empty list when no NVIDIA driver is present."""
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                       "nvidia-smi.exe")
    if not os.path.exists(exe):
        exe = shutil.which("nvidia-smi")
    if not exe:
        log("gpu: nvidia-smi not found - no GPU detection possible")
        return []
    try:
        r = subprocess.run(
            [exe, "--query-gpu=index,name,memory.total,utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, creationflags=NO_WINDOW, timeout=20)
        log("gpu: nvidia-smi: %s"
            % (r.stdout or "").replace("\n", " | ").strip())
        out = []
        for line in (r.stdout or "").strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 5:
                out.append((int(parts[0]), parts[1], int(parts[2]),
                            int(parts[3]), int(parts[4])))
        return out
    except Exception as e:
        log("gpu: nvidia-smi error: %s" % e)
        return []


def pick_gpu():
    """(ok, note). ok is False when no NVIDIA GPU is present.

    The card to use is chosen by the ESRGAN binary itself (its default
    Vulkan device 0 - the RTX 5060 Ti on this machine). We deliberately do
    NOT pass -g: the binary's Vulkan device order does not match
    nvidia-smi's index order (here Vulkan 0 = 5060 Ti while nvidia-smi 0
    = 4060), so an nvidia-smi index would select the WRONG card."""
    gpus = list_gpus()
    if not gpus:
        log("gpu: no NVIDIA GPU detected")
        return False, "no NVIDIA GPU detected"
    info = " | ".join("%s (%d MB free)" % (g[1], g[2] - g[4]) for g in gpus)
    log("gpu: %s - using the binary's default GPU (Vulkan device 0)" % info)
    return True, "NVIDIA GPU present - using default GPU"


def probe_fps(path):
    """Frame rate of a video file (parsed from ffmpeg's input banner), or None."""
    ff = find_ffmpeg()
    if not ff:
        return None
    try:
        r = subprocess.run([ff, "-i", path], capture_output=True, text=True,
                           creationflags=NO_WINDOW)
        m = re.search(r"(\d+(?:\.\d+)?)\s+fps", r.stderr or "")
        return float(m.group(1)) if m else None
    except Exception:
        return None


def probe_codec(path):
    """Video codec name of a file (e.g. 'h264', 'mpeg4', 'hevc'), parsed
    from ffmpeg's input banner. None when it cannot be determined."""
    ff = find_ffmpeg()
    if not ff:
        return None
    try:
        r = subprocess.run([ff, "-i", path], capture_output=True, text=True,
                           creationflags=NO_WINDOW)
    except Exception:
        return None
    m = re.search(r"Stream #\d+:\d+[^:]*: Video: (\w+)", r.stderr or "")
    return m.group(1) if m else None


def _run_esrgan(in_path, out_path, model, scale, tile=256,
               extra=None, on_frame=None, total=None):
    """Run the Real-ESRGAN binary on a file or directory. Returns (ok, msg).
    on_frame(done, total) is polled while the process runs.

    tile is always set (default 256): the binary's auto-tile sizes the
    Vulkan heap from the card's TOTAL VRAM, so on a card that another
    program has filled up, auto mode OOMs and produces zero frames. Fixed
    256 px tiles use a few tens of MB and work on a nearly-full card.

    No -g flag: the binary's Vulkan device order does not match
    nvidia-smi's index order, so the binary picks its default GPU."""
    es = find_esrgan()
    if not es:
        log("esrgan: FAIL: binary not found")
        return False, "Real-ESRGAN binary not found (esrgan\\ folder missing)"
    models = os.path.join(os.path.dirname(es), "models")
    if not os.path.isdir(models):
        log("esrgan: FAIL: models folder not found next to %s" % es)
        return False, "Real-ESRGAN models folder not found next to the binary"
    # NOTE: the binary requires the model dir name to contain "models"
    cmd = [es, "-i", in_path, "-o", out_path,
           "-n", UP_MODELS.get(model, model), "-s", str(scale),
           "-m", models, "-t", str(tile)]
    if extra:
        cmd += extra
    log("esrgan: running: %s" % " ".join(cmd))
    # stdout/stderr -> DEVNULL: the binary prints a progress line per frame;
    # reading it through a pipe would fill the 64 KB buffer and deadlock a
    # long run. Progress is tracked by counting output files instead.
    t0 = time.time()
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except OSError as e:
        log("esrgan: FAIL: could not start process: %s" % e)
        return False, "could not start Real-ESRGAN: %s" % e
    _track(p)
    last = -1
    while p.poll() is None:
        if on_frame and total:
            try:
                done = len([n for n in os.listdir(out_path)
                            if n.lower().endswith(".png")])
            except OSError:
                done = 0
            if done != last:
                last = done
                on_frame(done, total)
        time.sleep(0.5)
    _untrack(p)
    dt = time.time() - t0
    log("esrgan: exit code %d after %.1f s" % (p.returncode, dt))
    return p.returncode == 0, ""


def esrgan_preflight(model, scale):
    """Upscale a generated 64x64 test frame; verifies binary + model + GPU.
    Returns (ok, msg)."""
    log("esrgan preflight: model=%s scale=%dx" % (model, scale))
    ff = find_ffmpeg()
    if not ff:
        log("esrgan preflight: FAIL: ffmpeg not found")
        return False, "ffmpeg not found"
    tmp = os.path.join(tempfile_get_tempdir(), "esrgan_preflight")
    os.makedirs(tmp, exist_ok=True)
    tin = os.path.join(tmp, "in.png")
    tout = os.path.join(tmp, "out.png")
    r = subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "testsrc=duration=0.2:size=64x64:rate=10",
                        "-frames:v", "1", tin],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    if not os.path.exists(tin):
        log("esrgan preflight: FAIL: could not generate test frame: %s"
            % (r.stderr or "")[:200])
        return False, "could not generate test frame: " + (r.stderr or "")[:200]
    ok, msg = _run_esrgan(tin, tout, model, scale)
    if ok and not (os.path.exists(tout) and os.path.getsize(tout) > 0):
        ok, msg = False, "test frame produced no output"
    log("esrgan preflight: %s %s" % ("OK" if ok else "FAIL", msg[:200]))
    return ok, msg


def tempfile_get_tempdir():
    return os.environ.get("TEMP") or os.environ.get("TMP") or "."


def upscale_video(src, dst, model, scale, on_frame=None):
    """AI-upscale a video with Real-ESRGAN (ncnn-vulkan).

    Pipeline: ffmpeg -> PNG frames (from the RAW file) -> Real-ESRGAN ->
    ffmpeg H.264 + original audio -> dst MP4. Works from any source codec.
    Returns (ok, msg). Never raises."""
    log("upscale: %s -> %s (model=%s scale=%dx)"
        % (src, dst, model, scale))
    ff = find_ffmpeg()
    if not ff:
        log("upscale: FAIL: ffmpeg not found")
        return False, "ffmpeg not found"
    if find_esrgan() is None:
        log("upscale: FAIL: Real-ESRGAN binary not found")
        return False, "Real-ESRGAN binary not found (esrgan\\ folder missing)"

    tmp = dst + "_frames"
    in_dir = os.path.join(tmp, "in")
    out_dir = os.path.join(tmp, "out")
    try:
        os.makedirs(in_dir, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)

        # 1) extract frames
        log("upscale: extracting frames from %s" % src)
        r = _run_tracked([ff, "-y", "-v", "error", "-i", src,
                          in_dir + "\\frame_%05d.png"],
                         capture_output=True, text=True)
        frames = sorted(n for n in os.listdir(in_dir)
                        if n.lower().endswith(".png"))
        if not frames:
            log("upscale: FAIL: frame extraction produced no frames: %s"
                % (r.stderr or "")[:200])
            return False, "frame extraction failed: " + (r.stderr or "")[:200]
        total = len(frames)
        log("upscale: extracted %d frames" % total)
        if on_frame:
            on_frame(0, total)

        # 2) upscale all frames (directory mode, 256 px tiles)
        ok, msg = _run_esrgan(in_dir, out_dir, model, scale,
                              on_frame=on_frame, total=total)
        if not ok:
            log("upscale: first pass failed - retrying with 128 px tiles")
            # one retry with even smaller tiles (very low free VRAM)
            ok, msg2 = _run_esrgan(in_dir, out_dir, model, scale,
                                   tile=128,
                                   on_frame=on_frame, total=total)
            if not ok:
                log("upscale: FAIL: upscaling failed (both passes): %s | %s"
                    % (msg[:200], msg2[:200]))
                return False, "upscaling failed: %s (tile retry: %s)" % (
                    msg, msg2)
            msg = msg2
        done = len([n for n in os.listdir(out_dir)
                    if n.lower().endswith(".png")])
        log("upscale: %d/%d frames upscaled" % (done, total))
        if done == 0:
            log("upscale: FAIL: upscaling produced no frames: %s" % msg[:200])
            return False, "upscaling produced no frames: " + msg
        if on_frame:
            on_frame(done, total)

        # 3) re-encode frames + original audio (H.264, high quality)
        cmd = [ff, "-y", "-v", "error"]
        fps = probe_fps(src)
        log("upscale: source fps=%s - encoding final MP4" % fps)
        if fps:
            cmd += ["-framerate", "%.4f" % fps]
        cmd += ["-i", out_dir + "\\frame_%05d.png", "-i", src,
                "-map", "0:v", "-map", "1:a?",
                "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", dst]
        r = _run_tracked(cmd, capture_output=True, text=True)
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            note = "" if done == total else " (%d/%d frames upscaled)" % (done, total)
            log("upscale: OK -> %s (%d B)%s"
                % (dst, os.path.getsize(dst), note))
            return True, note
        try:
            if os.path.exists(dst):
                os.remove(dst)
        except OSError:
            pass
        log("upscale: FAIL: final encode failed: %s" % (r.stderr or "")[:300])
        return False, "final encode failed: " + (r.stderr or "")[:300]
    except Exception as e:
        log("upscale: FAIL: exception: %s" % e)
        return False, "upscaling error: %s" % e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------- SeedVR2 (diffusion upscaler)
# Optional second engine: a full PyTorch/CUDA install living in the
# seedvr2\ folder next to the program (installed by setup_seedvr2.bat).
# Much higher quality than ESRGAN (true diffusion restoration) but far
# slower. It is resolution-target based, so "scale" means the target
# short-side resolution in pixels (e.g. 1080).
SEEDVR2_DIR = "seedvr2"
# DiT batch size + streaming chunk size (4n+1, gives chunk-level progress).
# "Normal" is the fastest setting (big cards); "low" trades speed for VRAM
# headroom - 16 GB cards running 1080p / 7B models.
SEEDVR2_BATCH, SEEDVR2_CHUNK = 13, 41
SEEDVR2_BATCH_LOW, SEEDVR2_CHUNK_LOW = 8, 24
SEEDVR2_MODELS = {
    "seedvr2_3b": "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
    "seedvr2_3b_gguf": "seedvr2_ema_3b-Q8_0.gguf",
    "seedvr2_7b": "seedvr2_ema_7b_fp8_e4m3fn_mixed_block35_fp16.safetensors",
    "seedvr2_7b_gguf": "seedvr2_ema_7b-Q4_K_M.gguf",
}


def is_seedvr2(model):
    return str(model).startswith("seedvr2")


def find_seedvr2():
    """Locate the optional seedvr2\\ folder (venv + CLI + models).
    Returns a dict with paths, or None when not installed."""
    root = os.path.join(project_root(), SEEDVR2_DIR)
    py = os.path.join(root, ".venv", "Scripts", "python.exe")
    cli = os.path.join(root, "inference_cli.py")
    models = os.path.join(root, "models", "SEEDVR2")
    if os.path.exists(py) and os.path.exists(cli) and os.path.isdir(models):
        return {"root": root, "python": py, "cli": cli, "models": models}
    return None


def seedvr2_best_gpu():
    """nvidia-smi index of the single GPU with the most total VRAM ("" if
    none). The SeedVR2 CLI's CUDA indices match nvidia-smi order (unlike
    the ESRGAN binary's Vulkan order), so this is safe to pass directly.
    One GPU -> that one; equal VRAM -> lowest index. We deliberately use
    only ONE card: heterogeneous multi-GPU gives well under 2x speedup and
    a single process is far simpler to debug."""
    gpus = list_gpus()
    if not gpus:
        return ""
    best = min(gpus, key=lambda g: (-g[2], g[0]))
    log("seedvr2: using GPU %d (%s, %d MB VRAM)"
        % (best[0], best[1], best[2]))
    return str(best[0])


def _run_seedvr2(sv, in_path, out_path, model_key, target_res,
                 cuda_devices, on_frame=None, debug=False,
                 low_tiling=True, dit_offload=True, vae_offload=True):
    """Run the SeedVR2 CLI on one file. Returns (ok, msg).
    Progress is parsed from the CLI's chunk/tqdm output lines (read in a
    thread so the pipe never deadlocks)."""
    model_file = SEEDVR2_MODELS.get(model_key)
    if not model_file:
        log("seedvr2: FAIL: unknown model key %r" % model_key)
        return False, "unknown SeedVR2 model: %s" % model_key
    if not os.path.exists(os.path.join(sv["models"], model_file)):
        log("seedvr2: FAIL: model file missing: %s" % model_file)
        return False, ("model not downloaded: %s "
                       "(run setup_seedvr2.bat)" % model_file)
    batch, chunk = (SEEDVR2_BATCH_LOW, SEEDVR2_CHUNK_LOW) if low_tiling \
        else (SEEDVR2_BATCH, SEEDVR2_CHUNK)
    cmd = [sv["python"], sv["cli"], in_path,
           "--output", out_path,
           "--model_dir", sv["models"],
           "--dit_model", model_file,
           "--resolution", str(target_res),
           "--batch_size", str(batch),
           "--temporal_overlap", "3",
           "--chunk_size", str(chunk),
           "--vae_encode_tiled", "--vae_decode_tiled",
           "--color_correction", "lab",
           "--cache_dit", "--cache_vae",
           # "sdpa" = PyTorch SDPA, which auto-selects the native
           # flash-attention CUDA kernels (the fastest path available on
           # Windows - the sageattention/flash-attn packages have no
           # Windows wheels)
           "--attention_mode", "sdpa"]
    # CPU offload is a user toggle (GUI, default ON) - it protects
    # smaller cards at the cost of PCIe weight shuffling every step.
    if dit_offload:
        cmd += ["--dit_offload_device", "cpu"]
    if vae_offload:
        cmd += ["--vae_offload_device", "cpu"]
    # GGUF BlockSwap: still automatic for small cards - it keeps the
    # quantized models inside <12 GB VRAM even with offload toggled off.
    best_vram = max((g[2] for g in list_gpus()), default=0)
    if best_vram < 12000 and \
            model_key in ("seedvr2_3b_gguf", "seedvr2_7b_gguf"):
        cmd += ["--blocks_to_swap", "32" if "3b" in model_key else "36",
                "--swap_io_components"]
    log("seedvr2: batch=%d chunk=%d dit_offload=%s vae_offload=%s"
        % (batch, chunk, dit_offload, vae_offload))
    if cuda_devices:
        cmd += ["--cuda_device", cuda_devices]
    if debug:
        cmd += ["--debug"]
    log("seedvr2: running: %s" % " ".join(cmd))
    t0 = time.time()
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"   # the CLI prints emoji at import time
    env["PYTHONUTF8"] = "1"             # which crashes on cp1252 Windows pipes
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace",
                             creationflags=NO_WINDOW, cwd=sv["root"],
                             env=env)
    except OSError as e:
        log("seedvr2: FAIL: could not start process: %s" % e)
        return False, "could not start SeedVR2: %s" % e
    _track(p)

    state = {"done": 0, "total": 0}
    tail = []

    def _reader(proc):
        try:
            for line in proc.stdout:
                tail.append(line)
                if len(tail) > 40:
                    tail.pop(0)
                log("seedvr2: | " + line.rstrip())
                m = re.search(r"Chunk\s+(\d+)\s*/\s*(\d+)", line)
                if m:
                    # Authoritative per-chunk progress (force=True, printed
                    # whenever the video streams in >1 chunk)
                    state["done"] = (int(m.group(1)) - 1) * chunk
                    state["total"] = int(m.group(2)) * chunk
                    continue
                # Generic N/M progress only before the first chunk is seen,
                # so --debug chatter can't overwrite the chunk-based counter
                m = re.search(r"(\d+)\s*/\s*(\d+)", line)
                if m and not state["total"] and int(m.group(2)) < 100000:
                    state["done"] = int(m.group(1))
                    state["total"] = int(m.group(2))
        except Exception:
            pass

    th = threading.Thread(target=_reader, args=(p,), daemon=True)
    th.start()
    last = -1
    while p.poll() is None:
        if on_frame and state["total"]:
            d = min(state["done"], state["total"])
            if d != last:
                last = d
                on_frame(d, state["total"])
        time.sleep(0.5)
    p.wait()
    _untrack(p)
    th.join(timeout=5)
    dt = time.time() - t0
    log("seedvr2: exit code %d after %.1f s" % (p.returncode, dt))
    if p.returncode != 0:
        log("seedvr2: CLI output tail:\n" + "".join(tail[-20:]))
    return p.returncode == 0, ""


def seedvr2_preflight(model_key):
    """Fast preflight: static checks + a quick venv CUDA probe. NO model
    load and NO inference - file #1 is the real test. Returns (ok, msg)."""
    log("seedvr2 preflight: model=%s" % model_key)
    sv = find_seedvr2()
    if not sv:
        log("seedvr2 preflight: FAIL: seedvr2\\ folder not found")
        return False, ("SeedVR2 not installed "
                       "(seedvr2\\ folder missing - run setup_seedvr2.bat)")
    model_file = SEEDVR2_MODELS.get(model_key, "")
    if not model_file or not os.path.exists(os.path.join(sv["models"], model_file)):
        log("seedvr2 preflight: FAIL: model file missing")
        return False, "SeedVR2 model not downloaded: %s" % model_file
    ff = find_ffmpeg()
    if not ff:
        log("seedvr2 preflight: FAIL: ffmpeg not found")
        return False, "ffmpeg not found"
    code = ("import torch; print('cuda', torch.cuda.is_available(), "
            "torch.cuda.device_count())")
    try:
        r = subprocess.run([sv["python"], "-c", code],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           creationflags=NO_WINDOW, timeout=60, cwd=sv["root"])
    except Exception as e:
        log("seedvr2 preflight: FAIL: venv CUDA check crashed: %s" % e)
        return False, "SeedVR2 CUDA check failed: %s" % e
    out = (r.stdout or "").strip()
    log("seedvr2 preflight: venv torch: %s"
        % (out.replace("\n", " | ") or (r.stderr or "")[:200]))
    if "cuda True" in out:
        log("seedvr2 preflight: OK (CUDA visible to venv)")
        return True, ""
    log("seedvr2 preflight: FAIL: no CUDA visible to venv")
    return False, "no CUDA GPU visible to the SeedVR2 venv"


def seedvr2_upscale(src, dst, model_key, target_res, on_frame=None,
                    debug=False, need_clean=True, low_tiling=True,
                    dit_offload=True, vae_offload=True):
    """AI-upscale a video with SeedVR2 (diffusion).

    Pipeline: raw -> clean H.264 (ffmpeg) -> SeedVR2 CLI -> video ->
    ffmpeg H.264 + original audio -> dst MP4. Returns (ok, msg). Never
    raises. `debug` adds the CLI's --debug flag (only when log file
    writing is on).

    The clean-H.264 prep step is REQUIRED: the recovered raw files are
    damaged MPEG-4/Xvid that SeedVR2's OpenCV backend cannot fully decode
    (it yields 0-1 frames and reports 0x0), while ffmpeg decodes them
    cleanly. Feeding SeedVR2 a clean H.264 copy fixes the output."""
    log("seedvr2 upscale: %s -> %s (model=%s target=%dp)"
        % (src, dst, model_key, target_res))
    sv = find_seedvr2()
    if not sv:
        log("seedvr2: FAIL: seedvr2\\ folder not found")
        return False, ("SeedVR2 not installed "
                       "(seedvr2\\ folder missing - run setup_seedvr2.bat)")
    ff = find_ffmpeg()
    if not ff:
        log("seedvr2: FAIL: ffmpeg not found")
        return False, "ffmpeg not found"

    tmp = dst + "_seedvr2_raw.mp4"
    clean = dst + "_seedvr2_clean.mp4"
    try:
        # SeedVR2 reads frames with OpenCV, which chokes on the damaged
        # MPEG-4 .maas recoveries (0-1 frames decoded, sometimes 0x0).
        # ffmpeg decodes them fine, so first make a clean high-quality
        # H.264 copy for the AI to read (video only - audio is re-added at
        # the end). Intact regular files (any codec: h264/h265/av1/vp9/
        # mpeg4 - all verified decodable by this OpenCV build) skip this
        # step via need_clean=False.
        if need_clean:
            log("seedvr2: preparing clean H.264 input via ffmpeg ...")
            cmd_clean = [ff, "-y", "-v", "error", "-i", src,
                         "-c:v", "libx264", "-preset", "medium", "-crf", "16",
                         "-pix_fmt", "yuv420p", "-an", clean]
            r = _run_tracked(cmd_clean, capture_output=True, text=True)
            if not (os.path.exists(clean) and os.path.getsize(clean) > 0):
                log("seedvr2: FAIL: clean transcode failed: %s"
                    % (r.stderr or "")[:300])
                return False, "could not prepare input: " + (r.stderr or "")[:200]
            log("seedvr2: clean H.264 ready (%d B)" % os.path.getsize(clean))
        else:
            clean = src  # intact source goes straight to the CLI

        cuda = seedvr2_best_gpu()
        log("seedvr2: cuda device: %s" % (cuda or "default (0)"))
        ok, msg = _run_seedvr2(sv, clean, tmp, model_key, target_res,
                               cuda, on_frame=on_frame, debug=debug,
                               low_tiling=low_tiling,
                               dit_offload=dit_offload,
                               vae_offload=vae_offload)
        if not ok or not (os.path.exists(tmp) and os.path.getsize(tmp) > 0):
            log("seedvr2: FAIL: %s" % (msg or "no output"))
            return False, "SeedVR2 failed: %s" % (msg or "no output")
        log("seedvr2: CLI output OK (%d B) - encoding final MP4 with audio"
            % os.path.getsize(tmp))
        cmd = [ff, "-y", "-v", "error", "-i", tmp, "-i", src,
               "-map", "0:v", "-map", "1:a?",
               "-c:v", "libx264", "-preset", "slow", "-crf", "18",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
               "-movflags", "+faststart", dst]
        r = _run_tracked(cmd, capture_output=True, text=True)
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            log("seedvr2: OK -> %s (%d B)" % (dst, os.path.getsize(dst)))
            return True, ""
        try:
            if os.path.exists(dst):
                os.remove(dst)
        except OSError:
            pass
        log("seedvr2: FAIL: final encode failed: %s" % (r.stderr or "")[:300])
        return False, "final encode failed: " + (r.stderr or "")[:300]
    except Exception as e:
        log("seedvr2: FAIL: exception: %s" % e)
        return False, "SeedVR2 error: %s" % e
    finally:
        for t in (tmp,) + ((clean,) if need_clean else ()):
            try:
                if os.path.exists(t):
                    os.remove(t)
            except OSError:
                pass


# ---------------------------------------------------------------- batch
def collect_files(folder, recursive, include_maas=False):
    """All processable video files in folder (optionally recursive):
    regular .mp4 files, plus STOP/Djvu .maas files when include_maas is
    set. De-duplicated."""
    files = []
    it = (os.walk(folder) if recursive
          else [(folder, [], os.listdir(folder))])
    for root, _dirs, names in it:
        for n in names:
            ln = n.lower()
            if ln.endswith(".maas"):
                if include_maas:
                    files.append(os.path.join(root, n))
            elif os.path.splitext(ln)[1] in NORMAL_VIDEO_EXTS:
                files.append(os.path.join(root, n))
    seen, out = set(), []
    for f in files:
        k = os.path.normcase(os.path.abspath(f))
        if k not in seen:
            seen.add(k)
            out.append(f)
    log("scan %s (recursive=%s maas=%s): %d file(s) found"
        % (folder, recursive, include_maas, len(out)))
    return out


def _bad_result(src, name, reason):
    return {"file": src, "name": name, "ok": False, "reason": reason,
            "rec_out": None, "skipped_rec": False, "convert_error": None,
            "upscaled_out": None, "skipped_upscaled": False,
            "upscale_error": None}


def run_batch(selected_folder, files, on_progress=None,
              do_upscale=True, up_model="animevideov3", up_scale=2,
              do_log=True, low_tiling=True, dit_offload=True,
              vae_offload=True):
    """Process a list of video files: recover .maas ransomware files and /
    or H.264-convert legacy-codec videos (automatic), and optionally
    AI-upscale.

    on_progress(index, total, name, phase, result_or_None) is called from
    the calling thread's context (safe for both CLI printing and GUI).
    Returns the list of result dicts.
    """
    log_file = setup_logging(do_log)
    setting = ("%dp" % up_scale) if is_seedvr2(up_model) else ("%dx" % up_scale)
    log("=== RUN START folder=%s files=%d upscale=%s model=%s setting=%s "
        "log=%s ==="
        % (selected_folder, len(files), do_upscale, up_model, setting,
           log_file or "off"))
    rec_dir, up_dir = ensure_output_dirs(selected_folder,
                                         with_upscaled=do_upscale)
    log("output dirs: recovered=%s | upscaled=%s" % (rec_dir, up_dir or "-"))
    manifest = load_manifest(selected_folder)
    log("manifest: %d previously processed file(s)" % len(manifest))
    results = []
    total = len(files)

    # Preflight BEFORE the loop so every file sees the final upscale
    # state (a failed preflight must not leave .maas files un-recovered).
    upscale_enabled = do_upscale
    if do_upscale:
        if is_seedvr2(up_model):
            pok, pmsg = seedvr2_preflight(up_model)
        else:
            gpu_ok, gpu_note = pick_gpu()
            if not gpu_ok:
                pok, pmsg = False, gpu_note
            else:
                if on_progress:
                    on_progress(0, total, "", "gpu: " + gpu_note, None)
                pok, pmsg = esrgan_preflight(up_model, up_scale)
        if not pok:
            upscale_enabled = False
            log("upscaling disabled before first file: %s" % pmsg)
            if on_progress:
                on_progress(0, total, "", "upscaling disabled: " + pmsg, None)

    tmp_root = os.path.join(tempfile_get_tempdir(), "vur_maas_tmp")

    for i, src in enumerate(files, 1):
        name = os.path.basename(src)
        is_maas = name.lower().endswith(".maas")
        stem, ext = base_stem(src)
        try:
            fp = file_fingerprint(src)
        except OSError as e:
            r = _bad_result(src, name, "cannot read source: %s" % e)
            results.append(r)
            if on_progress:
                on_progress(i, total, name, "done", r)
            continue

        log("file %d/%d: %s (md5 %s)" % (i, total, name, fp))
        entry = manifest.get(fp, {})
        rec_known = entry.get("recovered")
        up_known = entry.get("upscaled")
        need_rec = not (rec_known and os.path.exists(rec_known))
        need_up = (upscale_enabled
                   and not (up_known
                            and up_known.get("model") == up_model
                            and up_known.get("scale") == up_scale
                            and os.path.exists(up_known.get("path", ""))))
        log("file %s: need_recovered=%s need_upscale=%s"
            % (name, need_rec, need_up))

        r = {"file": src, "name": name, "ok": True, "reason": "",
             "rec_out": rec_known if rec_known else None,
             "skipped_rec": not need_rec, "convert_error": None,
             "upscaled_out": (up_known.get("path")
                              if (upscale_enabled and not need_up) else None),
             "skipped_upscaled": (upscale_enabled and not need_up),
             "upscale_error": None}

        if not need_rec and not need_up:
            r["reason"] = "already processed - skipped"
            log("file %s: already done - skipped" % name)
            results.append(r)
            if on_progress:
                on_progress(i, total, name, "done", r)
            continue

        maas_tmp = None
        try:
            if is_maas:
                # .maas: recover the stream into a TEMP dir (there is no
                # raw\ output anymore), then H.264-convert it.
                if need_rec:
                    if on_progress:
                        on_progress(i, total, name, "recovering (.maas)", None)
                    tmpdir = os.path.join(tmp_root, fp)
                    os.makedirs(tmpdir, exist_ok=True)
                    rec = recover_one(src, tmpdir)
                    if not rec["ok"] or not rec["raw_out"]:
                        r["ok"] = False
                        r["reason"] = rec["reason"]
                        results.append(r)
                        if on_progress:
                            on_progress(i, total, name, "done", r)
                        continue
                    maas_tmp = rec["raw_out"]
                    r["reason"] = rec["reason"]
                if need_rec and not upscale_enabled:
                    if on_progress:
                        on_progress(i, total, name, "converting to H.264", None)
                    out = unique_path(rec_dir, stem + H264_SUFFIX, H264_EXT)
                    okc, msgc = convert_h264(maas_tmp, out, src)
                    if okc:
                        r["rec_out"] = out
                        r["skipped_rec"] = False
                        entry["recovered"] = out
                        manifest[fp] = entry
                        save_manifest(selected_folder, manifest)
                        log("file %s: recovered H.264 -> %s" % (name, out))
                    else:
                        r["ok"] = False
                        r["convert_error"] = msgc
                        r["reason"] = "H.264 conversion failed: " + msgc[:200]
                        log("file %s: h264 FAILED: %s" % (name, msgc[:300]))
                        results.append(r)
                        if on_progress:
                            on_progress(i, total, name, "done", r)
                        continue
            else:
                # regular video: H.264-convert legacy codecs, copy the rest.
                # Only when upscaling is OFF - with upscaling ON no
                # recovered\ output is produced at all.
                if need_rec and not upscale_enabled:
                    codec = probe_codec(src)
                    log("file %s: codec=%s" % (name, codec))
                    legacy = (codec in LEGACY_CODECS or codec is None)
                    if legacy and find_ffmpeg() is None:
                        # no ffmpeg: cannot probe/convert - copy as-is
                        legacy = False
                    if legacy:
                        if on_progress:
                            on_progress(i, total, name, "converting to H.264", None)
                        out = unique_path(rec_dir, stem + H264_SUFFIX, H264_EXT)
                        okc, msgc = convert_h264(src, out)
                        if not okc:
                            r["ok"] = False
                            r["convert_error"] = msgc
                            r["reason"] = "H.264 conversion failed: " + msgc[:200]
                            log("file %s: h264 FAILED: %s" % (name, msgc[:300]))
                            results.append(r)
                            if on_progress:
                                on_progress(i, total, name, "done", r)
                            continue
                    else:
                        if on_progress:
                            on_progress(i, total, name, "copying", None)
                        out = unique_path(rec_dir, stem + ext, "")
                        shutil.copy2(src, out)
                    r["rec_out"] = out
                    r["skipped_rec"] = False
                    entry["recovered"] = out
                    manifest[fp] = entry
                    save_manifest(selected_folder, manifest)
                    log("file %s: recovered -> %s" % (name, out))
                elif need_rec:
                    # upscaling ON: nothing to produce in recovered\
                    r["skipped_rec"] = True

            # ------------------------------------------------- upscaling
            if upscale_enabled and r["ok"]:
                if is_maas:
                    # just recovered -> damaged temp raw (needs the clean
                    # H.264 prep); previously recovered -> the clean H.264
                    src_for_up = maas_tmp if maas_tmp else rec_known
                    need_clean = bool(maas_tmp)
                else:
                    # intact file, any codec: OpenCV/ffmpeg decode it fine
                    src_for_up = src
                    need_clean = False
                if need_up:
                    def fcb(done, n):
                        if on_progress:
                            on_progress(i, total, name,
                                        "upscaling %d/%d frames" % (done, n), None)
                    tag = UP_TAGS.get(up_model, up_model)
                    if is_seedvr2(up_model):
                        out = unique_path(up_dir,
                                          "%s_UP%d_%s" % (stem, up_scale, tag),
                                          H264_EXT)
                        ok, msg = seedvr2_upscale(src_for_up, out, up_model,
                                                  up_scale, on_frame=fcb,
                                                  debug=do_log,
                                                  need_clean=need_clean,
                                                  low_tiling=low_tiling,
                                                  dit_offload=dit_offload,
                                                  vae_offload=vae_offload)
                    else:
                        out = unique_path(up_dir,
                                          "%s_UP%dX_%s" % (stem, up_scale, tag),
                                          H264_EXT)
                        ok, msg = upscale_video(src_for_up, out, up_model,
                                                up_scale, on_frame=fcb)
                    if ok:
                        r["upscaled_out"] = out
                        r["skipped_upscaled"] = False
                        entry["upscaled"] = {"path": out, "model": up_model,
                                             "scale": up_scale}
                        manifest[fp] = entry
                        save_manifest(selected_folder, manifest)
                        log("file %s: upscaled OK -> %s" % (name, out))
                        if msg:
                            r["upscale_note"] = msg
                    else:
                        r["upscale_error"] = msg
                        log("file %s: upscale FAILED: %s" % (name, msg[:300]))

            results.append(r)
            if on_progress:
                on_progress(i, total, name, "done", r)
        finally:
            # drop the temporary .maas recovery (and its folder)
            if maas_tmp:
                try:
                    os.remove(maas_tmp)
                except OSError:
                    pass
                try:
                    os.removedirs(os.path.dirname(maas_tmp))
                except OSError:
                    pass

    ok = sum(1 for r in results if r["ok"])
    ups = sum(1 for r in results if r.get("upscaled_out"))
    log("=== RUN FINISH ok=%d/%d upscaled=%d log=%s ==="
        % (ok, total, ups, current_log_path() or "off"))
    return results


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(
        description="Video Upscaler and Recovery: H.264 conversion, AI "
                    "upscaling and STOP/Djvu .maas ransomware recovery")
    ap.add_argument("files", nargs="*",
                    help="video files (.mp4) and/or encrypted files (.maas)")
    ap.add_argument("--folder", help="scan a folder for .mp4 and .maas files")
    ap.add_argument("--recursive", action="store_true", help="scan subfolders too")
    ap.add_argument("--upscale", action="store_true",
                    help="also AI-upscale (needs NVIDIA GPU)")
    ap.add_argument("--up-model", default="animevideov3",
                    choices=tuple(UP_TAGS.keys()),
                    help="upscaler model. ESRGAN: animevideov3/x4plus. "
                         "SeedVR2 (needs seedvr2\\ folder): "
                         "seedvr2_3b/seedvr2_3b_gguf/seedvr2_7b/seedvr2_7b_gguf")
    ap.add_argument("--up-scale", type=int, default=2,
                    help="ESRGAN: upscale factor 2/4 (default 2). "
                         "SeedVR2: target short-side resolution, e.g. 1080")
    ap.add_argument("--no-log", action="store_true",
                    help="do not write recovery_log.txt (default: write it)")
    args = ap.parse_args()

    files = list(args.files)
    if args.folder:
        files += collect_files(args.folder, args.recursive, include_maas=True)
    if not files:
        ap.print_help()
        sys.exit(1)

    selected = args.folder or os.path.dirname(os.path.abspath(files[0]))
    rec_dir, up_dir = get_output_dirs(selected)
    print("Selected folder :", selected)
    print("Output          : %s  +  %s" % (rec_dir, up_dir))
    print("Files to process: %d\n" % len(files))

    def cb(i, total, name, phase, r):
        print("[%d/%d] %s - %s" % (i, total, name, phase))

    results = run_batch(selected, files, cb,
                        args.upscale, args.up_model, args.up_scale,
                        do_log=not args.no_log)
    lp = current_log_path()
    if lp:
        print("Log file        : %s" % lp)

    ok = sum(1 for r in results if r["ok"])
    print("\n" + "=" * 70)
    print("SUMMARY: %d/%d OK" % (ok, len(results)))
    print("=" * 70)
    for r in results:
        mark = "OK  " if r["ok"] else "FAIL"
        print("[%s] %s" % (mark, r["name"]))
        print("       %s" % r["reason"])
        if r.get("rec_out"):
            print("       out : %s" % r["rec_out"])
        if r.get("convert_error"):
            print("       convert FAILED: %s" % r["convert_error"])
        if r.get("upscaled_out"):
            print("       upscale: %s" % r["upscaled_out"])
        if r.get("upscale_error"):
            print("       upscale FAILED: %s" % r["upscale_error"])
    sys.exit(0 if ok == len(results) else 2)


if __name__ == "__main__":
    main()
