#!/usr/bin/env python3
"""Rename sidecars an older pipeline mislabelled `.forced`.

A library `<title>.<lang>.forced.srt` is a mislabelled full track when the
lossless source has a <lang> subtitle track but none of them is flagged
forced -- no real forced track could have produced it. Renames it to
`<title>.<lang>.srt`, never clobbering. Sources with a forced-flagged <lang>
track are left alone (the file may be genuine). If `<title>.<lang>.srt` already
exists, the source must hold 2+ tracks of that language and the file becomes
`.sdh.srt` when it has >= 30% of the full file's cues (subs.settle's rule);
anything else is printed for review. Never overwrites. Dry-run by default; reads
only ffprobe/mkvmerge headers.

Usage:  python scripts/rename_forced_subs.py [--run] [--root /mnt/videos] [TITLE ...]
"""

import argparse
import os
import shutil
import sys

from skive import subs


def rename_forced(title: str, root: str, ffprobe: str, run: bool, inventory=subs.inventory) -> list[str]:
    lib = os.path.join(root, "library", "movies", title)
    src = os.path.join(root, "lossless", "movies", title, f"{title}_t00.mkv")
    if not os.path.isdir(lib) or not os.path.isfile(src):
        return []
    tracks = inventory(src, ffprobe)
    out = []
    for lang in ("eng", "dan"):
        mine = [t for t in tracks if t.lang == lang]
        old = os.path.join(lib, subs.sidecar_name(title, lang, "forced"))
        new = os.path.join(lib, subs.sidecar_name(title, lang, ""))
        if not mine or any(t.forced for t in mine) or not os.path.isfile(old):
            continue
        if os.path.exists(new):
            # same rule as subs.settle: the full track has the most cues, a
            # runner-up with >= 30% of them is the SDH duplicate, less is junk
            sdh = os.path.join(lib, subs.sidecar_name(title, lang, "sdh"))
            n_old, n_new = subs.count_events(old), subs.count_events(new)
            why = (f"{os.path.basename(old)} ({n_old} cues) vs {os.path.basename(new)} ({n_new})")
            if len(mine) < 2 or os.path.exists(sdh) or n_old > n_new \
                    or n_old * subs._SDH_DEN < n_new * subs._SDH_NUM:
                out.append(f"skip {why}: review by hand")
                continue
            new = sdh
        out.append(f"{os.path.basename(old)} -> {os.path.basename(new)}")
        if run:
            os.rename(old, new)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("titles", nargs="*")
    ap.add_argument("--run", action="store_true", help="rename (default: dry-run)")
    ap.add_argument("--root", default=os.environ.get("VIDEOS", "/mnt/videos"))
    a = ap.parse_args()
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        print("need ffprobe on PATH", file=sys.stderr)
        return 2
    n = 0
    for title in a.titles or sorted(os.listdir(os.path.join(a.root, "library", "movies"))):
        for line in rename_forced(title, a.root, ffprobe, a.run):
            print(f"{title}: {line}")
            n += "->" in line
    print(f"{n} renamed" if a.run else f"dry-run: {n} to rename; add --run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
