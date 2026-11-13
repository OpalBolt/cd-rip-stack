#!/usr/bin/env python3
"""One-off repair: re-check the tmdb ids the validation backfill recorded.

The backfill (scripts/backfill_validation.py) resolved legacy folders by
bare-name TMDB search and took the first hit — a popularity sort where a
sequel or sibling outranks the original ("X-Men" returns X-Men: Apocalypse
first), so some folders recorded a wrong film's id. The duplicate gate then
"recognised" the wrong film as already ripped. The backfill now year-checks
its pick; this repairs what it wrote before that.

Walks every backfill-shaped movie document (run/volume_label null — rip-run
docs were picked by a human and stay untouched) under rip/movies,
lossless/movies and library/movies, fetches the recorded id, and when the
id's year disagrees with the folder's (Year) re-picks by name+year search
and rewrites the id. Library copies are repaired by the same walk. Dry-run
by default; --apply writes.

Usage:  python scripts/repair_backfilled_ids.py [--apply] [--root PATH]
        (default root: $VIDEOS_MOUNT, like backfill_validation)
"""

import argparse
import os
import sys
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo))  # the skive package

from skive.tmdb import fetch_by_id, search_first, title_matches
from skive.validation import (
    VALIDATION_FILENAME, YEAR_SUFFIX, load_validation, write_validation,
)


def repair_folder(folder: Path, apply: bool) -> str | None:
    """One folder: None when its doc is fine (or not backfill-shaped), else
    the corrected id after rewriting the document.

    Only the id changes — the title stays the folder's display name and the
    graded rows stay as recorded (they were graded without a runtime; the
    checker re-grades on demand).
    """
    doc = load_validation(folder / VALIDATION_FILENAME)
    tmdb = doc.get("tmdb") if doc else None
    if (not isinstance(tmdb, dict) or tmdb.get("type") != "movie"
            or doc.get("run") is not None or doc.get("volume_label") is not None):
        return None
    m = YEAR_SUFFIX.match(folder.name)
    if m is None:
        return None
    name, year = m.group(1), m.group(2)
    try:
        recorded = fetch_by_id("movie", str(tmdb["id"]))
    except (RuntimeError, ValueError) as e:
        print(f"warn  {folder.name}: id {tmdb['id']} fetch failed ({e})", file=sys.stderr)
        recorded = None  # unreadable id: treat as suspect, let the search decide
    if recorded is not None and recorded.year == year:
        return None
    try:
        pick = search_first("movie", name, year)
    except (RuntimeError, ValueError) as e:
        print(f"skip  {folder.name}: TMDB search failed ({e})", file=sys.stderr)
        return None
    if pick is None or not title_matches(name, pick.title):
        print(f"skip  {folder.name}: id {tmdb['id']} looks wrong "
              f"({recorded.title} ({recorded.year}) on record) and no hit "
              f"titled {name!r} ({year}) — needs eyes", file=sys.stderr)
        return None
    if pick.id == str(tmdb["id"]):
        return None  # same id back: the year was right, the fetch lied about it
    tmdb["id"] = pick.id
    if apply:
        write_validation(folder / VALIDATION_FILENAME, doc)
    return pick.id


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="repair_backfilled_ids.py",
        description="Re-check the ids the validation backfill recorded "
                    "(dry-run by default).")
    p.add_argument("--apply", action="store_true",
                   help="rewrite mismatched ids (default: print the plan only)")
    p.add_argument("--root", metavar="PATH",
                   default=os.environ.get("VIDEOS_MOUNT", "/mnt/videos"),
                   help="share root (default: $VIDEOS_MOUNT or /mnt/videos)")
    args = p.parse_args(argv)
    root = Path(args.root)

    repaired = 0
    for sub in ("rip/movies", "lossless/movies", "library/movies"):
        base = root / sub
        if not base.is_dir():
            continue
        for folder in sorted(base.iterdir()):
            if not folder.is_dir() or not (folder / VALIDATION_FILENAME).exists():
                continue
            new_id = repair_folder(folder, args.apply)
            if new_id is None:
                continue
            verb = "fixed " if args.apply else "would fix"
            print(f"{verb} {sub}/{folder.name}: id -> {new_id}")
            repaired += 1
    print(f"\n{'fixed' if args.apply else 'would fix'} {repaired} document(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
