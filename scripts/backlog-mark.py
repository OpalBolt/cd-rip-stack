#!/usr/bin/env python3
"""Backfill stage 1/3: mark every validation.json with the default extras marker.

Adds extras: {include: false, quality: superfast} to every movie validation.json
under the rip staging and library roots (CONTEXT.md: Extras marker). Idempotent:
a document that already carries an extras key is never touched, so manual
keep-edits survive re-runs. Manually flip include to true for the keepers next
(library copy is the one sync-marks.py propagates), then run
backlog-delete-unmarked.py against the rip staging.

Usage:  python scripts/backlog-mark.py [--go]
        Dry-run by default: prints what it would mark; --go writes.
        Roots: $VIDEOS (default /mnt/videos) -> <root>/rip/movies and
        <root>/library/movies.
"""

import argparse
import json
import os
import sys
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo))  # the skive package

from skive.validation import VALIDATION_FILENAME, set_extras_marker


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--go", action="store_true", help="write the markers (default: dry-run)")
    args = ap.parse_args()

    root = Path(os.environ.get("VIDEOS", "/mnt/videos"))
    marked = 0
    for sub in ("rip/movies", "library/movies"):
        base = root / sub
        if not base.is_dir():
            print(f"{base}: not a directory -- skipped", file=sys.stderr)
            continue
        for doc in sorted(base.glob(f"*/{VALIDATION_FILENAME}")):
            existing = "extras" in json.loads(doc.read_text(encoding="utf-8"))
            if existing:
                print(f"already marked: {doc}")
                continue
            print(f"{'mark' if args.go else 'would mark'}: {doc}")
            if args.go and set_extras_marker(doc, include=False):
                marked += 1
    print(f"-- {marked} marked" if args.go else "-- dry-run, nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
