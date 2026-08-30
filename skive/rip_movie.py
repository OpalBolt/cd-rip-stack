"""Movie rip orchestration: gates, TMDB resolve, rip, rename, subtitle note."""

import sys

from skive.makemkv import (
    build_selection, inject_profile, promote_staged, rename_staged, resolve_titles,
    rip_header, subtitle_note, rip_titles,
)
from skive.rip_common import rip_preamble


def rip_movie(args) -> None:
    """Main movie rip orchestration.

    Args:
        args: argparse.Namespace from cmd_rip_movie

    Raises:
        SystemExit: On any error or user abort
    """
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
    try:
        # Bare -T opens the menu over the gate scan; no -T rips all titles
        # (Blu-ray: scrambled decoy titles are skipped by default)
        titles = resolve_titles(args.titles, titles_full, preamble.media)

        # Rip phase
        preexisting = set(stage_movie.glob("*.mkv"))
        log.phase("rip")
        infos = titles_full if titles is None else [t for t in titles_full if t.number in titles]
        worst_rc = rip_titles(titles, profile_path, minlength, stage_movie, disc=preamble.disc,
                              total=len(titles_full), infos=infos, header=rip_header(display, infos))

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
        log.finish(worst_rc)
        sys.exit(worst_rc)
    finally:
        # Clean up temp profile
        profile_path.unlink(missing_ok=True)