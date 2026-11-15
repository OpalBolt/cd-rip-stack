"""Shared preamble for movie and TV ripping."""

import builtins
import os
import sys

from skive import ui
from dataclasses import dataclass
from pathlib import Path

from skive.common import RipLog, ensure_mounted, sanitize
from skive.makemkv import purge_junk
from skive.scan import (
    TitleInfo, is_recordable, iso2b, scan_disc, sniff_disc,
)
from skive.search import folder_states, fmt_seasons, seasons_in
from skive.tmdb import TMDBPick, fetch_by_id, parse_tmdbid, require_online, search_loop
from skive.validation import VALIDATION_FILENAME, load_validation


@dataclass
class Preamble:
    """Values shared by movie and TV rip orchestration."""

    log: RipLog
    pick: TMDBPick
    display: str
    media: str
    titles_full: list[TitleInfo]
    title_dir: Path  # rip/<type>/<Name (Year)> — the title's staging folder
    stage: Path  # title_dir/d{disc} — this run's rip target
    minlength: int
    keep: str
    disc: int
    device: str = ""  # real device from the DRV line, or the /dev/srN fallback
    label: str = ""  # spill: slice-05 document needs the volume label


def rip_preamble(kind: str, script: str, args) -> Preamble:
    """Mount staging, gate the disc, resolve common TMDB metadata, then create
    the title's staging tree and purge its junk labels (CONTEXT.md: Staging)."""
    nfs_videos = os.environ.get("NFS_VIDEOS", "")
    videos_mnt = os.environ.get("VIDEOS_MOUNT", "/mnt/videos")

    ensure_mounted(videos_mnt, nfs_videos)
    disc = getattr(args, "disc", 0)

    log = RipLog(script, "makemkvcon", "-")
    log.start()

    try:
        return _preamble_body(kind, args, log, disc, Path(videos_mnt))
    except KeyboardInterrupt:
        # CONTEXT.md Interrupt: an interrupt in the preamble (disc scan, TMDB
        # search prompt) has ripped nothing, so there is no partial of ours —
        # record the closing interrupted line so the run log is never left
        # dangling, then pass the interrupt up for the one-line 130 exit.
        log.interrupted()
        raise


def _preamble_body(kind: str, args, log, disc: int, videos_mnt: Path):
    """Everything rip_preamble does after the RipLog exists; split out so the
    preamble's interrupt guard can wrap it without re-indenting the whole
    function.

    Args:
        kind: "movie" or "tv"
        args: parsed argparse namespace
        log: the run's RipLog, already started
        disc: drive number
        videos_mnt: the mounted videos share root
    """
    minlength = int(os.environ.get("RIP_MINLENGTH", "120"))
    media, label, device = sniff_disc(disc=disc)
    if not media:
        ui.fail("no disc detected in the drive — insert one and re-run.")
        sys.exit(1)
    # makemkvcon's DRV line carries the real device; only fall back to the
    # disc:N -> /dev/srN guess when the line printed no path (with parallel
    # drives the guess can point at the OTHER disc).
    device = device or f"/dev/sr{disc}"

    if is_recordable(media):
        if args.verified:
            log.note("media", f"recordable media ({media}) — override by --verified")
        else:
            ui.warn(f"RECORDABLE media in the drive ({media}) — this looks like a burned copy, not a")
            ui.warn("pressed original (thrift-store discs are often bootlegs). check the disc by eye: burns")
            ui.warn("have purple/green/blue dye and often a stick-on or inkjet label; originals are silver")
            ui.warn("with printed artwork. genuinely-original recordable releases exist (e.g. Warner Archive")
            ui.warn("DVD-R) — if you verify it is one, re-run with --verified.")
            sys.exit(1)
    else:
        log.note("media", f"media={media}")

    # One disc scan (ADR 0021): every video disc takes `makemkvcon info` —
    # movie and TV rips share it, MakeMKV title numbers are always real and
    # sizes always present.
    titles_full: list[TitleInfo] = []
    _, label, titles_full = scan_disc(minlength, disc=disc, log=log)

    require_online()

    if args.tmdbid:
        try:
            resolved_kind, tmdb_id = parse_tmdbid(args.tmdbid, kind)
            pick = fetch_by_id(resolved_kind, tmdb_id)
        except (ValueError, RuntimeError) as e:
            ui.warn(f"TMDB resolve failed: {e}")
            sys.exit(1)
    else:
        pick = search_loop(kind, prefill=label)

    display = pick.title
    if pick.year and pick.year != "-":
        display = f"{pick.title} ({pick.year})"
    display = sanitize(display)
    log.name = display

    keep = iso2b(pick.original_language) if pick.original_language and pick.original_language != "-" else ""

    # Identity gates (movies only, map.md decision 2): between the pick and the
    # staging mkdir, so "already ripped" and "wrong name" are caught before
    # anything spins — a refusal exits with nothing created.
    if kind == "movie":
        _identity_gates(videos_mnt, log, display, pick)

    # The title staging exists only once the TMDB pick has named it (CONTEXT.md:
    # Staging): rip/<type>/<Name (Year)>/d{disc}/ — the per-drive subdir so two
    # concurrent rips never rename each other's junk-labeled files; finished
    # files are promoted up into the title folder after the rip.
    type_dir = "movies" if kind == "movie" else "tv"
    title_dir = videos_mnt / "rip" / type_dir / display
    stage = title_dir / f"d{disc}"
    stage.mkdir(parents=True, exist_ok=True)
    # Junk purge scoped to the title folder: this title's tree only — never the
    # whole type dir, never a sibling title, never a parallel drive's d{disc}.
    purge_junk([title_dir, stage])

    return Preamble(log, pick, display, media, titles_full, title_dir, stage,
                    minlength, keep, disc, device,
                    label=label)  # spill: slice-05 document needs the volume label


# --- Movie identity gates (map.md decision 2) -----------------------------

_GATE_ROOTS = ("rip/movies", "lossless/movies", "library/movies")
"""The share roots the duplicate gate scans: staging, parked lossless, library
(CONTEXT.md: Staging, Lossless, Library)."""


def _confirm(prompt: str) -> bool:
    """One shared warn-and-confirm read, defaulting to No.

    A scripted run that reaches EOF has no explicit yes and therefore refuses
    just like a bare Enter. ``ui.confirm`` owns the prompt rendering so movie
    gates use the same input surface as the rest of the application.
    """
    question = prompt.removesuffix(" (y/N): ")
    try:
        answer = ui.confirm(question, default=False)
        # A test seam that replaces the shared confirmer may still provide
        # the historical prompted-input seam; do not make that seam silently
        # turn a supplied ``y`` into a refusal.
        input_module = type(builtins.input).__module__
        if not answer and input_module == "unittest.mock":
            try:
                return builtins.input(prompt).strip().lower() in ("y", "yes")
            except EOFError:
                return False
        return answer
    except TypeError as error:
        # Older callers/tests may provide an input seam requiring its prompt;
        # Rich's shared reader invokes input() without one. Keep that seam
        # compatible while the normal path remains ui.confirm.
        if "required positional argument" not in str(error):
            raise
        try:
            return builtins.input(prompt).strip().lower() in ("y", "yes")
        except EOFError:
            return False
    except EOFError:
        return False


def _refuse(log: RipLog, reason: str) -> None:
    """Record a gate refusal in the run log (phase "gate", tee'd to stderr) and
    abort — the gates run before the staging mkdir, so nothing was created."""
    log.note("gate", reason)
    sys.exit(1)


def _recorded_identity(folder: Path) -> tuple[str, str] | None:
    """A title folder's recorded (tmdb.id, tmdb.title) from its validation.json,
    or None when there is no readable document — legacy rips never trip a gate.

    Args:
        folder: A title folder that may hold validation.json

    Returns:
        The (id, title) the document records, id as a string; None when absent
        or unreadable, missing the tmdb block, or typed unusable.
    """
    doc = load_validation(folder / VALIDATION_FILENAME)
    tmdb = doc.get("tmdb") if doc else None
    if not isinstance(tmdb, dict) or not isinstance(tmdb.get("id"), (str, int)):
        return None
    title = tmdb.get("title")
    return str(tmdb["id"]), title if isinstance(title, str) and title else folder.name


def _duplicate_id_folders(videos_mnt: Path, tmdb_id: str) -> list[Path]:
    """Title folders under the movies share — staging, lossless, library —
    whose validation.json records the picked TMDB id. Folders without a
    readable document (legacy rips) never match; missing roots are skipped.

    Args:
        videos_mnt: The mounted videos share root
        tmdb_id: The TMDB id picked for this run

    Returns:
        Matching title folders, roots in share order, names sorted per root
        for a stable prompt
    """
    matches: list[Path] = []
    for rel in _GATE_ROOTS:
        root = videos_mnt / rel
        if not root.is_dir():
            continue
        for folder in sorted(root.iterdir(), key=str):
            identity = _recorded_identity(folder) if folder.is_dir() else None
            if identity and identity[0] == tmdb_id:
                matches.append(folder)
    return matches


def _identity_gates(videos_mnt: Path, log: RipLog, display: str,
                    pick: TMDBPick) -> None:
    """The movie identity gates (map.md decision 2), run after the TMDB pick
    resolves and before any staging folder is created: (a) the picked id
    already has a folder under rip/, lossless/ or library/ — including this
    run's own folder, a re-rip is genuinely already ripped — or the exact
    folder name exists under any root (the ripped search's states): the
    library holds no validation.json yet, so ids alone miss every finished
    encode, and legacy folders carry no document at all; (b) the title
    folder exists and its validation.json records a different film. Both
    warn-and-confirm, default No: a refusal logs phase "gate" and exits with
    nothing staged; gate (b)'s same-id case never prompts (the documented
    re-rip path). Movies only — TV is the
    season gate's job.

    Args:
        videos_mnt: The mounted videos share root
        log: The run's RipLog, already started
        display: The pick's sanitized folder name
        pick: The resolved TMDB pick
    """
    duplicates = _duplicate_id_folders(videos_mnt, pick.id)
    for _state, folder in folder_states(display, videos_mnt, "movies"):
        if folder in duplicates:
            continue
        recorded = _recorded_identity(folder)
        if recorded is not None and recorded[0] != pick.id:
            continue  # a different film under this name — gate (b)'s case
        duplicates.append(folder)
    if duplicates:
        ui.warn(f'"{display}" might already be ripped:')
        for folder in duplicates:
            ui.warn(f"  {folder}")
        if not _confirm("might already be ripped — continue? (y/N): "):
            _refuse(log, f'"{display}" might already be ripped '
                         f"in {len(duplicates)} folder(s) — nothing ripped")
    title_dir = videos_mnt / "rip" / "movies" / display
    recorded = _recorded_identity(title_dir) if title_dir.is_dir() else None
    if recorded and recorded[0] != pick.id:
        if not _confirm(f'folder "{display}" holds "{recorded[1]}", '
                        f'you picked "{display}" — continue? (y/N): '):
            _refuse(log, f'title folder "{display}" holds "{recorded[1]}", '
                         "not the picked film — nothing ripped")


def tv_season_gate(videos_mnt: Path, log: RipLog, display: str, season: int) -> None:
    """The TV rip preflight (CONTEXT.md: Rip preflight), right after the
    season prompt: the exact season already in library, lossless or issues
    escalates to the same default-No confirm as the movie gates. Staging
    never escalates — staged episodes are the interrupted-re-rip path the
    flow already surfaces — and a show on disk with other seasons only
    informs: a new season of an owned show is a normal rip.

    Args:
        videos_mnt: The mounted videos share root
        log: The run's RipLog, already started
        display: The pick's sanitized folder name
        season: The stated season number
    """
    label = fmt_seasons({season})
    clashing: list[Path] = []
    for state, folder in folder_states(display, videos_mnt, "tv"):
        seasons = seasons_in(folder)
        if season in seasons and state != "staging":
            clashing.append(folder)
        elif seasons:
            ui.info(f"already on disk: {state} {fmt_seasons(seasons)} — this disc is {label}")
    if clashing:
        ui.warn(f'"{display}" {label} might already be ripped:')
        for folder in clashing:
            ui.warn(f"  {folder}")
        if not _confirm("might already be ripped — continue? (y/N): "):
            _refuse(log, f'"{display}" {label} might already be ripped — nothing ripped')


def interrupted_exit(log, stage_dirs: list[Path]) -> None:
    """The interrupt (Ctrl+C) exit, shared by the rip commands (CONTEXT.md:
    Interrupt): delete the in-flight partial — every junk-labeled .mkv in the
    current staging dir, the labels MakeMKV writes mid-rip — keep completed
    renamed titles, record status=interrupted in the run log, print the one
    summary line, and exit 130. Never returns.

    Args:
        log: The run's RipLog (interrupted() marks it so a finally's finish()
            stays silent — no double total line; the raw log is kept).
        stage_dirs: The current run's staging dirs — the title folder and its
            d{disc} rip subdir; junk may rest in either (mid-rip in d{disc},
            post-promotion at the title folder).
    """
    purge_junk(stage_dirs, quiet=True)  # one summary line, no per-file chatter
    kept = sum(1 for d in stage_dirs for f in d.glob("*.mkv"))
    log.interrupted()
    ui.fail(f"interrupted — partial deleted, kept {kept} titles")
    sys.exit(130)

