"""Shared preamble for movie and TV ripping."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from skive.common import RipLog, ensure_mounted, sanitize
from skive.makemkv import (
    TitleInfo, ambiguous, fast_scan_bluray, fast_scan_dvd, is_recordable, iso2b, purge_junk, scan_disc, sniff_disc,
)
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
    fast_scan: bool = False
    scan_durations: list[int] | None = None  # durations in scan order, fast path only


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
    media, label = sniff_disc(disc=disc)
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

    # Fast path (ADR 0002): movie/TV + pressed video disc + no -T/--extras
    # gates on scanner durations in seconds — lsdvd (mounted DVD) or
    # `ffprobe bluray:` (device listing, DRM-free). Recordable BD already
    # exits at the gate above, so only pressed BD reaches the fast scan.
    # Flagged runs (-T menu/spec, --extras) stay on the full scan. Scanner
    # title numbers never match MakeMKV's, so TV joins staged files back by
    # duration (rip_tv.rename_tv_fast); movies rip all and keep MakeMKV's own
    # _tNN numbers (rename_staged is join-free).
    fast_scan = False
    scan_durations: list[int] | None = None
    titles_full: list[TitleInfo] = []
    if (kind in ("tv", "movie") and media.startswith(("DVD", "BD"))
            and getattr(args, "titles", None) is None
            and not getattr(args, "extras", False)):
        scanner = fast_scan_dvd if media.startswith("DVD") else fast_scan_bluray
        try:
            scan_durations = scanner(disc)
        except RuntimeError as e:
            print(f"skive: fast scan failed ({e}) — falling back to the full MakeMKV scan", file=sys.stderr)
            log.note("scan", "fast scan failed — full-scan fallback")
        else:
            if ambiguous(scan_durations):
                # Escalation trigger (ADR 0002): duplicate/decoy/angle playlists
                # that the fast path's duration join would mis-handle — discard
                # the fast result and take the full scan path (same code path
                # as the RuntimeError fallback below).
                print("skive: fast scan found duplicate-duration titles — escalating to the full MakeMKV scan", file=sys.stderr)
                log.note("scan", "duplicate durations — full-scan escalation")
                scan_durations = None
            else:
                fast_scan = True
                titles_full = [TitleInfo(number=i, seconds=d, size=0, audio="", subtitles="", scrambled=False)
                               for i, d in enumerate(scan_durations)]
    if not fast_scan:
        _, label, titles_full = scan_disc(minlength, disc=disc)

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
    return Preamble(log, pick, display, media, titles_full, stage_movie, stage_tv,
                    minlength, keep, disc, fast_scan=fast_scan, scan_durations=scan_durations)


def fast_scan_header(display: str, n: int) -> str:
    """Two-line rip header for fast-scan runs (ADR 0002).

    Fast-scan infos carry size 0 and no audio, so this omits the size/audio
    lines that makemkv.rip_header would fill with 0.0 B and '?'.

    Args:
        display: Sanitized display title shown on the header's first line.
        n: Number of titles reported on the second line.

    Returns:
        The header string passed to makemkv.rip_titles.
    """
    return (f"{display}\n"
            f"  titles: {n} (fast scan — no size/audio preview)")
