#!/usr/bin/env python3
"""Regenerate Danish/English sidecars that the old index+1 track-number bug
dropped as "barren" (MakeMKV writes non-contiguous Matroska TrackNumbers).

For each library movie whose lossless source has kept subtitle tracks but whose
library folder lacks the matching sidecars, run the subtitle pass on the source
into a temp dir and copy ONLY the missing sidecars beside the encode. Never
touches the video or validation.json. Dry-run by default (ffprobe/mkvmerge
only, no OCR); --run does the OCR -- run it on the transcode box.

Usage:  python scripts/regen_subs.py [--run] [--jobs N] [--root /mnt/videos] [TITLE ...]
        (no TITLE = scan every movie in the library)
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

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
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) // 4),
                    help="titles OCR'd at once (default: cpus // 4)")
    a = ap.parse_args()
    seconv, ffprobe = shutil.which("seconv"), shutil.which("ffprobe")
    mkvmerge = shutil.which("mkvmerge")
    if not ffprobe or (a.run and not (seconv and mkvmerge)):
        print("need ffprobe (and seconv, mkvmerge with --run) on PATH", file=sys.stderr)
        return 2
    lib = os.path.join(a.root, "library", "movies")
    src_root = os.path.join(a.root, "lossless", "movies")
    cache = os.path.expanduser("~/.cache/skive/regen-subs")
    os.makedirs(cache, exist_ok=True)
    todo = []
    for title in a.titles or sorted(os.listdir(lib)):
        src = os.path.join(src_root, title, f"{title}_t00.mkv")
        dest = os.path.join(lib, title)
        if not os.path.isfile(src) or not os.path.isdir(dest):
            print(f"skip {title} (no lossless source or library folder)")
            continue
        missing = sorted(n for n in expected(src, title, ffprobe)
                         if not os.path.exists(os.path.join(dest, n)))
        if missing:
            print(f"{title}: missing {', '.join(missing)}")
            todo.append((title, src, dest, missing))
    if not a.run:
        return 0

    def regen(item) -> bool:
        title, src, dest, missing = item
        with tempfile.TemporaryDirectory(prefix="regen-subs-") as tmp:
            # one sequential NFS read of the source, then every seconv/ffprobe
            # read hits the small local copy (was: one full NFS scan per track)
            local = os.path.join(cache, f"{title}.subs.mkv")
            try:
                print(f"  [{title}] extracting subtitle tracks (reads the whole "
                      f"source over the network, can take minutes)...", flush=True)
                r = subprocess.run(
                    [mkvmerge, "-o", local, "--no-video", "--no-audio",
                     "--no-chapters", "--no-attachments", "--no-global-tags", src],
                    capture_output=True, text=True)
                if r.returncode > 1:  # 1 = warnings only
                    print(f"  [{title}] FAIL mkvmerge extract: {r.stdout.strip()[-200:]}")
                    return False
                print(f"  [{title}] extracted {os.path.getsize(local) >> 20} MiB, OCR...",
                      flush=True)
                res = subs.run_pass(local, tmp, title, seconv, ffprobe)
            finally:
                if os.path.exists(local):
                    os.remove(local)
            for n in res.notes:
                print(f"  [{title}] {n}")
            for n in res.sidecars:
                if n in missing:
                    shutil.copy2(os.path.join(tmp, n), os.path.join(dest, n))
                    print(f"  [{title}] wrote {n}")
            return res.ok

    # each pass already OCRs up to 4 tracks at once; --jobs adds titles on top
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        return 0 if all(ex.map(regen, todo)) else 1


if __name__ == "__main__":
    sys.exit(main())
