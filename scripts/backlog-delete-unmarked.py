#!/usr/bin/env python3
"""Backfill stage 3/3: delete unmarked extras from the rip staging.

For each movie folder under rip/movies whose validation.json carries
extras.include=false, every staged non-main title is deleted (the marker
already decided they are not worth encoding). The main is never touched:
the doc's recorded main wins; with no recorded main, the longest title in
the doc's titles[] is the main (the same intent transcode's bytes fallback
encodes); a folder with no readable doc is skipped loudly. Junk-labelled
files (mid-rip/failed rename) are out of scope -- they are not extras.

Run this AFTER backlog-mark.py and the manual keep-edits + sync-marks.py.
Everything deleted here is recoverable only by re-ripping the disc.

Usage:  python scripts/backlog-delete-unmarked.py [--go]
        Dry-run by default: prints what would delete; --go deletes.
        Roots: $VIDEOS (default /mnt/videos) -> <root>/rip/movies.
"""

import argparse
import os
import sys
from pathlib import Path

from skive.validation import (VALIDATION_FILENAME, load_validation,
                              read_extras_marker)


def _main_t(doc: dict) -> int | None:
    """The movie's main title number: the recorded main, else the doc's
    longest title's t, else None (folder skipped -- no basis to protect a
    main). Protecting by t (not file name) keeps every .pN part of a split
    main safe."""
    rec = doc.get("main")
    if isinstance(rec, str) and rec.startswith("t") and rec[1:].isdigit():
        return int(rec[1:])
    rows = [r for r in doc.get("titles", []) if isinstance(r.get("dur_s"), int)]
    if rows:
        return max(rows, key=lambda r: r["dur_s"]).get("t")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--go", action="store_true", help="delete (default: dry-run)")
    args = ap.parse_args()

    base = Path(os.environ.get("VIDEOS", "/mnt/videos")) / "rip" / "movies"
    if not base.is_dir():
        print(f"{base}: not a directory", file=sys.stderr)
        return 1
    freed = deleted = 0
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        doc = load_validation(folder / VALIDATION_FILENAME)
        if doc is None:
            print(f"no validation.json, skipped: {folder}", file=sys.stderr)
            continue
        marker = read_extras_marker(doc)
        main_t = _main_t(doc)
        if marker["include"]:
            print(f"keep (include=true): {folder.name}")
            continue
        if main_t is None:
            print(f"no main basis, skipped: {folder}", file=sys.stderr)
            continue
        for row in doc.get("titles", []):
            f = row.get("file")
            if not isinstance(f, str) or row.get("t") == main_t:
                continue
            p = folder / f
            if not p.is_file():
                continue
            mb = p.stat().st_size // 1048576
            print(f"{folder.name}: {'delete' if args.go else 'would delete'} {f} ({mb} MB)")
            if args.go:
                p.unlink()
                deleted += 1
                freed += mb
    tail = f"{deleted} files, {freed} MB freed" if args.go else "dry-run, nothing deleted"
    print(f"-- {tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
