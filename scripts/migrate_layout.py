#!/usr/bin/env python3
"""One-shot migration: re-tree the videos share from the old layout to the
per-title layout (slice 7) -- library/{movies,tv}, lossless/, and
rip/<type>/<Name (Year)>/ staging.

Old shape -> new shape:
  library/<entry>/            -> library/movies/<entry>/
  library/TV/                 -> library/tv/            (merged if tv/ exists)
  rip-DONE/                   -> lossless/              (merged if lossless/ exists)
  rip-DONE/*.mkv (flat)       -> lossless/<type>/<Name (Year)>/<file>
  rip/<type>/<file>.mkv       -> rip/<type>/<Name (Year)>/<file>     (flat leftover)
  rip/<type>/dN/<file>.mkv    -> rip/<type>/<Name (Year)>/<file>     (old per-disc dir)

Dry-run by default: prints every move it WOULD make; --apply executes.
Idempotent: a migrated tree yields zero moves. Anything unclassifiable --
junk labels, unparseable or yearless names, target collisions, unexpected
shapes -- is reported and left in place: never touched, never overwritten.
Old text rip.log files are left in place, unread. After the moves (apply
only) now-empty d{disc} dirs and empty title folders under rip/, lossless/
and library/ are pruned; the managed roots themselves are never removed.

Names are parsed by windows/transcode.py's staged-name contracts
(_RX_TITLE / _RX_EP / _RX_EXTRA + the title-year guard) -- the same regexes
the transcoder matches, imported rather than duplicated (the module imports
cleanly on Linux; the test suite already does it).

Usage:  python scripts/migrate_layout.py [--apply] [--root PATH]
        (default root: $VIDEOS_MOUNT, like the skive scripts)
"""

import argparse
import os
import re
import sys
from pathlib import Path

_repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo / "windows"))  # transcode.py does `import subs` beside itself
import transcode  # staged-name contracts: _RX_TITLE, _RX_EP, _RX_EXTRA

# MakeMKV's throwaway staging label (CONTEXT.md: Junk label), e.g. B1_t00.mkv.
_JUNK = re.compile(r"^[A-Z][0-9]+_t\d+\.mkv$")
_YEAR = re.compile(r"\(\d{4}\)$")  # transcode's junk guard: a title must carry a year
_DISC = re.compile(r"^d\d+$")  # drive subdir, old and new staging shapes alike


def parse_name(name: str) -> tuple[str, bool] | str:
    """Parse one staged filename by the transcode contracts.

    Returns (title, is_tv) for a finished, renamed staging file, or a short
    reason string when the file must not be touched: a junk label, a name
    that parses but lost its title year (failed rename), or an unknown shape.

    Type is read from the filename -- rip-DONE is flat, so there is no type
    dir to consult: episodes (_SNNENN[-ENN]) and extras (" - Extra NN") are
    TV; _tNN titles are movies. A legacy _eNN extra title also parses as a
    movie extra, matching transcode's everything-not-TV-is-a-movie rule.
    """
    if _JUNK.match(name):
        return "junk label"
    for rx, is_tv in ((transcode._RX_EP, True), (transcode._RX_EXTRA, True),
                      (transcode._RX_TITLE, False)):
        m = rx.match(name)
        if m:
            title = m.group("movie")
            if _YEAR.search(title):
                return title, is_tv
            return "failed rename (no title year)"
    return "unparseable"


class Plan:
    """The migration plan: moves (src -> dst), prune candidates, skip-report
    lines, and how many inspected files were already in their final place.
    dst_seen remembers every planned destination so two moves never target
    the same path (idempotence depends on never overwriting)."""

    def __init__(self) -> None:
        self.moves: list[tuple[Path, Path]] = []
        self.prunes: list[Path] = []
        self.skips: list[str] = []
        self.already = 0
        self.dst_seen: set[Path] = set()


def _plan_file_move(plan: Plan, src: Path, dst_dir: Path) -> None:
    """Plan src -> dst_dir/src.name; a target that exists on disk or is
    already claimed by this run is reported and the source left in place."""
    dst = dst_dir / src.name
    if dst.exists() or dst in plan.dst_seen:
        plan.skips.append(f"skip {src} (target exists -- left in place)")
        return
    plan.dst_seen.add(dst)
    plan.moves.append((src, dst))


def _report_unparsed(plan: Plan, f: Path, parsed: str) -> None:
    reason = "old text rip.log -- left unread" if f.name == "rip.log" else parsed
    plan.skips.append(f"skip {f} ({reason} -- left in place)")


def _plan_library(root: Path, plan: Plan) -> None:
    """Class 1: root entries of library/ that are not movies/, tv/ (or the
    TV/ tree, handled next) move into library/movies/. Non-folders are
    reported and left in place."""
    lib = root / "library"
    if not lib.is_dir():
        return
    for entry in sorted(lib.iterdir()):
        n = entry.name
        if n == "TV":
            continue  # class 1b: the TV -> tv step below owns it
        if n in ("movies", "tv") and entry.is_dir():
            plan.already += sum(1 for _ in entry.iterdir())
            continue
        if entry.is_dir():
            _plan_file_move(plan, entry, lib / "movies")
        else:
            plan.skips.append(f"skip {entry} (not a folder -- left in place)")


def _plan_library_tv(root: Path, plan: Plan) -> None:
    """Class 1b: the old library/TV tree. On a case-insensitive filesystem TV
    and tv resolve to one directory -- done, nothing to do. With both present
    on a case-sensitive filesystem, merge TV's children into tv/ (collisions
    reported in place) and drop the emptied TV. Otherwise: a plain rename."""
    lib = root / "library"
    tv, tv_old = lib / "tv", lib / "TV"
    if tv_old.exists() and not tv_old.is_dir():
        plan.skips.append(f"skip {tv_old} (not a folder -- left in place)")
        return
    if not tv_old.is_dir():
        return
    if tv.is_dir():
        if os.path.samefile(tv_old, tv):
            plan.already += 1  # same dir under two spellings: already migrated
            return
        for child in sorted(tv_old.iterdir()):
            _plan_file_move(plan, child, tv)
        moved = {s for s, _ in plan.moves}
        if all(c in moved for c in tv_old.iterdir()):
            plan.prunes.append(tv_old)  # everything merges away: TV empties out
    else:
        plan.moves.append((tv_old, tv))


def _title_dir(lossless_root: Path, title: str, is_tv: bool) -> Path:
    return lossless_root / ("tv" if is_tv else "movies") / title


def _plan_lossless_child(root: Path, plan: Plan, child: Path) -> None:
    """One rip-DONE child in merge mode (lossless/ already exists): a flat
    staged file re-trees straight to its final lossless/<type>/<Title>/
    folder; anything else is reported and left in rip-DONE (so rip-DONE is
    only pruned once it holds nothing)."""
    if not child.is_file():
        plan.skips.append(f"skip {child} (unexpected shape -- left in place)")
        return
    parsed = parse_name(child.name)
    if isinstance(parsed, str):
        _report_unparsed(plan, child, parsed)
        return
    title, is_tv = parsed
    _plan_file_move(plan, child, _title_dir(root / "lossless", title, is_tv))


def _plan_flat_children(root: Path, plan: Plan, listing: Path, virtual: Path) -> None:
    """Re-tree the flat files of one directory. `listing` is where the files
    live now; `virtual` where the dir sits once earlier planned renames ran
    (rip-DONE -> lossless), so printed plan lines show the real sequential
    result. Subdirectories are not flat files: they ride along silently."""
    for child in sorted(listing.iterdir()):
        if not child.is_file():
            continue
        parsed = parse_name(child.name)
        if isinstance(parsed, str):
            # report at the virtual path: after the planned rename the file
            # rests there, so the plan reads as one sequential story
            _report_unparsed(plan, virtual / child.name, parsed)
            continue
        title, is_tv = parsed
        _plan_file_move(plan, virtual / child.name,
                        _title_dir(root / "lossless", title, is_tv))


def _plan_done(root: Path, plan: Plan) -> None:
    """Class 2: rip-DONE -> lossless. Rename when lossless/ is absent, merge
    into it otherwise; either way the flat staged files re-tree by parsed
    name into lossless/<type>/<Name (Year)>/. An emptied rip-DONE is pruned;
    unparsed stragglers keep it (and keep being reported). Flat files still
    resting at the lossless/ root -- stragglers of a partial earlier run --
    re-tree too."""
    done, lossless = root / "rip-DONE", root / "lossless"
    if done.exists() and not done.is_dir():
        plan.skips.append(f"skip {done} (not a folder -- left in place)")
    elif done.is_dir() and lossless.is_dir():
        for child in sorted(done.iterdir()):
            _plan_lossless_child(root, plan, child)
        moved = {s for s, _ in plan.moves}
        if all(c in moved for c in done.iterdir()):
            plan.prunes.append(done)
    elif done.is_dir():
        if lossless.exists():
            plan.skips.append(
                f"skip {done} (lossless exists but is not a folder -- left in place)")
        else:
            plan.moves.append((done, lossless))
            _plan_flat_children(root, plan, listing=done, virtual=lossless)
    if lossless.is_dir():
        _plan_flat_children(root, plan, listing=lossless, virtual=lossless)


def _plan_staging_file(root: Path, plan: Plan, f: Path, type_name: str) -> None:
    """One old-shape staging file: parse and move up into the title folder
    under its own type dir (positional type, transcode's rule)."""
    parsed = parse_name(f.name)
    if isinstance(parsed, str):
        _report_unparsed(plan, f, parsed)
        return
    title, _is_tv = parsed
    _plan_file_move(plan, f, root / "rip" / type_name / title)


def _count_new_shape(plan: Plan, title_dir: Path) -> None:
    """Files resting directly in a title folder or in its d{disc} subdirs are
    the new shape: already correct, only counted. Junk labels are still
    reported (never counted as content); unexpected subdirs are reported."""
    def count_file(f: Path) -> None:
        if not f.name.endswith(".mkv"):
            return
        if _JUNK.match(f.name):
            plan.skips.append(f"skip {f} (junk label -- left in place)")
        else:
            plan.already += 1

    for e in sorted(title_dir.iterdir()):
        if e.is_file():
            count_file(e)
        elif _DISC.match(e.name):
            for f in sorted(e.iterdir()):
                if f.is_file():
                    count_file(f)
        else:
            plan.skips.append(f"skip {e} (unexpected shape -- left in place)")


def _plan_rip(root: Path, plan: Plan) -> None:
    """Class 3: staging leftovers under rip/. Old shapes are flat files in
    the type dir and files inside old per-disc d{disc} dirs (depth <= 2);
    both parse and move into rip/<type>/<Name (Year)>/. Files already in the
    new shape are counted. Junk labels and unparseable names are reported
    and skipped wherever they rest; strays (unexpected dirs, files directly
    under rip/) are reported, never touched."""
    rip = root / "rip"
    if not rip.is_dir():
        return
    for entry in sorted(rip.iterdir()):
        if entry.is_file():
            parsed = parse_name(entry.name)
            _report_unparsed(plan, entry, parsed if isinstance(parsed, str)
                             else "unexpected shape")
    for typedir in sorted(p for p in rip.iterdir() if p.is_dir()):
        for level1 in sorted(typedir.iterdir()):
            if level1.is_file():
                _plan_staging_file(root, plan, level1, typedir.name)
            elif _DISC.match(level1.name):
                # old per-disc dir: its files are flat leftovers, one level up
                for f in sorted(level1.iterdir()):
                    if f.is_file():
                        _plan_staging_file(root, plan, f, typedir.name)
                    else:
                        plan.skips.append(
                            f"skip {f} (unexpected shape -- left in place)")
            elif _YEAR.search(level1.name):
                _count_new_shape(plan, level1)
            else:
                plan.skips.append(
                    f"skip {level1} (unexpected shape -- left in place)")


def _scan_prune(d: Path, moved: set[Path], plan: Plan) -> bool:
    """True when d is empty, or holds only planned-away files and subdirs
    that prune too (which are appended to the plan on the way)."""
    empty = True
    for e in sorted(d.iterdir()):
        if e in moved:
            continue
        if e.is_dir() and _scan_prune(e, moved, plan):
            continue
        empty = False
    if empty:
        plan.prunes.append(d)
    return empty


def _plan_prunes(root: Path, plan: Plan) -> None:
    """Empty d{disc} dirs and emptied title folders under the managed roots,
    deepest first. First-level dirs (the type dirs, library's movies/tv) are
    structural and never candidates; the managed roots themselves are never
    removed. Skipped files and collisions keep their parent dirs alive."""
    moved = {s for s, _ in plan.moves}
    for rt in (root / "rip", root / "lossless", root / "library"):
        if not rt.is_dir():
            continue
        for first in sorted(rt.iterdir()):
            if first.is_dir():
                for e in sorted(first.iterdir()):
                    if e.is_dir():
                        _scan_prune(e, moved, plan)


def _apply(plan: Plan) -> tuple[int, int]:
    """Execute the plan: every move is a rename into a mkdir'd parent, a
    re-checked target, and never an overwrite; prunes are rmdir, which the
    OS refuses on a non-empty dir. Returns (moved, pruned)."""
    moved = 0
    for src, dst in plan.moves:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                plan.skips.append(f"skip {src} (target exists -- left in place)")
                continue
            src.rename(dst)
        except OSError as e:
            plan.skips.append(f"skip {src} (move failed: {e} -- left in place)")
            continue
        moved += 1
        print(f"move {src} -> {dst}")
    pruned = 0
    for d in plan.prunes:
        try:
            d.rmdir()
        except OSError:
            continue
        pruned += 1
        print(f"prune {d}")
    return moved, pruned


def run_migration(root: Path, apply_mode: bool) -> int:
    """Plan the re-tree, print it (or execute it), and report. Returns 0;
    a missing root is the only error (exit 1, like backfill)."""
    if not root.is_dir():
        print(f"migrate_layout: {root} is not a directory", file=sys.stderr)
        return 1
    if not any((root / n).is_dir()
               for n in ("library", "rip", "rip-DONE", "lossless", "issues")):
        print(f"nothing to do under {root}")
        return 0
    plan = Plan()
    if (root / "rip.log").is_file():
        # old text log: left in place, unread (report only, per contract)
        plan.skips.append(
            f"skip {root / 'rip.log'} (old text rip.log -- left unread -- left in place)")
    _plan_library(root, plan)
    _plan_library_tv(root, plan)
    _plan_done(root, plan)
    _plan_rip(root, plan)
    _plan_prunes(root, plan)

    moved = pruned = 0
    if apply_mode:
        moved, pruned = _apply(plan)
    else:
        for src, dst in plan.moves:
            print(f"would move {src} -> {dst}")
        for d in plan.prunes:
            print(f"would prune {d}")
    for line in plan.skips:
        print(line)

    n_moves = moved if apply_mode else len(plan.moves)
    n_prunes = pruned if apply_mode else len(plan.prunes)
    print(f"migrate report -- {root}")
    print(f"{n_moves} move(s), {len(plan.skips)} skipped, {plan.already} already-correct"
          + (f", {n_prunes} pruned" if n_prunes else ""))
    if not apply_mode:
        print("dry-run: nothing changed (use --apply to execute)")
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="migrate_layout.py",
        description="One-shot re-tree of the videos share to the per-title layout "
                    "(library/{movies,tv}, lossless/, rip/<type>/<Name (Year)>/). "
                    "Dry-run by default; idempotent; anything unclassifiable is "
                    "reported and skipped, never touched.",
    )
    p.add_argument("--apply", action="store_true",
                   help="execute the planned moves and prunes (default: dry-run)")
    p.add_argument("--root", metavar="PATH", type=Path, default=None,
                   help="videos share root (default: $VIDEOS_MOUNT, else /mnt/videos)")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    root = args.root or Path(os.environ.get("VIDEOS_MOUNT", "/mnt/videos"))
    sys.exit(run_migration(root, args.apply))


if __name__ == "__main__":
    main()
