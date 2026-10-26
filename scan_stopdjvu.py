#!/usr/bin/env python3
"""
STOP/Djvu folder scanner.

Finds STOP/Djvu-encrypted files in a folder (by the constant file marker),
extracts each file's 40-char Personal ID, and classifies it as:

  OFFLINE  (Personal ID ends with 't1')  -> RECOVERABLE with the free
             Emsisoft / world-empty decryptor (key is public).
  ONLINE   (any other Personal ID)       -> NOT recoverable for free;
             the per-victim RSA private key is only held by the attacker.

Usage:
    python scan_stopdjvu.py <folder_or_file> [more paths...]
"""
import os
import sys

FILEMARKER = b'{36A698B9-D67C-4E07-BE82-0EC5B14B4DF5}'
MIN_SIZE = 334 + 5  # footer + salt


def analyze(path):
    """Return a dict describing a STOP/Djvu file, or None if not one."""
    try:
        size = os.path.getsize(path)
        if size < MIN_SIZE:
            return None
        with open(path, 'rb') as f:
            f.seek(-38, 2)
            marker = f.read(38)
        if marker != FILEMARKER:
            return None
        with open(path, 'rb') as f:
            f.seek(-78, 2)
            pid_raw = f.read(40)
        try:
            pid = pid_raw.decode('ascii')
            if not (pid.isalnum() and len(pid) == 40):
                pid = pid_raw.decode('latin1')
        except Exception:
            pid = "<non-ascii>"
        return {
            'path': path,
            'size': size,
            'pid': pid,
            'offline': pid.endswith('t1'),
        }
    except Exception:
        return None


def collect(paths):
    files = []
    for p in paths:
        if os.path.isfile(p):
            files.append(p)
        elif os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                for nm in names:
                    files.append(os.path.join(root, nm))
    return files


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    files = collect(sys.argv[1:])
    offline, online, other = [], [], 0
    for fp in files:
        info = analyze(fp)
        if info is None:
            other += 1
            continue
        (offline if info['offline'] else online).append(info)

    print("=" * 70)
    print(f"Scanned {len(files)} file(s). STOP/Djvu files found: {len(offline)+len(online)}")
    print("=" * 70)

    print(f"\n[+] RECOVERABLE (offline key, ends with 't1'): {len(offline)}")
    for i in offline:
        print(f"    {i['pid']}   <-  {i['path']}")

    print(f"\n[x] NOT recoverable for free (online key): {len(online)}")
    for i in online:
        print(f"    {i['pid']}   <-  {i['path']}")

    print(f"\n    (non-STOP/Djvu files ignored: {other})")

    if offline:
        print("\n> For the RECOVERABLE files, run the free decryptor:")
        print("    pip install pycryptodome requests")
        print("    python decrypt_stopdjvu.py <folder>")
        print("  (world-empty/stopdjvu-decryptor, or Emsisoft's official tool)")
    if online:
        print("\n> For the ONLINE-key files there is no free decryptor. Try:")
        print("    1. Backups (external drive, NAS, Time Machine, etc.)")
        print("    2. Windows 'Previous Versions' / Volume Shadow Copies")
        print("    3. Cloud-sync previous versions (OneDrive/Google/Dropbox)")
        print("    Do NOT pay the ransom - success is rare and it encourages more attacks.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
