#!/usr/bin/env python3
"""One-off backfill: write validation.json for rips made before the record
existed.

The validation document was born mid-library: everything ripped before it
has none. This backfill reconstructs the document from what still exists —
the lossless sources under rip/ and lossless/ — plus the same TMDB data the
rip flow would have recorded:

- movies (rip/movies, lossless/movies): every staged `_tNN` source probed
  and graded against the TMDB runtime (name+year search). main stays null —
  never guessed (the Mamma Mia lesson); volume label and run stay null —
  that provenance died with the run log. A copy lands beside the library
  encode when the library folder exists and has no document of its own, so
  the checker stops searching TMDB for it every run.
- tv (rip/tv): seasons derived from the staged episode names, each episode
  probed and graded against its TMDB runtime; the disc index stays empty —
  no disc provenance survives. An old one-season document (the first
  schema) is rebuilt whole; a seasons-shaped document is a live record and
  is left alone.

Folders with no (Year), no TMDB match, or no staged sources are skipped
with a note — never guessed. Dry-run by default: prints every document it
would write and where it would copy; --apply writes.

Usage:  python scripts/backfill_validation.py [--apply] [--root PATH]
        (default root: $VIDEOS_MOUNT, like backfill_years)
"""

import argparse
import os
import re
import sys
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo))  # the skive package

from skive.tmdb import fetch_season, search_first
from skive.validation import (
    STAGING_LABEL, VALIDATION_FILENAME, YEAR_SUFFIX, _double_runtime,
    load_validation, merge_tv_document, movie_document, movie_row,
    probe_duration, tv_document, tv_row, write_validation,
)


def _search(kind: str, folder: Path):
    """The folder's TMDB pick by name+year search, or None with one note.

    Never raises: no (Year), a failed search, and no match each print their
    one skip line — the walk goes on.
    """
    m = YEAR_SUFFIX.match(folder.name)
    if m is None:
        print(f"skip {folder.name}: no (Year) in the folder name", file=sys.stderr)
        return None
    try:
        pick = search_first(kind, m.group(1))
    except (RuntimeError, ValueError) as e:
        print(f"skip {folder.name}: TMDB search failed ({e})", file=sys.stderr)
        return None
    if pick is None:
        print(f"skip {folder.name}: no TMDB match for {m.group(1)!r}", file=sys.stderr)
        return None
    return pick


def backfill_movie(folder: Path) -> dict | None:
    """The reconstructed movie document from the folder's staged sources.

    Every root-level `_tNN`/`_eNN` source (promoted files — a straggler
    still in d<disc>/ is not this folder's record) probes and grades against
    the TMDB runtime; the search only runs when there are sources to grade
    (a folder with nothing to record never reaches the network).
    main/volume_label/run null: the reconstruction cannot know what the rip
    run knew.
    """
    staged = [p for p in sorted(folder.glob("*.mkv"))
              if STAGING_LABEL.match(p.stem)]
    if not staged:
        print(f"skip {folder.name}: no staged sources", file=sys.stderr)
        return None
    pick = _search("movie", folder)
    if pick is None:
        return None
    rows = [movie_row(p.name, int(STAGING_LABEL.match(p.stem).group("num")),
                      probe_duration(p), pick.runtime_secs)
            for p in staged]
    return movie_document(pick.id, folder.name, pick.runtime_secs,
                          None, None, None, rows)


def backfill_show(folder: Path) -> dict | None:
    """The reconstructed show document: one seasons entry per season found
    staged, episodes probed and graded, the disc index empty (no provenance
    survives). A season whose TMDB fetch fails is skipped with a note; the
    rest of the show still builds.
    """
    pick = _search("tv", folder)
    if pick is None:
        return None
    pattern = re.compile(rf"^{re.escape(folder.name)}"
                         r"_S(?P<sn>\d+)E(?P<en>\d+)(?:-E(?P<hi>\d+))?$")
    by_season: dict[int, list[Path]] = {}
    for p in sorted(folder.glob("*.mkv")):
        m = pattern.match(p.stem)
        if m is not None:
            by_season.setdefault(int(m.group("sn")), []).append(p)
    if not by_season:
        print(f"skip {folder.name}: no staged episode files", file=sys.stderr)
        return None
    doc = None
    for season in sorted(by_season):
        try:
            listed = fetch_season(pick.id, season)
        except (RuntimeError, ValueError) as e:
            print(f"skip {folder.name} season {season:02d}: "
                  f"TMDB season fetch failed ({e})", file=sys.stderr)
            continue
        runtimes = {ep: rt or None for ep, _name, rt in listed}
        rows = []
        for p in by_season[season]:
            m = pattern.match(p.stem)
            lo = int(m.group("en"))
            hi = max(lo, int(m.group("hi"))) if m.group("hi") else lo
            rows.append(tv_row(lo, p.name, None, probe_duration(p),
                               _double_runtime(runtimes, lo, hi)))
        season_doc = tv_document(pick.id, folder.name, season,
                                 None, None, [], rows)
        doc = season_doc if doc is None else merge_tv_document(doc, season_doc)
    if doc is None:
        return None
    for entry in doc["seasons"]:
        entry["discs"] = []  # no disc provenance survives a backfill
    return doc


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="backfill_validation.py",
        description="Write validation.json for rips made before the record "
                    "existed (dry-run by default).")
    p.add_argument("--apply", action="store_true",
                   help="write the documents (default: print the plan only)")
    p.add_argument("--root", metavar="PATH",
                   default=os.environ.get("VIDEOS_MOUNT", "/mnt/videos"),
                   help="share root (default: $VIDEOS_MOUNT or /mnt/videos)")
    args = p.parse_args(argv)
    root = Path(args.root)

    written = copied = skipped = 0
    for sub in ("rip/movies", "lossless/movies"):
        base = root / sub
        if not base.is_dir():
            continue
        for folder in sorted(base.iterdir()):
            if not folder.is_dir():
                continue
            rel = f"{sub}/{folder.name}"
            if (folder / VALIDATION_FILENAME).exists():
                print(f"keep   {rel}: validation.json already there")
                skipped += 1
                continue
            doc = backfill_movie(folder)
            if doc is None:
                skipped += 1
                continue
            lib = root / "library" / "movies" / folder.name
            copy_to = lib if lib.is_dir() \
                and not (lib / VALIDATION_FILENAME).exists() else None
            note = f" (+ copy to library/movies/{folder.name})" if copy_to else ""
            print(f"write  {rel}: {len(doc['titles'])} titles, main=null{note}")
            if args.apply:
                write_validation(folder / VALIDATION_FILENAME, doc)
                if copy_to is not None:
                    write_validation(copy_to / VALIDATION_FILENAME, doc)
                    copied += 1
            written += 1

    tv_base = root / "rip" / "tv"
    if tv_base.is_dir():
        for folder in sorted(tv_base.iterdir()):
            if not folder.is_dir():
                continue
            rel = f"rip/tv/{folder.name}"
            existing = load_validation(folder / VALIDATION_FILENAME)
            if existing is not None and "seasons" in existing:
                print(f"keep   {rel}: validation.json already there")
                skipped += 1
                continue
            doc = backfill_show(folder)
            if doc is None:
                skipped += 1
                continue
            seasons = ", ".join(f"S{e['season']:02d} ({len(e['episodes'])} eps)"
                                for e in doc["seasons"])
            verb = "rebuild" if existing is not None else "write"
            print(f"{verb} {rel}: {seasons}, discs=[]")
            if args.apply:
                write_validation(folder / VALIDATION_FILENAME, doc)
            written += 1

    tail = f", {copied} librar{'y' if copied == 1 else 'ies'}" if args.apply else ""
    print(f"\n{'wrote' if args.apply else 'would write'} "
          f"{written} document(s){tail}; {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
