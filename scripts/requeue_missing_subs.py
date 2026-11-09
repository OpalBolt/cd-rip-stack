#!/usr/bin/env python3
"""Requeue library movies that lack Danish/English sidecars their lossless
source can supply (the index+1 track-number bug dropped them), so the normal
`skive transcode` run encodes them again with the fixed subtitle pass.

Per movie, when the lossless source's kept subtitle tracks imply sidecars the
library folder lacks:
  1. move the lossless folder's contents to rip/movies/<title>/ (same share, a rename)
  2. copy the library validation.json there (the rip's record of the disc)
  3. delete the library encode, its sidecars and validation.json (transcode
     never clobbers a shipped file); extras/ and anything else stay
Skipped when rip/movies/<title>/ already holds files. Dry-run by default
(ffprobe/mkvmerge header reads only, no OCR); --run changes things.

shortcut: a title whose track is genuinely barren (0 cues) is requeued too and
will lack the sidecar again -- no way to know without OCR; accept the re-encode.

Usage:  python scripts/requeue_missing_subs.py [--run] [--root /mnt/videos] [TITLE ...]
        then: skive transcode
"""

import argparse
import glob
import os
import shutil
import sys

from skive import subs

VALIDATION = "validation.json"


def expected(src: str, title: str, ffprobe: str) -> set[str]:
    keeps, _ = subs.decide(subs.inventory(src, ffprobe))
    return {subs.sidecar_name(title, k.track.lang, k.flag) for k in keeps}


def requeue(title: str, root: str, ffprobe: str, run: bool, expected_fn=expected) -> str:
    """Returns a one-line status. Mutates the share only when run is True."""
    lossless = os.path.join(root, "lossless", "movies", title)
    lib = os.path.join(root, "library", "movies", title)
    rip = os.path.join(root, "rip", "movies", title)
    src = os.path.join(lossless, f"{title}_t00.mkv")
    if not os.path.isfile(src) or not os.path.isfile(os.path.join(lib, f"{title}.mkv")):
        return f"skip {title} (no lossless source or library encode)"
    missing = sorted(n for n in expected_fn(src, title, ffprobe)
                     if not os.path.exists(os.path.join(lib, n)))
    if not missing:
        return ""
    if os.path.isdir(rip) and any(n != VALIDATION for n in os.listdir(rip)):
        return f"skip {title} (rip/ already holds files)"
    line = f"{title}: missing {', '.join(missing)}"
    if not run:
        return line
    os.makedirs(rip, exist_ok=True)
    for name in os.listdir(lossless):
        shutil.move(os.path.join(lossless, name), os.path.join(rip, name))
    os.rmdir(lossless)
    lib_doc = os.path.join(lib, VALIDATION)
    if os.path.isfile(lib_doc):
        shutil.copy2(lib_doc, os.path.join(rip, VALIDATION))
    # only after the source is safely staged
    for path in [os.path.join(lib, f"{title}.mkv"), lib_doc,
                 *glob.glob(os.path.join(glob.escape(lib), f"{glob.escape(title)}.*.srt"))]:
        if os.path.isfile(path):
            os.remove(path)
    return line + "  -> requeued"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("titles", nargs="*")
    ap.add_argument("--run", action="store_true", help="move/delete (default: dry-run)")
    ap.add_argument("--root", default=os.environ.get("VIDEOS", "/mnt/videos"))
    a = ap.parse_args()
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        print("need ffprobe on PATH", file=sys.stderr)
        return 2
    n = 0
    for title in a.titles or sorted(os.listdir(os.path.join(a.root, "library", "movies"))):
        line = requeue(title, a.root, ffprobe, a.run)
        if line:
            print(line)
            n += line.endswith("requeued")
    print(f"{n} requeued" if a.run else "dry-run: nothing changed; add --run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
