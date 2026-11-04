#!/usr/bin/env python3
"""Backfill stage 2/3: sync manually-edited extras marks from the library outwards.

The library copy of validation.json is the source of truth (that is where the
manual include=true edits land). For each library movie, the extras marker is
copied onto the same-named validation.json under rip/movies (what transcode
reads) and lossless/movies (what a future re-transcode of parked sources would
read). Only the extras key is touched; everything else stays as each copy has
it. Movies with no marker in the library are skipped loudly.

Usage:  python scripts/sync-marks.py [--go]
        Dry-run by default: prints what would sync; --go writes.
        Roots: $VIDEOS (default /mnt/videos).
"""

import argparse
import os
import sys
from pathlib import Path

from skive.validation import (VALIDATION_FILENAME, load_validation,
                              read_extras_marker, write_validation)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--go", action="store_true", help="write the markers (default: dry-run)")
    args = ap.parse_args()

    root = Path(os.environ.get("VIDEOS", "/mnt/videos"))
    lib = root / "library" / "movies"
    if not lib.is_dir():
        print(f"{lib}: not a directory", file=sys.stderr)
        return 1
    synced = 0
    for doc_path in sorted(lib.glob(f"*/{VALIDATION_FILENAME}")):
        doc = load_validation(doc_path)
        if doc is None or "extras" not in doc:
            print(f"no marker, skipped: {doc_path}", file=sys.stderr)
            continue
        marker = read_extras_marker(doc)
        movie = doc_path.parent.name
        for dest in (root / "rip" / "movies" / movie / VALIDATION_FILENAME,
                     root / "lossless" / "movies" / movie / VALIDATION_FILENAME):
            if not dest.is_file():
                continue
            other = load_validation(dest)
            if other is None or read_extras_marker(other) == marker:
                continue
            print(f"{movie}: {'sync' if args.go else 'would sync'} "
                  f"include={marker['include']} ({marker['quality']}) -> {dest}")
            if args.go:
                other["extras"] = marker
                write_validation(dest, other)
                synced += 1
    print(f"-- {synced} synced" if args.go else "-- dry-run, nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
