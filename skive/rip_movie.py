"""Movie rip orchestration: gates, TMDB resolve, rip, rename, subtitle note."""

import os
import sys
from pathlib import Path

from skive.common import RipLog, ensure_mounted, sanitize
from skive.makemkv import (
    BASELINE, build_selection, inject_profile, is_recordable,
    purge_junk, rename_staged, resolve_titles, scan_disc, subtitle_note,
    rip_titles, iso2b,
)
from skive.tmdb import fetch_by_id, parse_tmdbid, search_loop, require_online


def rip_movie(args) -> None:
    """Main movie rip orchestration.

    Args:
        args: argparse.Namespace from cmd_rip_movie

    Raises:
        SystemExit: On any error or user abort
    """
    # Environment
    nfs_videos = os.environ.get("NFS_VIDEOS", "")
    videos_mnt = os.environ.get("VIDEOS_MOUNT", "/mnt/videos")

    # Mount staging dirs
    ensure_mounted(videos_mnt, nfs_videos)
    stage_movie = Path(videos_mnt) / "rip" / "movies"
    stage_tv = Path(videos_mnt) / "rip" / "tv"
    stage_movie.mkdir(parents=True, exist_ok=True)
    stage_tv.mkdir(parents=True, exist_ok=True)

    # RipLog
    log = RipLog("skive rip movie", "makemkvcon", "-")
    log.start()

    minlength = int(os.environ.get("RIP_MINLENGTH", "120"))

    # Burned-media gate (one info scan reused for the menu + media check)
    media, titles_full = scan_disc(minlength)
    if not media:
        print("skive: no disc detected in the drive — insert one and re-run.", file=sys.stderr)
        sys.exit(1)

    if is_recordable(media):
        if args.verified:
            log.note("media", f"recordable media ({media}) — override by --verified")
        else:
            print(f"skive: RECORDABLE media in the drive ({media}) — this looks like a burned copy, not a", file=sys.stderr)
            print("  pressed original (thrift-store discs are often bootlegs). check the disc by eye: burns", file=sys.stderr)
            print("  have purple/green/blue dye and often a stick-on or inkjet label; originals are silver", file=sys.stderr)
            print("  with printed artwork. genuinely-original recordable releases exist (e.g. Warner Archive", file=sys.stderr)
            print("  DVD-R) — if you verify it is one, re-run with --verified.", file=sys.stderr)
            sys.exit(1)
    else:
        log.note("media", f"media={media}")

    # Purge junk from both staging dirs
    purge_junk([stage_movie, stage_tv])

    # Online gate + TMDB_API_KEY check (inside require_online)
    require_online()

    # Resolve the pick
    if args.tmdbid:
        try:
            kind, tmdb_id = parse_tmdbid(args.tmdbid, "movie")
            pick = fetch_by_id(kind, tmdb_id)
        except (ValueError, RuntimeError) as e:
            print(f"skive: TMDB resolve failed: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        pick = search_loop("movie")

    # Display name
    display = pick.title
    if pick.year and pick.year != "-":
        display = f"{pick.title} ({pick.year})"
    display = sanitize(display)
    log.name = display

    log.note("type", "type=movie")

    # Build selection string
    if pick.original_language and pick.original_language != "-":
        keep = iso2b(pick.original_language)
    else:
        keep = ""
    sel = build_selection(keep)
    log.note("audio", f"kept={keep if keep else 'all'} origlang={pick.original_language if pick.original_language != '-' else '?'}")

    # Inject profile
    profile_path = inject_profile(sel)
    try:
        # Bare -T opens the menu over the gate scan; no -T rips all titles
        titles = resolve_titles(args.titles, titles_full)

        # Rip phase
        log.phase("rip")
        worst_rc = rip_titles(titles, profile_path, minlength, stage_movie)

        if worst_rc != 0:
            log.note("rip", f"makemkvcon rc={worst_rc} — partial rip, renaming what completed")

        # Rename
        suffix = "e" if args.extras else "t"
        rename_staged(display, stage_movie, suffix=suffix, part=args.part)

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