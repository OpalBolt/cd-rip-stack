"""TV rip flow: season prompt, episode-range prompt, confirm table, renames,
staging inspection, post-rip validation record."""

import os
import re
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from skive import ui
from skive.chapter_split import split_combined
from skive.makemkv import (
    CONFIG_DIR, build_selection, inject_profile, promote_staged, rename_staged,
    rip_header, subtitle_note, rip_titles, hsize,
)
from skive.rip_common import (
    fast_scan_header, interrupted_exit, rip_preamble, tv_season_gate,
)
from skive.scan import resolve_titles, scan_disc
from skive.tmdb import fetch_season
from skive.validation import (
    VALIDATION_FILENAME, _double_runtime, load_validation, merge_tv_document,
    tv_document, tv_row, write_validation,
)
from skive.tv_match import (
    tv_floor, sum_disqualified, parse_range, next_unstaged, assign_range,
    range_mismatch_note, range_past_end_note, apply_fixes, audit_cell,
    playall_skip, playall_only, guess_range,
)

console = Console()


def _hms(secs: int) -> str:
    """Duration as H:MM:SS (the confirm table's format)."""
    return f"{secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def _phase_status(part: str, name: str) -> None:
    """Run view status: the current unattended phase (tty-gated)."""
    v = ui.view()
    if v is not None and v.tty:
        v.status(f"{part}: {name}")


def staged_eps(show_dir: Path, display: str, season: int) -> set[int]:
    """Return episode numbers already staged for this show and season.

    S01E05.mkv -> 5
    S01E05-E06.mkv -> 5 and 6

    Args:
        show_dir: The show's title folder under rip/tv (CONTEXT.md: Staging) —
            episodes rest directly in it after promotion
        display: Display name (Show (Year))
        season: Season number

    Returns:
        Set of episode numbers already staged
    """
    staged = set()
    season_str = f"{season:02d}"
    pattern = re.compile(rf'^{re.escape(display)}_S{season_str}E(\d+)(-E(\d+))?$')
    for mkv in show_dir.glob("*.mkv"):
        base = mkv.stem
        m = pattern.match(base)
        if m:
            lo = int(m.group(1))
            staged.add(lo)
            hi_str = m.group(3)
            if hi_str:
                hi = int(hi_str)
                staged.add(hi)
    return staged


def show_staging(show_dir: Path, display: str) -> list[Path]:
    """Every staged file belonging to one show: its episodes (any season,
    singles and doubles) and its extras. The table's clear command deletes
    exactly these."""
    return [p for p in sorted(show_dir.glob("*.mkv"))
            if p.name.startswith(display + "_S") or p.name.startswith(display + " - ")]


def print_table(display: str, season: int, titles: list[tuple[int, int, int]],
                assignment: dict[int, tuple[int, int]], ep_names: dict[int, str],
                ep_runtimes: dict[int, int], estimate: bool = False,
                playalls: set[int] | None = None) -> None:
    """Print the confirmation table in title-number order.

    The range prompt reuses this printer as the title overview: estimate=True
    marks episode cells ` (est.)`, and playalls renders rows still assigned
    extra "play-all, not ripped" — on the overview and the confirm table
    alike; a fix row rescuing a play-all shows its episode instead.

    Args:
        display: Show name
        season: Season number
        titles: List of (title, secs, bytes)
        assignment: title -> (lo, hi) mapping
        ep_names: episode_number -> name mapping
        ep_runtimes: episode_number -> TMDB runtime seconds
        estimate: Mark episode assignments ` (est.)` (the overview's guess)
        playalls: Title numbers disqualified as play-alls
    """
    season_str = f"{season:02d}"
    console.print(f"\n{display} — season {season_str}")

    table = Table()
    table.add_column("title", style="cyan", width=6)
    table.add_column("duration", style="white", width=9)
    table.add_column("size", style="dim", width=9)
    table.add_column("audit", width=6)
    table.add_column("assignment", style="yellow")

    for t, secs, size in sorted(titles, key=lambda x: x[0]):
        # Format duration H:MM:SS
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        dur = f"{h}:{m:02d}:{s:02d}"

        # Fast-scan titles carry no size (lsdvd knows durations only) — no 0.0 B row
        sz = hsize(size) if size else ""

        lo, hi = assignment.get(t, (0, 0))
        audit = audit_cell(secs, lo, hi, ep_runtimes)
        audit_str = Text(audit[0], style=audit[1]) if audit else ""

        # Only a play-all still assigned extra reads skipped: a fix row handing
        # one an episode must show the rescue here (pin_playalls is not re-applied).
        if playalls and t in playalls and lo == 0:
            assign_str = "→ play-all, not ripped"
        elif lo == 0:
            assign_str = "→ extra"
        else:
            code = f"S{season_str}E{lo:02d}"
            if hi != lo:
                code += f"-E{hi:02d}"
            name = ep_names.get(lo, "")
            if name:
                assign_str = f"→ {code} ({name})"
            else:
                assign_str = f"→ {code}"
            if estimate:
                assign_str += " (est.)"

        table.add_row(str(t), dur, sz, audit_str, assign_str)

    console.print(table)


def episode_target(show_dir: Path, display: str, season: int, lo: int, hi: int) -> Path:
    """Target path for an episode assignment: single or double episode name.

    Shared by rename_tv (full scan) and rename_tv_fast (duration join).
    Existence is the caller's concern (never clobber).

    Args:
        show_dir: The show's title folder under rip/tv
        display: Show name
        season: Season number
        lo: First episode number of the assignment
        hi: Last episode number of the assignment

    Returns:
        The target .mkv path
    """
    season_str = f"{season:02d}"
    if lo == hi:
        return show_dir / f"{display}_S{season_str}E{lo:02d}.mkv"
    return show_dir / f"{display}_S{season_str}E{lo:02d}-E{hi:02d}.mkv"


def rename_tv(display: str, show_dir: Path, season: int, assignment: dict[int, tuple[int, int]]) -> None:
    """Rename staged files per the episode map.

    Junk-labeled titles become <display>_SNNENN[-ENNN].mkv or <display> - Extra NN.mkv.
    Never clobbers existing targets.

    Args:
        display: Show name
        show_dir: The show's title folder under rip/tv — the promoted files
            renamed here rest directly in it
        season: Season number
        assignment: title -> (lo, hi) mapping
    """
    for mkv in show_dir.glob("*.mkv"):
        base = mkv.stem
        # Extract _tNN
        m = re.search(r'_t(\d+)$', base)
        if not m:
            continue
        title_num = int(m.group(1))

        lohi = assignment.get(title_num)
        if lohi is None:
            # No mapping for this title - skip with warning
            ui.warn(f"no episode for title {title_num} ({base}) — left alone")
            continue

        lo, hi = lohi
        if lo == 0:
            # Extra - roll "Extra NN" past existing
            m_val = 1
            while (show_dir / f"{display} - Extra {m_val:02d}.mkv").exists():
                m_val += 1
            target = show_dir / f"{display} - Extra {m_val:02d}.mkv"
        else:
            target = episode_target(show_dir, display, season, lo, hi)

        # Check for clobber
        if target.exists():
            ui.warn(f"{target.name} already exists — {base}.mkv left alone")
            continue

        mkv.rename(target)
        cur = base[:m.start()]
        ui.info(f"{cur} → {target.stem}")


def rename_tv_fast(display: str, show_dir: Path, season: int,
                   assignment: dict[int, tuple[int, int]], scan_durations: list[int],
                   run=subprocess.run) -> bool:
    """Join staged junk files to scanner titles by duration, then rename.

    The fast scan's title numbers are positional and never match MakeMKV's,
    so every staged _tNN file is ffprobed and joined to the scanner title
    with the same duration (±1s) before the episode map is applied. An
    assigned episode with no staged file aborts before ANY rename — never
    guess. Unmatched extras, losers of a duplicate join, and unjoinable
    files stay junk-labeled with one warning each.

    Args:
        display: Show name
        show_dir: The show's title folder under rip/tv — the promoted files
            joined and renamed here rest directly in it
        season: Season number
        assignment: Scanner title number -> (lo, hi) mapping (table's keys)
        scan_durations: Scanner durations in scan order (index == title number)
        run: subprocess.run callable for monkeypatching

    Returns:
        True on success (renamed or warned); False when aborted — an
        assigned episode had no staged file (message already printed). The
        caller owns the exit so the abort still reaches rip.log.
    """
    ffprobe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                   "-of", "csv=p=0"]
    staged = []  # (path, _tNN, duration seconds)
    for mkv in show_dir.glob("*.mkv"):
        m = re.search(r'_t(\d+)$', mkv.stem)
        if not m:
            continue
        result = run(ffprobe_cmd + [str(mkv)], capture_output=True, text=True)
        try:
            fdur = int(float(result.stdout.strip()))
        except ValueError:
            ui.warn(f"cannot read duration of {mkv.name} — left junk-labeled")
            continue
        staged.append((mkv, int(m.group(1)), fdur))

    # Join: scanner title order, lowest _tNN wins, losers stay junk.
    claimed: set[Path] = set()
    losers: set[Path] = set()
    matched: dict[int, Path] = {}
    for i, d in enumerate(scan_durations):
        candidates = sorted((s for s in staged if s[0] not in claimed and abs(s[2] - d) <= 1),
                            key=lambda s: s[1])
        if not candidates:
            continue
        matched[i] = candidates[0][0]
        claimed.add(candidates[0][0])
        losers.update(loser[0] for loser in candidates[1:])
    # One warning per unclaimed file, only after the join: a file warned as
    # title i's loser can still be claimed by a later title j with the same
    # duration, and a file losing two joins must not be warned twice.
    for mkv, _tnum, _fdur in staged:
        if mkv in claimed:
            continue
        if mkv in losers:
            ui.warn(f"{mkv.name} matches a duplicate duration — left junk-labeled")
        else:
            ui.warn(f"{mkv.name} matches no scanned title — left junk-labeled")

    # Abort before touching anything: an episode without its file is a bug
    # (wrong disc, failed rip) — renaming the rest would mislabel the season.
    for i in range(len(scan_durations)):
        lo, hi = assignment.get(i, (0, 0))
        if lo >= 1 and i not in matched:
            code = f"E{lo:02d}" if lo == hi else f"E{lo:02d}-E{hi:02d}"
            ui.fail(f"title {i} ({code}) has no staged file — rename aborted, files left junk-labeled")
            return False

    for i in sorted(matched):
        mkv = matched[i]
        lo, hi = assignment.get(i, (0, 0))
        if lo == 0:
            # Extra - roll "Extra NN" past existing
            m_val = 1
            while (show_dir / f"{display} - Extra {m_val:02d}.mkv").exists():
                m_val += 1
            target = show_dir / f"{display} - Extra {m_val:02d}.mkv"
        else:
            target = episode_target(show_dir, display, season, lo, hi)

        # Check for clobber
        if target.exists():
            ui.warn(f"{target.name} already exists — {mkv.name} left alone")
            continue

        mkv.rename(target)
        cur = re.sub(r'_t\d+$', '', mkv.stem)
        ui.info(f"{cur} → {target.stem}")

    return True


def chapter_split_hook(preamble, season: int,
                       titles_info: list[tuple[int, int, int]],
                       playalls: set[int], assignment: dict[int, tuple[int, int]],
                       ep_runtimes: dict[int, int], run=subprocess.run) -> None:
    """The chapter split (ADR 0009) for play-all-only discs: when the final
    episode map leaves exactly one eligible title carrying a multi-episode
    range, its combined `_SNNENN-ENNN.mkv` is probed, confirmed at the
    boundary table, and cut into per-episode files. Runs after the rename on
    both scan paths, before the subtitle note and post-rip validation — so
    downstream sees per-episode files when the split ran and the combined
    file when it declined. Any other shape returns with no table, and the
    split phase contains its own interrupts: this never raises one.

    Args:
        preamble: The run's Preamble — title_dir, display, log
        season: Season number the disc belongs to
        titles_info: The confirm table's floored (title, secs, bytes) list
        playalls: Title numbers disqualified as play-alls
        assignment: Final title -> (lo, hi) episode map (post fix rows)
        ep_runtimes: TMDB runtime seconds per episode (0 = unknown)
        run: subprocess.run callable for monkeypatching (probe and ffmpeg)
    """
    lone = playall_only(titles_info, playalls, assignment)
    if lone is None:
        return
    _t, lo, hi = lone
    show_dir = preamble.title_dir
    combined = episode_target(show_dir, preamble.display, season, lo, hi)
    targets = {e: episode_target(show_dir, preamble.display, season, e, e)
               for e in range(lo, hi + 1)}
    split_combined(combined, targets, season, lo, hi, ep_runtimes,
                   preamble.log, run=run)


def probe_episodes(show_dir: Path, display: str, season: int,
                   assignment: dict[int, tuple[int, int]], ep_runtimes: dict[int, int],
                   fast_scan: bool, run=subprocess.run) -> list[dict]:
    """Probe this season's staged episode files for the validation record.

    Globs the show folder for `<display>_S{season:02d}E*.mkv` and parses each
    name with the staged_eps shape (extras and junk-labeled files never
    match); a double (S02E05-E06) records one row under its lo episode,
    graded against the range's summed runtime (the checker's _double_runtime
    rule — one runtime per lo would read a false 2.00 on a healthy double).
    Durations are actuals probed from the files — never the scan table's
    promise. `t` inverts this run's episode map: the first title whose
    (lo, hi) covers the episode, full scans only — the fast scan's table
    numbers are scanner indices, not real _tNN (slice-04 review P2), and the
    real join died with the junk labels at rename, so fast-scan rows record
    t=None; so does an episode this run's map doesn't cover (an earlier
    disc's file re-probed by the all-season sweep). Null is honest. A file
    whose duration ffprobe won't read is recorded with dur_s null and one
    stderr warning: probing never crashes, never skips the write.

    Args:
        show_dir: The show's title folder — episodes rest here after promotion
        display: Show name (Show (Year))
        season: Season number the disc belongs to
        assignment: This run's title -> (lo, hi) episode map
        ep_runtimes: TMDB runtime seconds per episode (0 = unknown)
        fast_scan: The fast path ran — table numbers are indices, t stays None
        run: subprocess.run callable for monkeypatching

    Returns:
        episodes rows [{"ep", "file", "t", "dur_s", "runtime_s", "ratio",
        "band"}] in ep order
    """
    ffprobe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                   "-of", "csv=p=0"]
    pattern = re.compile(rf"^{re.escape(display)}_S{season:02d}E(\d+)(-E(\d+))?$")
    rows = []
    for mkv in show_dir.glob("*.mkv"):
        m = pattern.match(mkv.stem)
        if not m:
            continue
        ep = int(m.group(1))
        hi = max(ep, int(m.group(3))) if m.group(3) else ep
        result = run(ffprobe_cmd + [str(mkv)], capture_output=True, text=True)
        try:
            dur = int(float(result.stdout.strip()))
        except ValueError:
            ui.warn(f"cannot read duration of {mkv.name} — recorded without one")
            dur = None
        t = None
        if not fast_scan:
            # Invert this run's map: the first title whose (lo, hi) covers the ep
            t = next((num for num in sorted(assignment)
                      if assignment[num][0] <= ep <= assignment[num][1]), None)
        rows.append(tv_row(ep, mkv.name, t, dur,
                           _double_runtime(ep_runtimes, ep, hi)))
    rows.sort(key=lambda r: r["ep"])
    return rows


def post_rip_validation(preamble, season: int, assignment: dict[int, tuple[int, int]],
                        ep_runtimes: dict[int, int], run=subprocess.run) -> None:
    """The post-rip record (map.md decision 6), run beside the subtitle note:
    probe the season's staged episodes, grade them against their TMDB
    runtimes, report the one summary line, and write validation.json at the
    show-folder root.

    A re-rip into the folder merges into the existing same-show document:
    per season this run's rows replace the episodes they record and its disc
    entry (volume label, run, the episodes this run's map staged) appends to
    the season's index — one document holds every season and every disc. An
    existing file naming a different show is never overwritten: stderr note,
    logged, old file kept. Interrupts exit through the rip handler and never
    reach this; a partial rip (makemkvcon rc != 0) is not an interrupt — it
    renames what completed and is recorded from what is there. No new
    prompts or tables: the single summary note is the run's whole voice.

    Args:
        preamble: The run's Preamble — show folder, log, pick, display,
            volume label, fast-scan flag
        season: Season number the disc belongs to
        assignment: This run's title -> (lo, hi) episode map
        ep_runtimes: TMDB runtime seconds per episode (0 = unknown)
        run: subprocess.run callable for monkeypatching
    """
    show_dir = preamble.title_dir
    log = preamble.log
    rows = probe_episodes(show_dir, preamble.display, season, assignment,
                          ep_runtimes, preamble.fast_scan, run)
    yellow = sum(1 for r in rows if r["band"] == "yellow")
    log.note("validation", f"{len(rows)} episodes, {yellow} yellow")

    staged = sorted({ep for lo, hi in assignment.values() if lo >= 1
                     for ep in range(lo, hi + 1)})
    doc = tv_document(preamble.pick.id, preamble.display, season,
                      preamble.label, log.run, staged, rows)
    path = show_dir / VALIDATION_FILENAME
    old = load_validation(path)
    if old is not None:
        old_tmdb = old.get("tmdb")
        old_id = old_tmdb.get("id") if isinstance(old_tmdb, dict) else None
        if old_id is None or str(old_id) != str(preamble.pick.id):
            ui.warn(f"{path} records a different show — validation left untouched")
            return rows
        doc = merge_tv_document(old, doc)
    write_validation(path, doc)
    return rows


def rip_tv(args) -> None:
    """Main TV rip orchestration.

    Args:
        args: argparse.Namespace from cmd_rip_tv

    Raises:
        SystemExit: On any error, user abort, or interrupt (exit 130)
    """
    preamble = None
    view = ui.view() or ui.RunView()  # a test may pre-set a tty/live view
    ui.set_view(view)
    try:
        preamble = rip_preamble("tv", "skive rip tv", args)
        log = preamble.log
        pick = preamble.pick
        display = preamble.display
        titles_full = preamble.titles_full
        show_dir = preamble.title_dir
        stage_tv = preamble.stage
        minlength = preamble.minlength

        # --extras: no season, no scan, no table
        if args.extras:
            log.note("type", "type=tv extras")
            titles = resolve_titles(args.titles, titles_full, preamble.media)
            preexisting = set(show_dir.glob("*.mkv"))
            log.phase("rip")
            infos = titles_full if titles is None else [t for t in titles_full if t.number in titles]
            worst_rc = rip_titles(titles, CONFIG_DIR / "custom.mmcp.xml", minlength, stage_tv, disc=preamble.disc,
                                  total=len(titles_full), infos=infos, header=rip_header(display, infos), log=log)
            if worst_rc != 0:
                log.note("rip", f"makemkvcon rc={worst_rc} — partial rip, renaming what completed")

            # Promote first so the renames see everything the show folder
            # already holds; a collision leaves the file in d{disc}/ with a note.
            promote_staged(stage_tv)
            rename_staged(display, show_dir, suffix="e", preexisting=preexisting)

            # Subtitle note
            note = subtitle_note(show_dir)
            ui.info(note)

            log.phase_end()
            log.finish(worst_rc)
            sys.exit(worst_rc)

        # Season prompt — suspended: the prompt prints normally (scrollback),
        # the view redraws after the answer.
        with ui.suspend():
            console.print("season [1] (0 = specials): ", end="")
            season_str = Prompt.ask("") or "1"
            season_str = season_str.strip()

            if not season_str:
                season = 1
            else:
                try:
                    season = int(season_str)
                except ValueError:
                    ui.fail("season must be a number 0-99")
                    sys.exit(1)

            if not (0 <= season <= 99):
                ui.fail("season must be a number 0-99")
                sys.exit(1)

        log.note("type", f"type=tv season={season}")

        # Rip preflight (CONTEXT.md): the exact season on disk escalates to
        # the default-No confirm; other seasons only inform
        tv_season_gate(Path(os.environ.get("VIDEOS_MOUNT", "/mnt/videos")),
                       log, display, season)

        # The gate already scanned the disc; reuse that result (no second scan)
        if not titles_full:
            ui.fail("disc scan failed — no titles found")
            sys.exit(1)

        # Bare -T opens the title menu; no -T rips all titles (DVD shows prefer this)
        user_titles = resolve_titles(args.titles, titles_full, preamble.media)
        if user_titles is not None:
            titles_full = [t for t in titles_full if t.number in user_titles]
            if not titles_full:
                ui.fail(f"none of the requested titles survived the scan (minlength={minlength})")
                sys.exit(1)

        titles_info = [(t.number, t.seconds, t.size) for t in titles_full]

        # TV floor: menu loops and fragments never make episodes. Dropped
        # entirely — not eligible, not in the table, not ripped.
        titles_info = [t for t in titles_info if t[1] >= tv_floor(minlength)]
        playalls = sum_disqualified(titles_info)

        # ADR 0002/0008: a play-all on the fast scan escalates to the full
        # MakeMKV scan — skipping it needs MakeMKV's real title numbers. The
        # fast durations are discarded and the run joins the full-scan path
        # (direct rename, no duration join), like the duplicate-duration
        # trigger in the preamble.
        if preamble.fast_scan and playalls:
            ui.info("fast scan found a play-all title — escalating to the full MakeMKV scan")
            log.note("scan", "play-all detected — full-scan escalation")
            _, _, titles_full = scan_disc(minlength, disc=preamble.disc, log=preamble.log)
            titles_info = [(t.number, t.seconds, t.size) for t in titles_full]
            titles_info = [t for t in titles_info if t[1] >= tv_floor(minlength)]
            playalls = sum_disqualified(titles_info)
            preamble.fast_scan = False
            preamble.scan_durations = None

        def pin_playalls(a: dict[int, tuple[int, int]]) -> dict[int, tuple[int, int]]:
            """Play-alls pin extra whatever the stated range said — the rip
            skips them unless a fix row assigns one an episode.

            Re-applied after each automatic rebuild (initial assignment, `n`
            re-ask). Never applied after a fix row: `t=ep` typed at the table is
            the deliberate escape hatch.
            """
            for t in playalls:
                a[t] = (0, 0)
            return a

        # Fetch season from TMDB
        if not pick.id or pick.id == "-":
            ui.fail("the TMDB pick carries no id — cannot fetch episodes. re-pick or use --tmdbid tv:N.")
            sys.exit(1)

        try:
            episodes = fetch_season(pick.id, season)
        except RuntimeError as e:
            ui.fail(f"TMDB season fetch failed: {e}")
            sys.exit(1)

        # Build episodes dict
        ep_names = {ep: name for ep, name, _ in episodes}
        ep_runtimes = {ep: rt for ep, _, rt in episodes}
        max_ep = max(ep_names.keys()) if ep_names else 0

        # Get staged episodes
        staged = staged_eps(show_dir, display, season)
        if staged:
            eps_str = ", ".join(f"E{e:02d}" for e in sorted(staged))
            console.print(f"already staged: {eps_str} — c at the prompt clears the show's staging")

        def ask_range() -> tuple[int, int]:
            """One pre-filled prompt states which episodes this disc carries.

            The title overview prints first — again on every `n` re-ask —
            showing the positional fill of the pre-filled range, so what Enter
            accepts is exactly what the overview showed. The pre-fill is the
            episode guess when the runtimes suggest one, else one episode per
            eligible title. Unparseable answers re-ask.
            """
            eligible = len(titles_info) - len(playalls)
            if eligible == 0:
                ui.fail("no eligible titles on this disc")
                sys.exit(1)
            first = next_unstaged(ep_names, staged)
            guess = guess_range(titles_info, playalls, ep_runtimes, staged, ep_names)
            lo, hi = guess if guess is not None else (
                (first, first) if eligible == 1 else (first, first + eligible - 1))
            with ui.suspend():  # the overview + prompt print normally
                print_table(display, season, titles_info,
                            assign_range(titles_info, playalls, lo, hi),
                            ep_names, ep_runtimes, estimate=True, playalls=playalls)
                prefill = str(lo) if lo == hi else f"{lo}-{hi}"
                while True:
                    console.print(f"episodes on this disc [{prefill}]: ", end="")
                    rng = parse_range((Prompt.ask("") or prefill).strip())
                    if rng is not None:
                        return rng

        def build_note(lo: int, hi: int) -> str | None:
            """Mismatch and past-the-season-end warnings, joined to one print."""
            lines = [ln for ln in (range_mismatch_note(titles_info, playalls, lo, hi),
                                   range_past_end_note(hi, max_ep)) if ln]
            return "\n".join(lines) if lines else None

        lo, hi = ask_range()
        assignment = pin_playalls(assign_range(titles_info, playalls, lo, hi))
        note = build_note(lo, hi)

        # Confirm loop — suspended for the whole interactive phase: the table,
        # notes, fix prompts and the clear-staged listing print normally
        # (scrollback); the view redraws after the loop's answer.
        with ui.suspend():
            while True:
                if note:
                    console.print(note)
                print_table(display, season, titles_info, assignment, ep_names, ep_runtimes,
                            playalls=playalls)
                # Play-all-only caption (ADR 0009), recomputed from the final map
                # every iteration: fix rows and `n` re-asks arm or disarm it
                # before the drive spins.
                lone = playall_only(titles_info, playalls, assignment)
                if lone:
                    t, clo, chi = lone
                    console.print(f"title {t} carries S{season:02d}E{clo:02d}-E{chi:02d}"
                                  f" — play-all-only disc, split after the rip")
                console.print("ok? (Enter=rip, fixes like 3=7 / 1=1-2 / 2=e, n=episode range, c=clear staged, q=quit): ", end="")
                ans = Prompt.ask("") or ""

                if not ans:
                    break
                elif ans.lower() == "q":
                    print("skive: quit — nothing ripped.", file=sys.stderr)
                    sys.exit(1)
                elif ans.lower().startswith("n"):
                    # Re-state the range (`n=5` spells the same as bare `n`; any
                    # =-suffix is ignored): disc order stands, the stated range rules.
                    lo, hi = ask_range()
                    assignment = pin_playalls(assign_range(titles_info, playalls, lo, hi))
                    note = build_note(lo, hi)
                elif ans.lower() == "c":
                    doomed = show_staging(show_dir, display)
                    if not doomed:
                        console.print("nothing staged for this show")
                        continue
                    for f in doomed:
                        console.print(f"  {f.name}")
                    sure = Prompt.ask(f"delete {len(doomed)} staged file(s) for {display}? (y/N): ") or ""
                    if sure.lower() != "y":
                        continue
                    for f in doomed:
                        f.unlink()
                    # No re-assignment: the map comes from the stated range, not from
                    # staging. The refreshed set only steers the next pre-fill.
                    staged = staged_eps(show_dir, display, season)
                else:
                    # Apply fixes - rebuild title->ep mapping for clash check
                    title_to_eps = {}
                    for t, (elo, ehi) in assignment.items():
                        if elo != 0:
                            for e in range(elo, ehi + 1):
                                title_to_eps[e] = t
                    apply_fixes(assignment, [t for t, _, _ in titles_info], staged, ans, max_ep, title_to_eps)
                    # A fix row is the user speaking; the automated note does not repeat.
                    note = None

        # Selection string + profile injection
        keep = preamble.keep
        sel = build_selection(keep)
        log.note("audio", f"kept={keep if keep else 'all'} origlang={pick.original_language if pick.original_language != '-' else '?'}")

        profile_path = inject_profile(sel)
        rc = 1  # every exit path must reach log.finish: raw log is unlinked only on rc==0
        try:
            # Rip phase
            log.phase("rip")
            if preamble.fast_scan:
                # Fast scan: MakeMKV's title set is shaped by the same floor the
                # scanner list used, so rip all in one call and join the staged
                # files back to scanner titles by duration at rename time.
                header = fast_scan_header(display, len(titles_info))
                worst_rc = rip_titles(None, profile_path, tv_floor(minlength), stage_tv, disc=preamble.disc,
                                      total=len(titles_info), infos=titles_full, header=header, log=log)
            else:
                # Explicit -T selections may include titles filtered from the confirmed scan.
                rip_selection = None if user_titles is None else [t for t, _, _ in titles_info]
                # Play-alls whose final assignment is extra are never ripped
                # (ADR 0008) — derived after the confirm loop, so a fix row
                # rescuing one rips it. -T runs stay exactly as they were.
                if user_titles is None:
                    skip = playall_skip(playalls, assignment)
                    if skip:
                        # The full scan list, not the floored titles_info: sub-TV-floor
                        # fragments (120-600s extras) rip too — "all" means all of them.
                        rip_selection = [t.number for t in titles_full if t.number not in skip]
                worst_rc = rip_titles(rip_selection, profile_path, minlength, stage_tv, disc=preamble.disc,
                                      total=len(titles_info), infos=titles_full, header=rip_header(display, titles_full), log=log)

            if worst_rc != 0:
                log.note("rip", f"makemkvcon rc={worst_rc} — partial rip, renaming what completed")

            # Promote first: the show folder is the rename target, so the joins
            # and renames see — and roll past — everything already staged for
            # the show; a collision leaves the file in d{disc}/ with a note.
            promote_staged(stage_tv)

            # Rename per the map
            _phase_status("rename", f"{display} S{season:02d}")
            if preamble.fast_scan:
                # The caller owns the exit so the abort still lands in the run log.
                if not rename_tv_fast(display, show_dir, season, assignment, preamble.scan_durations):
                    log.note("rename", "join aborted — an episode's staged file is missing")
                    log.phase_end()
                    rc = 1
                    sys.exit(1)
            else:
                rename_tv(display, show_dir, season, assignment)

            # Chapter split (ADR 0009): a play-all-only disc's combined file
            # divides here — after the rename, before the subtitle note and
            # post-rip validation, on both scan paths. The split contains its
            # own interrupts, so a Ctrl+C at its table never reaches the
            # generic interrupt exit below.
            _phase_status("split", f"{display} S{season:02d}")
            chapter_split_hook(preamble, season, titles_info, playalls,
                               assignment, ep_runtimes)

            # Subtitle note
            note = subtitle_note(show_dir)
            ui.info(note)

            # Post-rip validation (map.md decision 6): the record beside the
            # subtitle note — this season's staged episodes probed and graded,
            # merged into the folder's document, validation.json at the
            # show-folder root. Interrupts exit above and never reach this.
            probed = post_rip_validation(preamble, season, assignment, ep_runtimes)

            log.phase_end()
            rc = worst_rc
        except KeyboardInterrupt:
            # CONTEXT.md Interrupt: delete the in-flight partial (junk-labeled
            # staged files), keep completed renames, log status=interrupted,
            # exit 130. interrupted_exit never returns; the finally below still
            # unlinks the temp profile, and finish() stays silent because
            # interrupted() already wrote this run's total line. The view
            # closes first so its Live never garbles the summary prints.
            view.close()
            interrupted_exit(log, [show_dir, stage_tv])
        finally:
            profile_path.unlink(missing_ok=True)
            log.finish(rc)
        # Close the view before the summary prints (its Live redraw would
        # garble them); the outer close is idempotent. No summary on
        # interrupt — rip_movie precedent.
        view.close()
        # The run summary: per staged episode rows × duration × size × result.
        summary_rows = []
        for r in probed:
            f = show_dir / r["file"]
            try:
                size = hsize(f.stat().st_size)
            except OSError:
                size = "?"
            summary_rows.append((f"S{season:02d}E{r['ep']:02d}",
                                 _hms(r["dur_s"]) if r["dur_s"] is not None else "?",
                                 size, "ok" if worst_rc == 0 else "FAILED"))
        n_ok = sum(1 for r in summary_rows if r[3] == "ok")
        ui.run_summary(summary_rows, n_ok, len(summary_rows) - n_ok,
                       columns=("Episode", "duration", "size", "result"),
                       replay=view.replay_lines(), failed=worst_rc != 0)
        sys.exit(worst_rc)
    except KeyboardInterrupt:
        # Interrupt outside the rip try (TMDB prompt via rip_preamble, season
        # prompt, episode range, confirm table): once the preamble exists the
        # partial purge and the full interrupt exit still apply; main's
        # fallback prints the one line for the not-yet-started window.
        if preamble is not None:
            view.close()
            interrupted_exit(preamble.log, [preamble.title_dir, preamble.stage])
        raise
    finally:
        view.close()
        ui.clear_view()
        ui.clear_log()
