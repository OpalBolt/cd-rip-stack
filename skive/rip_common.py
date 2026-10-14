"""Shared preamble for movie and TV ripping."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from skive.common import RipLog, ensure_mounted, sanitize
from skive.makemkv import TitleInfo, is_recordable, iso2b, purge_junk, scan_disc
from skive.tmdb import TMDBPick, fetch_by_id, parse_tmdbid, require_online, search_loop


@dataclass
class Preamble:
    """Values shared by movie and TV rip orchestration."""

    log: RipLog
    pick: TMDBPick
    display: str
    media: str
    titles_full: list[TitleInfo]
    stage_movie: Path
    stage_tv: Path
    minlength: int
    keep: str
    disc: int


def rip_preamble(kind: str, script: str, args) -> Preamble:
    """Mount staging, gate the disc, and resolve common TMDB metadata."""
    nfs_videos = os.environ.get("NFS_VIDEOS", "")
    videos_mnt = os.environ.get("VIDEOS_MOUNT", "/mnt/videos")

    ensure_mounted(videos_mnt, nfs_videos)
    disc = getattr(args, "disc", 0)
    stage_movie = Path(videos_mnt) / "rip" / "movies"
    stage_tv = Path(videos_mnt) / "rip" / "tv"
    if disc > 0:
        # Per-disc subdir so two concurrent rips never rename each other's files;
        # finished files are promoted to the parent dir after renaming.
        stage_movie = stage_movie / f"d{disc}"
        stage_tv = stage_tv / f"d{disc}"
    stage_dirs = [stage_movie, stage_tv] if kind == "movie" else [stage_tv, stage_movie]
    for stage_dir in stage_dirs:
        stage_dir.mkdir(parents=True, exist_ok=True)

    log = RipLog(script, "makemkvcon", "-")
    log.start()

    minlength = int(os.environ.get("RIP_MINLENGTH", "120"))
    media, label, titles_full = scan_disc(minlength, disc=disc)
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

    purge_junk(stage_dirs)
    require_online()

    if args.tmdbid:
        try:
            resolved_kind, tmdb_id = parse_tmdbid(args.tmdbid, kind)
            pick = fetch_by_id(resolved_kind, tmdb_id)
        except (ValueError, RuntimeError) as e:
            print(f"skive: TMDB resolve failed: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        pick = search_loop(kind, prefill=label)

    display = pick.title
    if pick.year and pick.year != "-":
        display = f"{pick.title} ({pick.year})"
    display = sanitize(display)
    log.name = display

    keep = iso2b(pick.original_language) if pick.original_language and pick.original_language != "-" else ""
    return Preamble(log, pick, display, media, titles_full, stage_movie, stage_tv, minlength, keep, disc)
