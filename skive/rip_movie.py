"""Movie rip orchestration: gates, TMDB resolve, rip, rename, subtitle note."""

import sys

from skive.makemkv import (
    build_selection, inject_profile, promote_staged, rename_staged,
    rip_header, subtitle_note, rip_titles,
)
from skive.rip_common import fast_scan_header, interrupted_exit, rip_preamble
from skive.scan import resolve_titles


def rip_movie(args) -> None:
    """Main movie rip orchestration.

    Args:
        args: argparse.Namespace from cmd_rip_movie

    Raises:
        SystemExit: On any error, user abort, or interrupt (exit 130)
    """
    preamble = None
    try:
        preamble = rip_preamble("movie", "skive rip movie", args)
        log = preamble.log
        pick = preamble.pick
        display = preamble.display
        titles_full = preamble.titles_full
        stage_movie = preamble.stage_movie
        minlength = preamble.minlength

        log.note("type", "type=movie")

        # Build selection string
        keep = preamble.keep
        sel = build_selection(keep)
        log.note("audio", f"kept={keep if keep else 'all'} origlang={pick.original_language if pick.original_language != '-' else '?'}")

        # Inject profile
        profile_path = inject_profile(sel)
        rc = 1  # every exit path must reach log.finish: raw log is unlinked only on rc==0
        try:
            # Rip phase
            preexisting = set(stage_movie.glob("*.mkv"))
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
                                      total=len(titles_full), infos=titles_full, header=header, log=log)
            else:
                # Bare -T opens the menu over the gate scan; no -T rips all titles
                # (Blu-ray: scrambled decoy titles are skipped by default)
                titles = resolve_titles(args.titles, titles_full, preamble.media)

                infos = titles_full if titles is None else [t for t in titles_full if t.number in titles]
                worst_rc = rip_titles(titles, profile_path, minlength, stage_movie, disc=preamble.disc,
                                      total=len(titles_full), infos=infos, header=rip_header(display, infos), log=log)

            if worst_rc != 0:
                log.note("rip", f"makemkvcon rc={worst_rc} — partial rip, renaming what completed")

            # Rename
            suffix = "e" if args.extras else "t"
            rename_staged(display, stage_movie, suffix=suffix, part=args.part, preexisting=preexisting)
            if preamble.disc > 0:
                promote_staged(stage_movie)

            # Subtitle note
            note = subtitle_note(stage_movie)
            print(f"{note}", file=sys.stderr)
            log.note("subtitle", note)

            log.phase_end()
            rc = worst_rc
            sys.exit(worst_rc)
        except KeyboardInterrupt:
            # CONTEXT.md Interrupt: delete the in-flight partial (junk-labeled
            # staged files), keep completed renames, log status=interrupted,
            # exit 130. interrupted_exit never returns; the finally below still
            # unlinks the temp profile, and finish() stays silent because
            # interrupted() already wrote this run's total line.
            interrupted_exit(log, [stage_movie])
        finally:
            # Clean up temp profile
            profile_path.unlink(missing_ok=True)
            log.finish(rc)
    except KeyboardInterrupt:
        # Interrupt outside the rip try (TMDB prompt via rip_preamble, scan,
        # the note/profile window): rip_preamble logs the interrupted status
        # for its own window; once the preamble exists the partial purge and
        # the full interrupt exit still apply. main's fallback prints the
        # one line for the not-yet-started window.
        if preamble is not None:
            interrupted_exit(preamble.log, [preamble.stage_movie])
        raise