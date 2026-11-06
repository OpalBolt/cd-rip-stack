"""Movie rip orchestration: gates, TMDB resolve, pre-rip table with main
pick, rip, rename, subtitle note, post-rip validation record."""

import re
import subprocess
import sys
from pathlib import Path

from rich.text import Text

from skive import ui
from skive.makemkv import (
    build_selection, hsize, inject_profile, promote_staged, purge_junk,
    rename_staged, rip_header, subtitle_note, rip_titles,
)
from skive.rip_common import fast_scan_header, rip_preamble
from skive.scan import TitleInfo, resolve_titles
from skive.validation import (
    MAIN_FLOOR_S, arbitrate_main, grade_main, main_candidates,
    merge_movie_document, movie_document, movie_row, resolve_main,
    VALIDATION_FILENAME, load_validation, write_validation,
)



def _hms(secs: int) -> str:
    """Duration as H:MM:SS (the TV table's format)."""
    return f"{secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def _interrupted(log, stage_dirs: list[Path], view,
                 preexisting: set[Path] | None = None) -> None:
    """Close a movie run with its summary after Ctrl+C.

    Files left by this run with final names are completed rows; files that
    predated it are kept on disk but do not count toward this run's result.
    Junk-labelled in-flight files are purged before the rows are collected.
    """
    preexisting = preexisting or set()
    ui.item_end("fail", "interrupted")  # the open title's frame closes before the summary
    purge_junk(stage_dirs, quiet=True)
    kept = sum(1 for directory in stage_dirs for path in directory.glob("*.mkv"))
    rows = []
    for directory in stage_dirs:
        for path in sorted(directory.glob("*.mkv")):
            if path in preexisting:
                continue
            try:
                size = hsize(path.stat().st_size)
            except OSError:
                size = "?"
            rows.append((path.stem, "?", size, "ok"))
    replay = view.replay_lines()
    view.close()
    log.interrupted()
    ui.fail(f"interrupted — partial deleted, kept {kept} titles")
    ui.run_summary(rows, len(rows), 0, columns=("Title", "duration", "size", "result"),
                   replay=replay, interrupt_note="partial deleted", log=log)
    sys.exit(130)


_FINAL_T = re.compile(r'_t(\d+)(?:\.p\d+)?$')
"""A staged file's final title number — parsed from the FINAL name, never
the scan table (fast-scan numbers are scanner indices; renames roll numbers
forward past collisions). An optional trailing .pN part suffix (multi-part
movies) may follow the number: the part file's t is its base title's t."""


def probe_staged(title_dir: Path, preexisting: set[Path], runtime_s: int | None,
                 run=subprocess.run) -> list[dict]:
    """Probe this run's staged *_tNN.mkv files for the validation record.

    Glob *_t*.mkv and parse the final _tNN (a .pN part suffix may trail it:
    multi-part movies get their own row under the base title's number);
    files that rested in the folder
    before the rip started (an earlier rip's files — the re-rip merge keeps
    their rows) and names without a final _tNN (extras, transcode outputs)
    are skipped. Durations are actuals probed from the files — never the scan
    table's promise — and each row is graded against the film runtime. A file
    whose duration ffprobe won't read is recorded with dur_s/ratio/band null
    and one stderr warning: probing never crashes, never skips the write.

    Args:
        title_dir: The title folder — staged files rest here after promotion
        preexisting: Files present before the rip started — never probed
        runtime_s: TMDB runtime in seconds, None when unknown
        run: subprocess.run callable for monkeypatching

    Returns:
        titles rows [{"file", "t", "dur_s", "ratio", "band"}] in t order
    """
    ffprobe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                   "-of", "csv=p=0"]
    rows = []
    for mkv in title_dir.glob("*_t*.mkv"):
        if mkv in preexisting:
            continue
        m = _FINAL_T.search(mkv.stem)
        if not m:
            continue
        result = run(ffprobe_cmd + [str(mkv)], capture_output=True, text=True)
        try:
            dur = int(float(result.stdout.strip()))
        except ValueError:
            ui.warn(f"cannot read duration of {mkv.name} — recorded without one")
            dur = None
        rows.append(movie_row(mkv.name, int(m.group(1)), dur, runtime_s))
    rows.sort(key=lambda r: r["t"])
    return rows


def post_rip_validation(preamble, preexisting: set[Path], chosen_main: str | None,
                        graded_rows: list[dict], run=subprocess.run,
                        include_extras: bool = False) -> list[dict]:
    """The post-rip record (map.md decision 5), run beside the subtitle note:
    probe what actually staged, grade it, resolve the main to a real file
    number, report one line per file, and write validation.json at the
    title-folder root.

    The report and the run-log note carry the green/yellow summary. A re-rip
    into the folder merges into an existing same-id document (rows appended,
    this run's main wins when it resolves, latest run wins); an existing file
    naming a different film is never overwritten — stderr note, logged, old
    file kept. Interrupts exit through the rip handler and never reach this;
    a partial rip (makemkvcon rc != 0) is not an interrupt — it renames what
    completed and is recorded from what is there.

    Args:
        preamble: The run's Preamble — title folder, log, pick, display,
            volume label, fast-scan flag
        preexisting: Files present before the rip started — never probed
        chosen_main: The table's pick ("t21"), None when nothing was chosen
        graded_rows: The pre-rip rows from pre_rip_confirm
        run: subprocess.run callable for monkeypatching
    """
    title_dir = preamble.title_dir
    log = preamble.log
    pick = preamble.pick

    rows = probe_staged(title_dir, preexisting, pick.runtime_secs, run)
    main = resolve_main(chosen_main, graded_rows, rows, preamble.fast_scan)

    for r in rows:
        parts = [r["file"], _hms(r["dur_s"]) if r["dur_s"] is not None else "?"]
        if r["band"]:
            parts.append(r["band"])
        if main == f"t{r['t']}":
            parts.append("MAIN")
        ui.info("  ".join(parts))
    main_row = next((r for r in rows if f"t{r['t']}" == main), None)
    green_mains = 1 if main_row is not None and main_row["band"] == "green" else 0
    yellow = sum(1 for r in rows if r["band"] == "yellow")
    summary = f"{len(rows)} files, {green_mains} green main, {yellow} yellow"
    ui.info(f"validation: {summary}")
    log.note("validation", summary)

    doc = movie_document(pick.id, preamble.display, pick.runtime_secs,
                         preamble.label, log.run, main, rows)
    path = title_dir / VALIDATION_FILENAME
    old = load_validation(path)
    if old is not None:
        old_tmdb = old.get("tmdb")
        old_id = old_tmdb.get("id") if isinstance(old_tmdb, dict) else None
        if old_id is None or str(old_id) != str(pick.id):
            ui.warn(f"{path} records a different film — validation left untouched")
            log.note("validation", "existing validation.json names a different film — kept")
            return rows
        doc = merge_movie_document(old, doc)
    if include_extras:
        # -i/--include-extras (CONTEXT.md: Extras marker): the extras this
        # run staged are keepers — transcode encodes them (superfast)
        doc["extras"] = {"include": True, "quality": "superfast"}
    write_validation(path, doc)
    return rows


def print_table(display: str, titles: list[TitleInfo], runtime_s: int | None,
                extras: bool = False) -> None:
    """Print the pre-rip table in title-number order (the TV confirm table,
    movie cut).

    One row per scanned title: number, duration, size when known (fast-scan
    rows carry size 0 and show a blank cell, like TV). Candidates at/above
    MAIN_FLOOR_S get a ratio cell graded green/yellow in the shared band;
    sub-floor titles show duration only; an unknown runtime blanks the ratio
    cells — never a false yellow. -E runs drop the ratio column entirely: a
    bonus disc has no film runtime to grade against.

    Args:
        display: Sanitized display title shown over the table
        titles: Every scanned TitleInfo
        runtime_s: TMDB runtime in seconds, None when unknown
        extras: -E run — no ratio column
    """
    ui.console.print(f"\n{display}")

    columns = ["title", "duration", "size"]
    if not extras:
        columns.append("ratio")
    table = ui.table(*columns)

    for t in sorted(titles, key=lambda x: x.number):
        cell = ""
        if not extras and t.seconds >= MAIN_FLOOR_S:
            graded = grade_main(t.seconds, runtime_s)
            if graded is not None:
                cell = Text(f"{graded[0]:.2f}", style=graded[1])
        row = [Text(str(t.number), style="bold"), _hms(t.seconds),
               hsize(t.size) if t.size else ""]
        if not extras:
            row.append(cell)
        table.add_row(*row)

    ui.console.print(table)


def _quit_nothing_ripped() -> None:
    """TV's quit: one line on stderr, exit 1 — nothing has been ripped."""
    ui.info("quit — nothing ripped.")
    sys.exit(1)


def _ask_main(near: list[tuple[int, int, float]], default_number: int) -> int:
    """The loud main-pick prompt: bare Enter takes the default (biggest), a
    number picks that title, q quits. Anything else re-asks.

    Args:
        near: (t-number, duration, exact ratio) candidates near the runtime
        default_number: The default pick's title number

    Returns:
        The picked title number
    """
    ui.warn(f"{len(near)} titles near the TMDB runtime — pick the main feature")
    choices = " · ".join(f"t{number} {_hms(secs)} ({ratio:.2f})"
                         for number, secs, ratio in near)
    while True:
        ans = ui.ask("Main feature", choices=f"{choices} · q quit",
                      default=f"t{default_number}").strip().lower()
        if not ans or ans == f"t{default_number}":
            return default_number
        if ans == "q":
            _quit_nothing_ripped()
        digits = ans[1:] if ans.startswith("t") else ans
        if digits.isdigit() and any(n == int(digits) for n, _, _ in near):
            return int(digits)
        ui.warn(f"pick one of: {', '.join(f't{n}' for n, _, _ in near)}")


def pre_rip_confirm(log, display: str, titles: list[TitleInfo],
                    runtime_s: int | None,
                    extras: bool = False) -> tuple[str | None, list[dict]]:
    """The pre-rip gate (map.md decision 3): table, main pick, confirm —
    everything the human sees before the drive spins.

    Arbitration (skive.validation.arbitrate_main) decides the main: one
    candidate near the TMDB runtime wins automatically, several force the
    pick prompt (default biggest), none near — or an unknown runtime —
    falls back to biggest bytes silently. Every outcome lands in the run log
    under phase "main". The confirm ends the gate: plain
    `ok? (Enter=rip, q=quit)`, escalating to a default-No y/N whenever any
    row graded yellow (blank cells never escalate). q — or anything but an
    explicit y on the yellow escalation — quits with nothing ripped, before
    inject_profile. The -T title menu still opens later, over the scan; this
    gate stands over it.

    Args:
        log: The run's RipLog; main outcomes log under phase "main"
        display: Sanitized display title shown over the table
        titles: Every scanned TitleInfo (fast-scan and full scan alike)
        runtime_s: TMDB runtime in seconds, None when unknown
        extras: -E run — no ratio column, no main logic at all

    Returns:
        (main, rows) for the validation record written after the rip:
        main is the chosen main "t21", None when nothing was chosen (shorts,
        bonus discs, -E runs — transcode keeps its biggest-bytes fallback);
        rows are {"t", "dur_s", "ratio", "band"} per scanned title in title
        order, ratio the exact quotient (2dp for the record is the writer's
        call) and ratio/band None where never graded.
    """
    ordered = sorted(titles, key=lambda t: t.number)
    triples = [(t.number, t.seconds, t.size) for t in ordered]

    print_table(display, ordered, runtime_s, extras=extras)

    main_number: int | None = None
    if not extras:
        arb = arbitrate_main(main_candidates(triples), runtime_s)
        if arb.kind == "several":
            main_number = _ask_main(arb.near, arb.pick[0])
            how = "picked by hand" if main_number != arb.pick[0] else "default (biggest bytes)"
            log.note("main", f"main=t{main_number} {how} — {len(arb.near)} titles near the runtime")
        elif arb.pick is not None:
            main_number = arb.pick[0]
            why = {"sole": "near the runtime — automatic",
                   "zero": "biggest bytes — nothing near the runtime",
                   "unknown": "biggest bytes — runtime unknown"}[arb.kind]
            log.note("main", f"main=t{main_number} {why}")
        else:
            log.note("main", "main=none — no candidate main on the disc")

    rows = []
    for number, secs, _size in triples:
        ratio = band = None
        if not extras and secs >= MAIN_FLOOR_S:
            graded = grade_main(secs, runtime_s)
            if graded is not None:
                ratio, band = graded
        rows.append({"t": number, "dur_s": secs, "ratio": ratio, "band": band})

    if any(r["band"] == "yellow" for r in rows):
        while True:
            if ui.confirm("Rip with yellow ratio rows?", default=False):
                break
            _quit_nothing_ripped()
    else:
        while True:
            ans = ui.ask("Rip with this map?", choices="rip · q quit",
                         default="Enter").strip().lower()
            if ans in ("", "enter", "rip"):
                break
            if ans == "q":
                _quit_nothing_ripped()

    return (f"t{main_number}" if main_number is not None else None, rows)


def rip_movie(args) -> None:
    """Main movie rip orchestration.

    Args:
        args: argparse.Namespace from cmd_rip_movie

    Raises:
        SystemExit: On any error, user abort, or interrupt (exit 130)
    """
    preamble = None
    run_preexisting: set[Path] = set()
    view = ui.view() or ui.RunView()  # a test may pre-set a tty/live view
    ui.set_view(view)
    try:
        preamble = rip_preamble("movie", "skive rip movie", args)
        log = preamble.log
        pick = preamble.pick
        display = preamble.display
        titles_full = preamble.titles_full
        title_dir = preamble.title_dir
        stage_movie = preamble.stage
        minlength = preamble.minlength

        log.note("type", "type=movie")
        # Identity note: the film's runtime when known, so the run log records
        # what disc durations were graded against. Unknown stays absent.
        if pick.runtime_secs is not None:
            log.note("runtime", f"runtime={pick.runtime_secs}s")

        # Pre-rip gate (map.md decision 3): table, main pick, confirm — every
        # run confirms before the drive spins. The chosen main and the graded
        # rows travel on to the validation record (map.md decision 5), written
        # beside the subtitle note after the rip. The view suspends for the
        # gate: table and prompt print normally (scrollback), the view redraws
        # after the answer.
        with view.suspend():
            chosen_main, graded_rows = pre_rip_confirm(
                log, display, titles_full, pick.runtime_secs, extras=args.extras)

        # Resolve any title menu after the interactive identity gate. The run
        # opener must wait until this final scope is known.
        if preamble.fast_scan:
            titles = None
            infos = titles_full
        else:
            with view.suspend():
                titles = resolve_titles(args.titles, titles_full, preamble.media)
            infos = titles_full if titles is None else [t for t in titles_full if t.number in titles]

        ui.opener("skive rip movie", display, f"disc {preamble.disc} · {preamble.device}")
        started_titles = 0

        def title_started(number: int) -> None:
            nonlocal started_titles
            started_titles += 1
            ui.item(started_titles, len(infos), f"title {number}")

        if view.tty:
            view.footer(f"title 1/{len(infos)}", display, "rip")
            estimated = sum(title.size for title in infos)
            if estimated:
                view.bg("disc", f"{hsize(0)}/{hsize(estimated)}")

        # Build selection string
        keep = preamble.keep
        sel = build_selection(keep)
        log.note("audio", f"kept={keep if keep else 'all'} origlang={pick.original_language if pick.original_language != '-' else '?'}")

        # Inject profile
        profile_path = inject_profile(sel)
        rc = 1  # every exit path must reach log.finish: raw log is unlinked only on rc==0
        summary_rows: list[tuple] = []
        try:
            # Rip phase
            run_preexisting = set(title_dir.glob("*.mkv"))
            preexisting = run_preexisting
            log.phase("rip")
            if preamble.fast_scan:
                # Fast scan (ADR 0002): durations only, so rip all in one call —
                # the ±1s escalation already filtered decoys, and rename_staged
                # keeps MakeMKV's own _tNN numbers (no duration join needed).
                # minlength unchanged: movies have no episode floor, so
                # RIP_MINLENGTH stays the gate (a 10-minute short must still rip).
                # Synthesized infos carry size 0 and no audio, so mirror the TV
                # fast path's two-line header instead of rip_header's 0.0 B / ?.
                header = fast_scan_header(display, len(titles_full))
                worst_rc = rip_titles(None, profile_path, minlength, stage_movie, disc=preamble.disc,
                                      total=len(titles_full), infos=titles_full, header=header, log=log,
                                      device=preamble.device, on_title_start=title_started)
            else:
                worst_rc = rip_titles(titles, profile_path, minlength, stage_movie, disc=preamble.disc,
                                      total=len(titles_full), infos=infos, header=rip_header(display, infos), log=log,
                                      device=preamble.device, on_title_start=title_started)

            if worst_rc != 0:
                log.note("rip", f"makemkvcon rc={worst_rc} — partial rip, renaming what completed")

            # Promote first so the renames see — and roll past — everything the
            # title folder already holds; a collision leaves the file in
            # d{disc}/ with a note (never clobber).
            promote_staged(stage_movie)

            # Rename
            suffix = "e" if args.extras else "t"
            rename_staged(display, title_dir, suffix=suffix, part=args.part, preexisting=preexisting)

            # Subtitle note
            note = subtitle_note(title_dir)
            ui.info(note)
            log.note("subtitle", note)

            # Post-rip validation (map.md decision 5): the record beside the
            # subtitle note — actuals probed from what staged, the main
            # resolved to a real final file number, validation.json at the
            # title-folder root. Interrupts exit above and never reach this.
            probed = post_rip_validation(preamble, preexisting, chosen_main, graded_rows,
                                         include_extras=args.include_extras)

            log.phase_end()
            rc = worst_rc
        except KeyboardInterrupt:
            # CONTEXT.md Interrupt: delete the in-flight partial (junk-labeled
            # staged files), keep completed renames, log status=interrupted,
            # exit 130. _interrupted never returns; the finally below still
            # unlinks the temp profile, and finish() stays silent because
            # interrupted() already wrote this run's total line. It closes the
            # view before printing the interrupt summary.
            _interrupted(log, [title_dir, stage_movie], view, preexisting=run_preexisting)
        finally:
            # Clean up temp profile
            profile_path.unlink(missing_ok=True)
            log.finish(rc)
        # Close the view before the summary prints (its Live redraw would
        # garble them); replay works after close, and the outer close is
        # idempotent.
        view.close()
        # The run summary: per-title rows × duration × size × result.
        for r in probed:
            f = title_dir / r["file"]
            try:
                size = hsize(f.stat().st_size)
            except OSError:
                size = "?"
            summary_rows.append((f.stem, _hms(r["dur_s"]) if r["dur_s"] is not None else "?",
                                 size, "ok" if worst_rc == 0 else "FAILED"))
        # Extras are renamed by their descriptive name rather than _tNN, so
        # they intentionally do not appear in validation titles. They are
        # still this run's completed items and belong in the rip summary.
        for f in sorted(title_dir.glob("* - Extra *.mkv")):
            if f in preexisting:
                continue
            try:
                size = hsize(f.stat().st_size)
            except OSError:
                size = "?"
            summary_rows.append((f.stem, "?", size, "ok" if worst_rc == 0 else "FAILED"))
        n_ok = sum(1 for r in summary_rows if r[3] == "ok")
        ui.run_summary(summary_rows, n_ok, len(summary_rows) - n_ok,
                       columns=("Title", "duration", "size", "result"),
                       replay=view.replay_lines(), log=log, failed=worst_rc != 0)
        sys.exit(worst_rc)
    except KeyboardInterrupt:
        # Interrupt outside the rip try (TMDB prompt via rip_preamble, scan,
        # the note/profile window): rip_preamble logs the interrupted status
        # for its own window; once the preamble exists the partial purge and
        # the full interrupt exit still apply. main's fallback prints the
        # one line for the not-yet-started window.
        if preamble is not None:
            _interrupted(preamble.log, [preamble.title_dir, preamble.stage], view,
                          preexisting=run_preexisting)
        raise
    finally:
        view.close()
        ui.clear_view()