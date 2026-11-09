#!/usr/bin/env python3
"""Regenerate Danish/English sidecars that the old index+1 track-number bug
dropped as "barren" (MakeMKV writes non-contiguous Matroska TrackNumbers).

For each library movie whose lossless source has kept subtitle tracks but whose
library folder lacks the matching sidecars, run the subtitle pass on the source
into a temp dir and copy ONLY the missing sidecars beside the encode. Never
touches the video or validation.json. Dry-run by default (ffprobe/mkvmerge
only, no OCR); --run does the OCR -- run it on the transcode box.

Usage:  python scripts/regen_subs.py [--run] [--root /mnt/videos] [TITLE ...]
        (no TITLE = scan every movie in the library)
"""

import argparse
import os
import shutil
import sys
import tempfile

from skive import subs


def expected(src: str, title: str, ffprobe: str) -> set[str]:
    keeps, _ = subs.decide(subs.inventory(src, ffprobe))
    # shortcut: a language with several full tracks settles to one name; the
    # pre-settle set can over-report, which only costs a no-op OCR pass.
    return {subs.sidecar_name(title, k.track.lang, k.flag) for k in keeps}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("titles", nargs="*")
    ap.add_argument("--run", action="store_true", help="do the OCR (default: dry-run)")
    ap.add_argument("--root", default=os.environ.get("VIDEOS", "/mnt/videos"))
    a = ap.parse_args()
    seconv, ffprobe = shutil.which("seconv"), shutil.which("ffprobe")
    if not ffprobe or (a.run and not seconv):
        print("need ffprobe (and seconv with --run) on PATH", file=sys.stderr)
        return 2
    lib = os.path.join(a.root, "library", "movies")
    src_root = os.path.join(a.root, "lossless", "movies")
    rc = 0
    for title in a.titles or sorted(os.listdir(lib)):
        src = os.path.join(src_root, title, f"{title}_t00.mkv")
        dest = os.path.join(lib, title)
        if not os.path.isfile(src) or not os.path.isdir(dest):
            print(f"skip {title} (no lossless source or library folder)")
            continue
        missing = sorted(n for n in expected(src, title, ffprobe)
                         if not os.path.exists(os.path.join(dest, n)))
        if not missing:
            continue
        print(f"{title}: missing {', '.join(missing)}")
        if not a.run:
            continue
        with tempfile.TemporaryDirectory(prefix="regen-subs-") as tmp:
            res = subs.run_pass(src, tmp, title, seconv, ffprobe)
            for n in res.notes:
                print(f"  {n}")
            if not res.ok:
                rc = 1
            for n in res.sidecars:
                if n in missing:
                    shutil.copy2(os.path.join(tmp, n), os.path.join(dest, n))
                    print(f"  wrote {n}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
