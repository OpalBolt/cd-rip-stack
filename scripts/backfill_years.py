#!/usr/bin/env python3
"""One-off backfill: complete the failed rename on staged rip leftovers.

Some staged files under rip/<type>/ were renamed by an older hand-era step
that wrote the title but never the (Year) -- sometimes gluing MakeMKV's
disc tag into the name (Title-B1_t00.mkv) or stripping Danish characters
(Det flles DK). They parse by the staged-name contracts but carry no year,
so both transcode (its junk guard) and migrate_layout ("failed rename (no
title year)") refuse them -- correctly: the year is the only thing in the
filename that says a TMDB pick confirmed the title.

This backfill supplies that confirmation: per title group it runs the same
interactive TMDB pick the rip flow uses (skive/tmdb.py's search_loop -- at
the table 's' re-queries, 'q' aborts before anything is renamed), then
renames each file in place to the canonical staged name: sanitize(title
(year)) plus the file's own _tNN[.pN] / _SNNENN[-E..] / " - Extra NN"
suffix, exactly as a fresh rip names them (skive/rip_common.py's display).
The glued disc tag is dropped; the whole title becomes the pick's. A TMDB
entry without a release date asks for the year by hand (Enter skips the
group). Groups that land on one canonical name in the same directory
become parts, .pN in group order.

Dry-run by default: prints the plan and discards the picks. After --apply,
re-run migrate_layout.py to re-tree the renamed files (idempotent).

Usage:  python scripts/backfill_years.py [--apply] [--root PATH]
        (default root: $VIDEOS_MOUNT, like migrate_layout)
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo / "windows"))  # transcode.py does `import subs` beside itself
sys.path.insert(0, str(_repo))  # the skive package
import transcode  # staged-name contracts: _RX_TITLE, _RX_EP, _RX_EXTRA

from skive import tmdb
from skive.common import sanitize

_JUNK = re.compile(r"^[A-Z][0-9]+_t\d+\.mkv$")  # MakeMKV throwaway label (CONTEXT.md)
_YEAR = re.compile(r"\(\d{4}\)$")  # transcode's junk guard: a title must carry a year
_DISC = re.compile(r"^d\d+$")  # rip/<type>/<Name (Year)>/d{disc} staging subdir
_GLUED = re.compile(r"-[A-Z][0-9]+$")  # MakeMKV disc tag glued onto the title

# parse order matches migrate_layout.parse_name: EP, EXTRA, TITLE
_RXS = ((transcode._RX_EP, "ep"), (transcode._RX_EXTRA, "extra"),
        (transcode._RX_TITLE, "title"))


def _part(m: re.Match) -> str:
    return f".p{m.group('pn')}" if m.group("part") else ""


def parse_yearless(name: str) -> tuple[str, str] | None:
    """(cleaned title, staged suffix) for a staged name whose title carries
    no year -- the failed rename this backfill completes. None for anything
    else: a junk label (mid-rip), a conforming name (already carries a
    year), or an unknown shape (migrate_layout's report covers those)."""
    if _JUNK.match(name):
        return None
    for rx, kind in _RXS:
        m = rx.match(name)
        if not m:
            continue
        title = _GLUED.sub("", m.group("movie"))
        if _YEAR.search(title):
            return None  # conforming: the rename already happened
        if kind == "ep":
            suffix = f"_S{m.group('sn')}E{m.group('en')}" + (m.group("tail") or "")
        elif kind == "extra":
            suffix = f" - Extra {m.group('num')}" + _part(m)
        else:  # _RX_TITLE: _tNN (title) or _eNN (legacy extra title)
            suffix = f"_{m.group('sfx')}{m.group('num')}" + _part(m)
        return title, suffix
    return None


def find_groups(root: Path) -> dict[tuple[str, str], list[tuple[Path, str]]]:
    """Yearless leftovers grouped by (type dir, cleaned title): the flat
    files of rip/<type>/ and the files in its d{disc}/ dirs -- the two
    shapes migrate_layout re-trees. Everything else (junk labels,
    conforming names, unexpected shapes) is invisible here."""
    groups: dict[tuple[str, str], list[tuple[Path, str]]] = {}
    rip = root / "rip"
    if not rip.is_dir():
        return groups
    for typedir in sorted(p for p in rip.iterdir() if p.is_dir()):
        for level1 in sorted(typedir.iterdir()):
            if level1.is_file():
                files = [level1]
            elif _DISC.match(level1.name):
                files = sorted(f for f in level1.iterdir() if f.is_file())
            else:
                continue
            for f in files:
                parsed = parse_yearless(f.name)
                if parsed:
                    groups.setdefault((typedir.name, parsed[0]), []).append(
                        (f, parsed[1]))
    return groups


def staged_stem(pick: tmdb.TMDBPick) -> str:
    """The canonical <Name (Year)> stem, built exactly like the rip flow
    names a title folder (skive/rip_common.py: display)."""
    display = pick.title
    if pick.year and pick.year != "-":
        display = f"{pick.title} ({pick.year})"
    return sanitize(display)


def _plan_renames(groups, picks) -> tuple[list[tuple[Path, Path]], list[tuple[Path, str]]]:
    """(renames, skips): src -> dst in place, plus refusals. Groups that
    picked one canonical stem into the same directory would collide on a
    shared _tNN -- they become parts, .pN in group (title) order, the
    contract's shape for a movie split across discs."""
    dirs_of = defaultdict(set)  # (dir, stem) -> the groups that picked it
    for key, files in groups.items():
        for src, _suffix in files:
            dirs_of[(src.parent, picks[key])].add(key)
    part_of: dict[tuple[tuple[str, str], Path], int] = {}
    for (_d, _stem), keys in dirs_of.items():
        if len(keys) > 1:
            for i, key in enumerate(sorted(keys, key=lambda k: k[1]), 1):
                part_of[(key, _d)] = i
    renames: list[tuple[Path, Path]] = []
    skips: list[tuple[Path, str]] = []
    dst_seen: set[Path] = set()
    for key, files in sorted(groups.items()):
        for src, suffix in files:
            part = part_of.get((key, src.parent))
            # a file already wearing its own .pN keeps it; two parted groups
            # on one stem can still collide -- dst_seen reports that
            p = "" if re.search(r"\.p\d+$", suffix) else (f".p{part}" if part else "")
            dst = src.parent / f"{picks[key]}{suffix}{p}.mkv"
            if dst.exists() or dst in dst_seen:
                skips.append((src, "target exists"))
                continue
            dst_seen.add(dst)
            renames.append((src, dst))
    return renames, skips


def run_backfill(root: Path, apply_mode: bool, picker=None) -> int:
    """Pick per group, print the rename plan (or execute it), and report.
    Returns 0; a missing root is the only error (exit 1, like migrate)."""
    if not root.is_dir():
        print(f"backfill_years: {root} is not a directory", file=sys.stderr)
        return 1
    groups = find_groups(root)
    if not groups:
        print(f"nothing to backfill under {root}")
        return 0
    if picker is None:
        tmdb.require_online()
        picker = tmdb.search_loop
    n_files = sum(len(fs) for fs in groups.values())
    print(f"{len(groups)} title group(s), {n_files} file(s) to confirm")
    picks: dict[tuple[str, str], str] = {}
    no_year = 0
    for (type_name, title), files in sorted(groups.items()):
        kind = "movie" if type_name == "movies" else "tv"
        pick = picker(kind, prefill=title)
        # A TMDB entry without a release date would rename a file onto
        # itself (the stem equals its own title); the year is the point.
        while not (pick.year and pick.year != "-"):
            manual = input(
                f'  TMDB has no year for "{title}" -- type one '
                "(4 digits; Enter skips this group): ").strip()
            if not manual:
                pick = None
                break
            if re.fullmatch(r"\d{4}", manual):
                pick.year = manual
            else:
                print("  year must be 4 digits")
        if pick is None:
            no_year += 1
            print(f'skip group "{title}" ({len(files)} file(s), no year '
                  "-- left in place)")
            continue
        stem = staged_stem(pick)
        picks[(type_name, title)] = stem
        print(f'pick "{title}" -> {stem}')
    renames, skips = _plan_renames(
        {k: v for k, v in groups.items() if k in picks}, picks)
    if apply_mode:
        done = 0
        for src, dst in renames:
            try:
                src.rename(dst)
            except OSError as e:
                skips.append((src, f"rename failed: {e}"))
                continue
            done += 1
            print(f"rename {src} -> {dst}")
    else:
        for src, dst in renames:
            print(f"would rename {src} -> {dst}")
    for p, reason in skips:
        print(f"skip {p} ({reason} -- left in place)")
    print(f"backfill report -- {root}")
    print(f"{len(renames)} rename(s), {len(skips)} skipped"
          + (f", {no_year} group(s) skipped (no year)" if no_year else ""))
    if not apply_mode:
        print("dry-run: nothing changed (picks discarded -- re-run with --apply)")
    elif renames:
        print("re-run migrate_layout.py to re-tree the renamed files")
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="backfill_years.py",
        description="One-off backfill: per-title TMDB pick for staged rip leftovers "
                    "whose rename never wrote the year, renaming them in place to "
                    "the canonical <Name (Year)> staged names. Dry-run by default.",
    )
    p.add_argument("--apply", action="store_true",
                   help="execute the planned renames (default: dry-run)")
    p.add_argument("--root", metavar="PATH", type=Path, default=None,
                   help="videos share root (default: $VIDEOS_MOUNT, else /mnt/videos)")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    root = args.root or Path(os.environ.get("VIDEOS_MOUNT", "/mnt/videos"))
    sys.exit(run_backfill(root, args.apply))


if __name__ == "__main__":
    main()
