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
shapes, bare " - Extra NN" names in a flat context the library cannot type
(title homed in neither -- or in both -- of the movie and TV homes), paths
that would traverse a symlink -- is reported and left in place: never
touched, never overwritten, never followed outside the root.
Old text rip.log files are left in place, unread. After the moves (apply
only) now-empty d{disc} dirs and empty title folders under rip/, lossless/
and library/ are pruned; the managed roots themselves are never removed.

Names are parsed by windows/transcode.py's staged-name contracts
(_RX_TITLE / _RX_EP / _RX_EXTRA + the title-year guard) -- the same regexes
the transcoder matches, imported rather than duplicated (the module imports
cleanly on Linux; the test suite already does it).

--mock [PATH] (default /tmp/migrate-layout-mock) rehearses the APPLY against
a structure-only mirror of the MANAGED roots (library/, rip/, rip-DONE/,
lossless/, issues/, logs/) built at PATH: real dirs (empty ones included),
empty files in place of media, recreated symlinks -- the source root itself
is never listed (only read below the managed roots), the mirror is the sole
mutation target.

Usage:  python scripts/migrate_layout.py [--apply] [--root PATH] [--mock [PATH]]
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
_TYPE_DIRS = ("movies", "tv")  # structural children of lossless/
# the managed roots the mock mirrors -- and nothing else: the source root
# itself is never listed (a sandboxed share may refuse to list it while
# serving the subtrees fine), so nothing outside these roots crosses over
_MANAGED_ROOTS = ("library", "rip", "rip-DONE", "lossless", "issues", "logs")


def _through_symlink(root: Path, p: Path) -> bool:
    """True when any component of p below root is a symlink: mkdir/rename/
    rmdir on p could then land outside the managed tree. Components are
    checked one by one -- planned dst paths may not exist yet, so a
    resolve() containment check cannot see them."""
    try:
        rel = p.relative_to(root)
    except ValueError:
        return True
    cur = root
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            return True
    return False


def parse_name(name: str) -> tuple[str, bool] | str:
    """Parse one staged filename by the transcode contracts.

    Returns (title, is_tv) for a finished, renamed staging file, or a short
    reason string when the file must not be touched: a junk label, a name
    that parses but lost its title year (failed rename), or an unknown shape.

    Type is read from the filename -- rip-DONE is flat, so there is no type
    dir to consult: episodes (_SNNENN[-ENN]) and extras (" - Extra NN") are
    TV; _tNN titles are movies. Extras also parse as TV, but a FLAT extra
    needs evidence beyond the name: movie extras and TV extras share it, so
    the planner types a flat extra by its title's home in the library
    (_extra_type_from_library) and reports the ones the library leaves
    ambiguous. A legacy _eNN extra title also parses
    as a movie extra, matching transcode's everything-not-TV-is-a-movie rule.
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
    entries, and how many inspected files were already in their final place.
    Skips are (path, reason) pairs rendered at print time, so apply can
    re-point entries recorded against a virtual path when the carrying
    rename that would have created it failed (report the real location).
    dst_seen remembers every planned destination so two moves never target
    the same path (idempotence depends on never overwriting). move()/prune()
    refuse any path that traverses a symlink below the root -- mkdir/rename/
    rmdir through it could act outside the managed tree -- and report the
    refusal, returning False so dependents are not planned either."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.moves: list[tuple[Path, Path]] = []
        self.prunes: list[Path] = []
        self.skips: list[tuple[Path, str]] = []
        self.already = 0
        self.dst_seen: set[Path] = set()

    def move(self, src: Path, dst: Path) -> bool:
        """Plan src -> dst; False (and a skip line) when refused: a path
        that traverses a symlink, or a target that exists on disk or is
        already claimed by this run. The symlink check comes first so the
        report names the hazard, not a collision visible only through it."""
        if _through_symlink(self.root, src) or _through_symlink(self.root, dst):
            self.skips.append((src, "path traverses a symlink"))
            return False
        if dst.exists() or dst in self.dst_seen:
            self.skips.append((src, "target exists"))
            return False
        self.dst_seen.add(dst)
        self.moves.append((src, dst))
        return True

    def prune(self, d: Path) -> bool:
        """Plan d for rmdir; False (and a skip line) when refused."""
        if _through_symlink(self.root, d):
            self.skips.append((d, "path traverses a symlink"))
            return False
        self.prunes.append(d)
        return True


def _plan_file_move(plan: Plan, src: Path, dst_dir: Path) -> None:
    """Plan src -> dst_dir/src.name via Plan.move (collision and symlink
    checks; refusals are reported and the source left in place)."""
    plan.move(src, dst_dir / src.name)


def _report_unparsed(plan: Plan, f: Path, parsed: str) -> None:
    reason = "old text rip.log -- left unread" if f.name == "rip.log" else parsed
    plan.skips.append((f, reason))


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
            plan.skips.append((entry, "not a folder"))


def _plan_library_tv(root: Path, plan: Plan) -> None:
    """Class 1b: the old library/TV tree. On a case-insensitive filesystem TV
    and tv resolve to one directory -- done, nothing to do. With both present
    on a case-sensitive filesystem, merge TV's children into tv/ (collisions
    reported in place) and drop the emptied TV. Otherwise: a plain rename."""
    lib = root / "library"
    tv, tv_old = lib / "tv", lib / "TV"
    if tv_old.exists() and not tv_old.is_dir():
        plan.skips.append((tv_old, "not a folder"))
        return
    if not tv_old.is_dir():
        return
    if tv.is_dir():
        if os.path.samefile(tv_old, tv):
            plan.already += 1  # same dir under two spellings: already migrated
            return
        for child in sorted(tv_old.iterdir()):
            if child.is_file():
                # typically an old rip.log stray: report, keep, and let it
                # keep TV/ alive rather than carrying it into the new tree
                plan.skips.append((child, "unexpected shape"))
                continue
            _plan_file_move(plan, child, tv)
        moved = {s for s, _ in plan.moves}
        if all(c in moved for c in tv_old.iterdir()):
            plan.prune(tv_old)  # everything merges away: TV empties out
    else:
        plan.move(tv_old, tv)


def _title_dir(lossless_root: Path, title: str, is_tv: bool) -> Path:
    return lossless_root / ("tv" if is_tv else "movies") / title


def _extra_type_from_library(root: Path, title: str) -> bool | None:
    """Type evidence for a FLAT-context extra, read from the library: the
    title's own home decides. A movie title rests at the library root (any
    entry there -- parsed titles always carry a year, so they can never
    collide with the structural movies/tv/TV names) or under movies/; a TV
    title under TV/ or tv/ (the pre- and post-move spellings). All four
    homes are checked so the verdict is the same in the dry-run world (title
    at the root), after the library re-tree, and on idempotent re-runs.
    Homeless, or homed on both sides, the name stays a guess: None -- no
    guessing."""
    lib = root / "library"
    as_movie = (lib / title).is_dir() or (lib / "movies" / title).is_dir()
    as_tv = (lib / "TV" / title).is_dir() or (lib / "tv" / title).is_dir()
    if as_movie and not as_tv:
        return False  # movie homes only: a movie extra
    if as_tv and not as_movie:
        return True  # TV homes only: a TV extra
    return None  # both homes or neither: still ambiguous


def _plan_flat_extra(root: Path, plan: Plan, src: Path, title: str) -> None:
    """A bare " - Extra NN" file in a flat context (a rip-DONE child, or a
    stray at the lossless/ root): movie extras and TV extras share the name
    and no type dir positions it, so the library does -- the extra re-trees
    to lossless/<type>/<title>/ wherever the library homes <title>
    (_extra_type_from_library). Ambiguous evidence (both homes or neither)
    is reported, not guessed; collisions and symlinked paths are refused by
    Plan.move as for any other move."""
    is_tv = _extra_type_from_library(root, title)
    if is_tv is None:
        plan.skips.append((src, "ambiguous extra (no type dir: tv or movies?)"))
        return
    _plan_file_move(plan, src, _title_dir(root / "lossless", title, is_tv))


def _plan_lossless_child(root: Path, plan: Plan, child: Path) -> None:
    """One rip-DONE child in merge mode (lossless/ already exists): a flat
    staged file re-trees straight to its final lossless/<type>/<Title>/
    folder; anything else is reported and left in rip-DONE (so rip-DONE is
    only pruned once it holds nothing). A bare " - Extra NN" file is typed
    by its title's library home (_plan_flat_extra); with no single home it
    is reported as ambiguous instead of guessed: with no type dir, movie
    extra and TV extra share the name."""
    if not child.is_file():
        plan.skips.append((child, "unexpected shape"))
        return
    parsed = parse_name(child.name)
    if isinstance(parsed, str):
        _report_unparsed(plan, child, parsed)
        return
    title, is_tv = parsed
    if is_tv and transcode._RX_EXTRA.match(child.name):
        _plan_flat_extra(root, plan, child, title)
        return
    _plan_file_move(plan, child, _title_dir(root / "lossless", title, is_tv))


def _plan_flat_children(root: Path, plan: Plan, listing: Path, virtual: Path) -> None:
    """Re-tree the flat files of one directory. `listing` is where the files
    live now; `virtual` where the dir sits once earlier planned renames ran
    (rip-DONE -> lossless), so printed plan lines show the real sequential
    result. Subdirectories are not flat files: they ride along silently.
    A bare " - Extra NN" file is typed by its title's library home
    (_plan_flat_extra); with no single home it is reported as ambiguous
    instead of guessed: with no type dir, movie extra and TV extra share
    the name."""
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
        if is_tv and transcode._RX_EXTRA.match(child.name):
            _plan_flat_extra(root, plan, virtual / child.name, title)
            continue
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
        plan.skips.append((done, "not a folder"))
    elif done.is_dir() and lossless.is_dir():
        for child in sorted(done.iterdir()):
            _plan_lossless_child(root, plan, child)
        moved = {s for s, _ in plan.moves}
        if all(c in moved for c in done.iterdir()):
            plan.prune(done)
    elif done.is_dir():
        if lossless.exists():
            plan.skips.append((done, "lossless exists but is not a folder"))
        elif plan.move(done, lossless):
            # dependents are planned only when the carrying rename was: if it
            # was refused (symlinked path), the virtual lossless/ paths would
            # be fiction
            _plan_flat_children(root, plan, listing=done, virtual=lossless)
    if lossless.is_dir():
        if _through_symlink(root, lossless):
            plan.skips.append((lossless, "path traverses a symlink"))
        else:
            for child in sorted(lossless.iterdir()):
                if child.is_dir() and child.name not in _TYPE_DIRS:
                    # a stray dir at the lossless root is nobody's title tree
                    plan.skips.append((child, "unexpected shape"))
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
            plan.skips.append((f, "junk label"))
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
            plan.skips.append((e, "unexpected shape"))


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
                        plan.skips.append((f, "unexpected shape"))
            elif _YEAR.search(level1.name):
                _count_new_shape(plan, level1)
            else:
                plan.skips.append((level1, "unexpected shape"))


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
        plan.prune(d)
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


def _real_path(p: Path, failed: list[tuple[Path, Path]]) -> Path:
    """p re-pointed at the real on-disk location: when a carrying rename
    src -> dst failed, planned paths under dst still rest under src."""
    for dst, src in failed:
        if p == dst or dst in p.parents:
            return src / p.relative_to(dst)
    return p


def _apply(plan: Plan) -> tuple[int, int]:
    """Execute the plan: every move is a rename into a mkdir'd parent, a
    re-checked target, and never an overwrite; a failed move un-does the
    scaffold dirs its mkdir created (only while empty); prunes are rmdir,
    which the OS refuses on a non-empty dir. Returns (moved, pruned)."""
    moved = 0
    failed_carries: list[tuple[Path, Path]] = []  # (virtual dst, real src)
    for src, dst in plan.moves:
        created: list[Path] = []  # scaffold this attempt mkdirs, deepest first
        try:
            d = dst.parent
            while not d.exists() and d != d.parent:
                created.append(d)
                d = d.parent
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                plan.skips.append((src, "target exists"))
                continue
            src.rename(dst)
        except OSError as e:
            plan.skips.append((src, f"move failed: {e}"))
            for d in created:
                try:
                    d.rmdir()
                except OSError:
                    break  # a parent of an unremovable dir is not empty either
            failed_carries.append((dst, src))
            continue
        moved += 1
        print(f"move {src} -> {dst}")
    if failed_carries:
        # a failed carrying rename leaves its dependents where they really
        # are: cite the pre-rename location, not the virtual in-root path
        plan.skips = [(_real_path(p, failed_carries), reason)
                      for p, reason in plan.skips]
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
    plan = Plan(root)
    if (root / "rip.log").is_file():
        # old text log: left in place, unread (report only, per contract)
        plan.skips.append((root / "rip.log", "old text rip.log -- left unread"))
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
    for p, reason in plan.skips:
        print(f"skip {p} ({reason} -- left in place)")

    n_moves = moved if apply_mode else len(plan.moves)
    n_prunes = pruned if apply_mode else len(plan.prunes)
    print(f"migrate report -- {root}")
    print(f"{n_moves} move(s), {len(plan.skips)} skipped, {plan.already} already-correct"
          + (f", {n_prunes} pruned" if n_prunes else ""))
    if not apply_mode:
        print("dry-run: nothing changed (use --apply to execute)")
    return 0


def _mock_symlink(src: Path, dst: Path) -> None:
    """Recreate src's symlink at dst verbatim (the target string is copied,
    so a dangling target stays dangling, exactly like the source); if the OS
    refuses, an empty file stands in and a note is printed."""
    try:
        os.symlink(os.readlink(src), dst)
    except OSError as e:
        print(f"note: could not symlink {dst} ({e}) -- wrote an empty file")
        dst.touch()


def _build_mock_tree(source: Path, mirror: Path) -> tuple[int, int, int]:
    """Mirror source's MANAGED roots into mirror: real dirs (empty ones
    included), empty files in place of regular files, recreated symlinks
    (os.walk with followlinks=False: a symlinked dir is mirrored as the
    symlink alone and never descended into). Each managed root that exists
    is walked as a subtree; the source root itself is never listed -- a
    sandboxed share may refuse to list it -- and nothing outside the managed
    roots belongs to the migration. Returns (dirs, files, symlinks) mirrored;
    the source is only ever read."""
    mirror.mkdir(parents=True, exist_ok=True)  # caller vetted PATH first
    ndir = nfile = nlink = 0
    for root_name in _MANAGED_ROOTS:
        managed = source / root_name
        if managed.is_symlink():
            # exactly as within the walk: mirrored as the link alone, never
            # descended into
            _mock_symlink(managed, mirror / root_name)
            nlink += 1
            continue
        if not managed.is_dir():
            continue  # absent: nothing to mirror
        (mirror / root_name).mkdir()  # the managed root exists in the mirror
        ndir += 1
        for dirpath, dirnames, filenames in os.walk(managed, followlinks=False):
            rel = Path(dirpath).relative_to(source)
            for name in dirnames:
                src, dst = Path(dirpath) / name, mirror / rel / name
                if src.is_symlink():
                    _mock_symlink(src, dst)
                    nlink += 1
                else:
                    dst.mkdir()  # parent already walked, so it exists
                    ndir += 1
            for name in filenames:
                src, dst = Path(dirpath) / name, mirror / rel / name
                if src.is_symlink():
                    _mock_symlink(src, dst)
                    nlink += 1
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.touch()  # empty: no media bytes cross into the mirror
                    nfile += 1
    return ndir, nfile, nlink


def run_mock(source: Path, mirror: Path) -> int:
    """--mock: build a structure-only mirror of source's managed roots at
    mirror, then run the FULL migration (plan + apply) against the mirror and
    point at it for
    inspection. The source is never written; the mirror is the only target.
    PATH is refused unless absent or an empty directory."""
    if not source.is_dir():
        print(f"migrate_layout: {source} is not a directory", file=sys.stderr)
        return 1
    if mirror.exists() or mirror.is_symlink():
        if mirror.is_symlink() or not mirror.is_dir() or any(mirror.iterdir()):
            print(f"migrate_layout: {mirror} already exists and is not empty"
                  " (remove it first)", file=sys.stderr)
            return 1
    try:
        ndir, nfile, nlink = _build_mock_tree(source, mirror)
    except OSError as e:
        print(f"migrate_layout: could not build the mock tree: {e}",
              file=sys.stderr)
        return 1
    print(f"mock tree at {mirror} (structure only, empty files): "
          f"{ndir} dir(s), {nfile} file(s), {nlink} symlink(s)")
    rc = run_migration(mirror, apply_mode=True)
    print(f"inspect with: find {mirror} -type d | sort")
    return rc


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
    p.add_argument("--mock", nargs="?", const=Path("/tmp/migrate-layout-mock"),
                   metavar="PATH", type=Path, default=None,
                   help="rehearse the apply on a structure-only mirror of the "
                        "managed roots at PATH (default: /tmp/migrate-layout-mock): "
                        "empty files, no media bytes; the source is never written")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    root = args.root or Path(os.environ.get("VIDEOS_MOUNT", "/mnt/videos"))
    if args.mock is not None:
        sys.exit(run_mock(root, args.mock))
    sys.exit(run_migration(root, args.apply))


if __name__ == "__main__":
    main()
