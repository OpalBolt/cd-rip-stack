"""TV rip flow: season prompt, the selection table that decides the episode
map, renames, staging inspection, post-rip validation record."""

import os
import re
import subprocess
import sys
from pathlib import Path

from skive import ui
from skive.chapter_split import split_combined
from skive.makemkv import (
    build_selection, inject_profile, promote_staged, purge_junk,
    rip_header, subtitle_note, rip_titles, hsize,
)
from skive.rip_common import rip_preamble, tv_season_gate
from skive.selection import (
    INTERRUPT, QUIT, episode_map, print_accepted_episodes, run_episode_table,
)
from skive.tmdb import fetch_season
from skive.validation import (
    VALIDATION_FILENAME, _double_runtime, load_validation, merge_tv_document,
    tv_document, tv_row, write_validation,
)
from skive.tv_match import (
    tv_floor, playall_only, sum_disqualified_windowed,
)

console = ui.console  # the shared console; a module name tests can swap

SUMMARY_COLUMNS = ("Episode", "duration", "size", "result")


def _hms(secs: int) -> str:
    """Duration as H:MM:SS."""
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


def episode_target(show_dir: Path, display: str, season: int, lo: int, hi: int) -> Path:
    """Target path for an episode assignment: single or double episode name.

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


def rename_tv(display: str, show_dir: Path, season: int,
              assignment: dict[int, tuple[int, int]]) -> dict[Path, int]:
    """Rename staged files per the episode map.

    Junk-labeled titles become <display>_SNNENN[-ENNN].mkv or <display> - Extra NN.mkv.
    Never clobbers existing targets.

    Args:
        display: Show name
        show_dir: The show's title folder under rip/tv — the promoted files
            renamed here rest directly in it
        season: Season number
        assignment: title -> (lo, hi) mapping

    Returns:
        Each renamed file's target path -> its title number
    """
    renamed: dict[Path, int] = {}
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
        renamed[target] = title_num
        cur = base[:m.start()]
        ui.info(f"{cur} → {target.stem}")
    return renamed


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
        titles_info: The floored (title, secs, bytes) list
        playalls: Title numbers disqualified as play-alls
        assignment: Final title -> (lo, hi) episode map (the accepted table)
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
                   run=subprocess.run) -> list[dict]:
    """Probe this season's staged episode files for the validation record.

    Globs the show folder for `<display>_S{season:02d}E*.mkv` and parses each
    name with the staged_eps shape (extras and junk-labeled files never
    match); a double (S02E05-E06) records one row under its lo episode,
    graded against the range's summed runtime (the checker's _double_runtime
    rule — one runtime per lo would read a false 2.00 on a healthy double).
    Durations are actuals probed from the files — never the scan table's
    promise. `t` inverts this run's episode map: the first title whose
    (lo, hi) covers the episode; None for an episode this run's map doesn't cover (an earlier
    disc's file re-probed by the all-season sweep). Null is honest. A file
    whose duration ffprobe won't read is recorded with dur_s null and one
    stderr warning: probing never crashes, never skips the write.

    Args:
        show_dir: The show's title folder — episodes rest here after promotion
        display: Show name (Show (Year))
        season: Season number the disc belongs to
        assignment: This run's title -> (lo, hi) episode map
        ep_runtimes: TMDB runtime seconds per episode (0 = unknown)
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
        # `t` inverts this run's map: the first title whose (lo, hi) covers the ep
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
            volume label
        season: Season number the disc belongs to
        assignment: This run's title -> (lo, hi) episode map
        ep_runtimes: TMDB runtime seconds per episode (0 = unknown)
        run: subprocess.run callable for monkeypatching
    """
    show_dir = preamble.title_dir
    log = preamble.log
    rows = probe_episodes(show_dir, preamble.display, season, assignment,
                          ep_runtimes, run)
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
            log.note("validation", "existing validation.json names a different show — kept")
            return rows
        doc = merge_tv_document(old, doc)
    write_validation(path, doc)
    return rows


def _episode_code(season: int, lohi: tuple[int, int]) -> str:
    """One title's assignment as `S01E04`, `S01E04-E05`, or `extra`."""
    lo, hi = lohi
    if lo == 0:
        return "extra"
    return f"S{season:02d}E{lo:02d}" + (f"-E{hi:02d}" if hi != lo else "")


def _size(path: Path) -> str:
    try:
        return hsize(path.stat().st_size)
    except OSError:
        return "?"


def _summary_rows(show_dir: Path, display: str, season: int, probed: list[dict],
                  preexisting: set[Path], worst_rc: int,
                  outcomes: dict[int, int] | None = None,
                  attempted: list[int] | None = None,
                  assignment: dict[int, tuple[int, int]] | None = None,
                  renamed: dict[Path, int] | None = None) -> list[tuple]:
    """This run's run-summary rows: each episode file it staged, labeled
    with its full range (failed when its own title's rc is known and
    non-zero, else by the run's worst rc), then the extras it staged (by
    their own title's rc when the rename recorded it), then each unmapped
    title's (sub-TV-floor: no assignment, never renamed) junk-labeled file
    by its own rc, then one failed row per attempted title whose output is
    missing. Files that predate the run are not its items.
    """
    outcomes = outcomes or {}
    assignment = assignment or {}
    renamed = renamed or {}
    rows = []
    new_eps: set[int] = set()
    for r in probed:
        f = show_dir / r["file"]
        if f in preexisting:
            continue
        new_eps.add(r["ep"])
        rc = outcomes.get(r["t"], worst_rc)
        rows.append((f.stem.removeprefix(f"{display}_"),
                     _hms(r["dur_s"]) if r["dur_s"] is not None else "?",
                     _size(f), "ok" if rc == 0 else "failed"))
    extras = [f for f in sorted(show_dir.glob("*.mkv"))
              if f.name.startswith(f"{display} - Extra ") and f not in preexisting]
    for f in extras:
        rc = outcomes.get(renamed[f], worst_rc) if f in renamed else worst_rc
        rows.append((f.stem, "?", _size(f), "ok" if rc == 0 else "failed"))
    unrenamed: dict[int, Path] = {}
    for f in sorted(show_dir.glob("*.mkv")):
        m = re.search(r"_t(\d+)$", f.stem)
        if m and f not in preexisting:
            unrenamed[int(m.group(1))] = f
    missing_extras = 0
    for t in attempted or []:
        if t not in assignment and t in unrenamed:
            f = unrenamed[t]
            rc = outcomes.get(t, worst_rc)
            rows.append((f.stem, "?", _size(f), "ok" if rc == 0 else "failed"))
            continue
        lohi = assignment.get(t, (0, 0))
        if lohi[0] == 0:
            missing_extras += 1
        elif lohi[0] not in new_eps:
            rows.append((_episode_code(season, lohi), "?", "?", "failed"))
    rows += [("extra", "?", "?", "failed")] * max(0, missing_extras - len(extras))
    return rows


def _interrupted(log, stage_dirs: list[Path], view, preexisting: set[Path],
                 inflight: str | None = None) -> None:
    """The interrupt (Ctrl+C) exit with its run summary (CONTEXT.md:
    Interrupt): delete the in-flight partial (junk-labeled files), keep
    completed renames, record status=interrupted, list this run's kept files
    as ok and the title being ripped as interrupted, exit 130. Never returns.
    """
    ui.item_end("fail", "interrupted")  # the open title's frame closes before the summary
    purge_junk(stage_dirs, quiet=True)
    kept = sum(1 for d in stage_dirs for f in d.glob("*.mkv"))
    rows = [(f.stem, "?", _size(f), "ok") for d in stage_dirs
            for f in sorted(d.glob("*.mkv")) if f not in preexisting]
    n_ok = len(rows)
    if inflight:
        rows.append((inflight, "?", "?", "interrupted"))
    replay = view.replay_lines()
    view.close()
    log.interrupted()
    ui.fail(f"interrupted — partial deleted, kept {kept} titles")
    ui.run_summary(rows, n_ok, 0, columns=SUMMARY_COLUMNS, replay=replay,
                   interrupt_note="partial deleted", log=log)
    sys.exit(130)


def rip_tv(args) -> None:
    """Main TV rip orchestration.

    Args:
        args: argparse.Namespace from cmd_rip_tv

    Raises:
        SystemExit: On any error, user abort, or interrupt (exit 130)
    """
    preamble = None
    preexisting: set[Path] = set()  # files that predate the run: not its items
    inflight: str | None = None  # the title being ripped, for the interrupt summary
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
        preexisting = set(show_dir.glob("*.mkv")) | set(stage_tv.glob("*.mkv"))

        def start_rip(order: list[int], names: list[str], by_title: bool = False):
            """Seed the footer with the first title and return the
            title-start callback: each title's item heading prints when that
            title starts ripping. A selected-title rip (by_title) calls back
            with the title number, so a title that fails before saving never
            shifts the next one's label; an all-titles rip calls back per
            saved file and takes names[i] for the i-th title ripped."""
            started = 0
            if view.tty:
                view.footer(f"title 1/{len(order)}", names[0] if names else display, "rip")

            def on_start(number) -> None:
                nonlocal started, inflight
                if by_title and number in order:
                    started = order.index(number) + 1
                else:
                    started += 1
                inflight = names[started - 1] if started <= len(names) else display
                ui.item(started, len(order), inflight)
            return on_start

        # Season prompt (ui.ask suspends the view while it asks)
        try:
            season = int(ui.ask("Season", choices="0 = specials", default="1").strip())
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

        titles_info = [(t.number, t.seconds, t.size) for t in titles_full]

        # TV floor: menu loops and fragments never make episodes. Floored
        # titles feed the play-all sums and the chapter-split trigger; the
        # sub-floor ones still list in the table (bottom section, prefilled
        # skip, rescuable).
        titles_info = [t for t in titles_info if t[1] >= tv_floor(minlength)]
        # Play-alls by window tier: tight ±120s and the soft ±300s window both
        # disqualify and prefill skip (ADR 0008).
        disqualified = sum_disqualified_windowed(titles_info)
        playalls = set(disqualified)
        if len(titles_info) == len(playalls):
            ui.fail("no eligible titles on this disc")
            sys.exit(1)

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

        # Get staged episodes
        staged = staged_eps(show_dir, display, season)

        def list_staging() -> list[Path]:
            return show_staging(show_dir, display)

        def clear_staging(doomed: list[Path]) -> set[int]:
            """`c` after its explicit confirm: delete the listed files. They
            no longer predate the run — a replacement staged at the same path
            is this run's item. Returns the refreshed staged-episode set."""
            for f in doomed:
                f.unlink()
            preexisting.difference_update(doomed)
            return staged_eps(show_dir, display, season)

        # The selection table (ADR 0020) decides the whole episode map: the
        # episode guess prefills every eligible title, the operator restates
        # actions at the cursor, Enter accepts. The view suspends for it, the
        # accepted map echoes into scrollback, the view redraws after.
        # Non-TTY runs refuse before the table — nothing spins.
        emap = episode_map(season, titles_full, minlength, disqualified,
                           ep_names, ep_runtimes, staged)
        with view.suspend():
            if not view.tty and not ui.terminal_present():
                ui.fail("selection table needs a terminal")
                sys.exit(1)
            result = run_episode_table(display, emap, list_staging, clear_staging)
            if isinstance(result, dict):
                print_accepted_episodes(display, emap, result)
                # Play-all-only caption (ADR 0009): the accepted map arms the
                # chapter split before the drive spins.
                lone = playall_only(titles_info, playalls,
                                    {t: r for t, r in result.items() if r is not None})
                if lone:
                    t, clo, chi = lone
                    console.print(f"title {t} carries S{season:02d}E{clo:02d}-E{chi:02d}"
                                  f" — play-all-only disc, split after the rip")
        if result == INTERRUPT:
            # Textual has restored the terminal; follow the repo Interrupt
            # through the handler below (exit 130).
            raise KeyboardInterrupt
        if result == QUIT or not isinstance(result, dict):
            ui.info("quit — nothing ripped.")
            sys.exit(1)

        # The accepted map: ripped titles → (lo, hi), extras (0, 0); a
        # skipped title is simply not in it and is never ripped.
        assignment = {t: r for t, r in result.items() if r is not None}
        skipped = {t for t, r in result.items() if r is None}

        ui.opener("skive rip tv", f"{display} · season {season}", f"disc {preamble.disc} · {preamble.device}")

        # Selection string + profile injection
        keep = preamble.keep
        sel = build_selection(keep)
        log.note("audio", f"kept={keep if keep else 'all'} origlang={pick.original_language if pick.original_language != '-' else '?'}")

        profile_path = inject_profile(sel)
        rc = 1  # every exit path must reach log.finish: raw log is unlinked only on rc==0
        probed: list[dict] = []
        outcomes: dict[int, int] = {}  # title -> rc, selected-title rips only
        attempted: list[int] = []  # titles this run ripped, in rip order
        renamed: dict[Path, int] | None = None  # full-scan renames: target -> title
        try:
            # Rip phase
            log.phase("rip")
            # Skipped titles — prefilled play-alls and sub-floor fragments the
            # operator left alone, or any title restated `s` — are never
            # ripped (ADR 0008); a rescued row is not skipped, so it rips.
            # Nothing skipped rips "all" in one call.
            rip_selection = (None if not skipped else
                             [t.number for t in titles_full if t.number not in skipped])
            order = (rip_selection if rip_selection is not None
                     else sorted(t.number for t in titles_full))
            names = [f"{display} {_episode_code(season, assignment.get(t, (0, 0)))}" for t in order]
            attempted = order
            worst_rc = rip_titles(rip_selection, profile_path, minlength, stage_tv, disc=preamble.disc,
                                  total=len(titles_info), infos=titles_full, header=rip_header(display, titles_full), log=log,
                                  device=preamble.device,
                                  on_title_start=start_rip(order, names, rip_selection is not None),
                                  names=names, outcomes=outcomes)
            inflight = None

            if worst_rc != 0:
                log.note("rip", f"makemkvcon rc={worst_rc} — partial rip, renaming what completed")

            # Promote first: the show folder is the rename target, so the joins
            # and renames see — and roll past — everything already staged for
            # the show; a collision leaves the file in d{disc}/ with a note.
            promote_staged(stage_tv)

            # Rename per the map
            _phase_status("rename", f"{display} S{season:02d}")
            renamed = rename_tv(display, show_dir, season, assignment)

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
            log.note("subtitle", note)

            # Post-rip validation (map.md decision 6): the record beside the
            # subtitle note — this season's staged episodes probed and graded,
            # merged into the folder's document, validation.json at the
            # show-folder root. Interrupts exit above and never reach this.
            probed = post_rip_validation(preamble, season, assignment, ep_runtimes)

            if renamed is not None and not renamed:
                # Full scan staged nothing (makemkvcon rc 0, every save
                # aborted): fail the run so the raw log survives.
                worst_rc = worst_rc or 1
                log.phase_end("FAIL — nothing staged")
            else:
                log.phase_end()
            rc = worst_rc
        except KeyboardInterrupt:
            # CONTEXT.md Interrupt: _interrupted deletes the in-flight partial,
            # keeps completed renames, logs status=interrupted, prints the
            # interrupt summary and exits 130. The finally below still unlinks
            # the temp profile, and finish() stays silent because interrupted()
            # already wrote this run's total line.
            _interrupted(log, [show_dir, stage_tv], view, preexisting, inflight)
        finally:
            profile_path.unlink(missing_ok=True)
            log.finish(rc)
        # Close the view before the summary prints (its Live redraw would
        # garble them); the outer close is idempotent.
        view.close()
        # The run summary: this run's episodes and extras × duration × size × result.
        rows = _summary_rows(show_dir, display, season, probed, preexisting,
                             worst_rc, outcomes, attempted, assignment, renamed)
        n_ok = sum(1 for r in rows if r[3] == "ok")
        ui.run_summary(rows, n_ok, len(rows) - n_ok, columns=SUMMARY_COLUMNS,
                       replay=view.replay_lines(), log=log, failed=rc != 0)
        sys.exit(rc)
    except KeyboardInterrupt:
        # Interrupt outside the rip try (TMDB prompt via rip_preamble, season
        # prompt, the selection table): once the
        # preamble exists the partial purge and the interrupt summary still
        # apply; main's fallback prints the one line for the not-yet-started
        # window.
        if preamble is not None:
            _interrupted(preamble.log, [preamble.title_dir, preamble.stage], view,
                         preexisting, inflight)
        raise
    finally:
        view.close()
        ui.clear_view()
