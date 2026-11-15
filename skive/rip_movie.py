"""Movie rip orchestration: gates, TMDB resolve, pre-rip table with main
pick, rip, rename, subtitle note, post-rip validation record."""

import re
import subprocess
import sys
from pathlib import Path

from skive import ui
from skive.makemkv import (
    build_selection, hsize, inject_profile, promote_staged, purge_junk,
    rename_staged, rip_header, subtitle_note, rip_titles,
)
from skive.rip_common import rip_preamble
from skive.selection import (
    INTERRUPT, QUIT, EXTRA_ACTION, graded_rows as build_graded_rows, hms,
    print_accepted, rip_set_line, run_selection_table,
)
from skive.validation import (
    MAIN_ACTION, merge_movie_document, movie_document, movie_row, resolve_main,
    set_extras_marker, VALIDATION_FILENAME, load_validation, write_validation,
)



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
the scan table (renames roll numbers
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
                        graded_rows: list[dict], run=subprocess.run) -> list[dict]:
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
            volume label
        preexisting: Files present before the rip started — never probed
        chosen_main: The table's pick ("t21"), None when nothing was chosen
        graded_rows: The pre-rip rows the selection table graded
        run: subprocess.run callable for monkeypatching
    """
    title_dir = preamble.title_dir
    log = preamble.log
    pick = preamble.pick

    rows = probe_staged(title_dir, preexisting, pick.runtime_secs, run)
    main = resolve_main(chosen_main, graded_rows, rows)

    for r in rows:
        parts = [r["file"], hms(r["dur_s"]) if r["dur_s"] is not None else "?"]
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
    write_validation(path, doc)
    return rows


def _terminal_present() -> bool:
    """True terminal presence on stderr, ignoring NO_COLOR/TERM: plainness
    is a rendering choice, not the non-TTY case."""
    try:
        if sys.stderr is not None and sys.stderr.isatty():
            return True
    except ValueError:  # closed stream
        pass
    return bool(ui.console.is_terminal)


def _doc_is_this_film(tmdb_id, path: Path) -> bool:
    """No document (marker is a no-op) or a document naming this film —
    the guard for writing the extras marker into validation.json."""
    old = load_validation(path)
    if old is None:
        return True
    old_tmdb = old.get("tmdb")
    old_id = old_tmdb.get("id") if isinstance(old_tmdb, dict) else None
    return old_id is not None and str(old_id) == str(tmdb_id)


def _quit_nothing_ripped() -> None:
    """TV's quit: one line on stderr, exit 1 — nothing has been ripped."""
    ui.info("quit — nothing ripped.")
    sys.exit(1)


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

        # Pre-rip gate (map.md decision 3): the selection table — the audited
        # prefill and the operator's verb edits — is everything the human
        # sees before the drive spins. The accepted actions and the graded
        # rows travel on to the validation record (map.md decision 5). The
        # view suspends for the gate: the app owns the terminal, the accepted
        # table echoes into scrollback, the view redraws after. Non-TTY runs
        # refuse before the table — nothing spins, one stderr line.
        with view.suspend():
            if not view.tty and not _terminal_present():
                ui.fail("selection table needs a terminal")
                sys.exit(1)
            result = run_selection_table(log, display, titles_full,
                                         pick.runtime_secs)
            if isinstance(result, dict):
                print_accepted(display, titles_full, result, pick.runtime_secs)
                if result:  # an empty scan names no rip set line
                    ui.info(rip_set_line(result))

        if result == INTERRUPT:
            # Textual has restored the terminal; follow the repo Interrupt
            # through the rip handler below (exit 130).
            raise KeyboardInterrupt
        if result == QUIT or not isinstance(result, dict):
            _quit_nothing_ripped()

        actions = result
        main_number = next((n for n, a in actions.items() if a == MAIN_ACTION),
                           None)
        extra_numbers = sorted(n for n, a in actions.items()
                               if a == EXTRA_ACTION)
        chosen_main = f"t{main_number}" if main_number is not None else None
        graded_rows = build_graded_rows(titles_full, pick.runtime_secs)

        # The accepted table states the scope (ADR 0020): the main alone, or
        # the main plus accepted extras riding the old -i machinery. An empty
        # scan names no main, so the run rips all exactly like the old
        # default.
        if chosen_main is None:
            titles = None
            infos = titles_full
            log.note("titles", "rip set: all — no main on the disc")
        else:
            titles = [main_number] + extra_numbers
            infos = [t for t in titles_full if t.number in titles]
            log.note("titles", rip_set_line(actions))

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
            worst_rc = rip_titles(titles, profile_path, minlength, stage_movie, disc=preamble.disc,
                                  total=len(titles_full), infos=infos, header=rip_header(display, infos), log=log,
                                  device=preamble.device, on_title_start=title_started)

            if worst_rc != 0:
                log.note("rip", f"makemkvcon rc={worst_rc} — partial rip, renaming what completed")

            # Promote first so the renames see — and roll past — everything the
            # title folder already holds; a collision leaves the file in
            # d{disc}/ with a note (never clobber).
            promote_staged(stage_movie)

            # Rename: extras accepted at the table ride the title suffix
            # (the old -i shape), never the -E extras suffix.
            rename_staged(display, title_dir, suffix="t", part=args.part, preexisting=preexisting)

            # Subtitle note
            note = subtitle_note(title_dir)
            ui.info(note)
            log.note("subtitle", note)

            # Post-rip validation (map.md decision 5): the record beside the
            # subtitle note — actuals probed from what staged, the main
            # resolved to a real final file number, validation.json at the
            # title-folder root. Interrupts exit above and never reach this.
            probed = post_rip_validation(preamble, preexisting, chosen_main, graded_rows)
            if extra_numbers and _doc_is_this_film(preamble.pick.id,
                                                   title_dir / VALIDATION_FILENAME):
                # Accepted extra rows (the table's -i replacement): the
                # extras marker only — never forced over a manual keep-edit,
                # and never into a foreign film's record (post_rip_validation
                # leaves a different-film document untouched).
                set_extras_marker(title_dir / VALIDATION_FILENAME, True,
                                  "superfast")

            if infos and not probed:
                # makemkvcon exits 0 even when every title save aborts (BD-RE
                # read/hash failure): an empty stage is a failed rip, and
                # rc!=0 keeps the raw log that names the MakeMKV error.
                worst_rc = worst_rc or 1
                log.phase_end(f"FAIL — nothing staged from {len(infos)} title(s)")
            else:
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
            summary_rows.append((f.stem, hms(r["dur_s"]) if r["dur_s"] is not None else "?",
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