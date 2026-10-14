"""Music rip orchestration: cyanrip wrapper, Multiple releases prompt, rip name from tags."""

import os
import sys
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from rich.prompt import Prompt

from skive import common
from skive.common import RipLog, ensure_mounted


def rip_music(args) -> None:
    """Main music rip orchestration.

    Args:
        args: argparse.Namespace from cmd_rip_music

    Raises:
        SystemExit: On any error or user abort
    """
    # Validate offset
    if args.offset is None:
        print("usage: skive rip music <offset> [cyanrip_flags...]", file=sys.stderr)
        print("  offset       drive sample offset (see drive table in README)", file=sys.stderr)
        print("  cyanrip_flags passed through to cyanrip (e.g. -N, -R x)", file=sys.stderr)
        sys.exit(2)

    # Environment
    nfs_music = os.environ.get("NFS_MUSIC", "")
    music_mnt = os.environ.get("MUSIC_MOUNT", "/mnt/music")

    # Mount staging dir
    ensure_mounted(music_mnt, nfs_music)
    rip_dir = Path(music_mnt) / "rip"
    rip_dir.mkdir(parents=True, exist_ok=True)

    # RipLog
    log = RipLog("skive rip music", f"cyanrip offset={args.offset}", "-")
    log.start()

    # Online gate: skip if -N in cyanrip_flags
    flags = args.cyanrip_flags or []
    skip_online = "-N" in flags

    if not skip_online:
        require_online()

    log.phase("rip")

    # Run cyanrip with tee-like capture
    rc, cap_text = _cyanrip_run(args.offset, flags, rip_dir)

    # Handle "Multiple releases found"
    if rc != 0 and "Multiple releases found" in cap_text:
        choice = Prompt.ask("Multiple MusicBrainz releases — pick one (blank to abort): ", default="")
        if choice:
            # Re-run with -R <choice>
            rc, _ = _cyanrip_run(args.offset, ["-R", choice] + flags, rip_dir)
        else:
            print("skive: aborting", file=sys.stderr)
            rc = 1

    log.phase_end()

    # Rip name from newest FLAC
    name = _newest_flac_album(rip_dir)
    log.name = name

    log.finish(rc)
    if rc == 0:
        common.eject_disc(0, log)
    sys.exit(rc)


def _cyanrip_run(offset: str, flags: list[str], rip_dir: Path) -> tuple[int, str]:
    """Run cyanrip with merged output captured and streamed to stdout."""
    cmd = ["cyanrip", "-s", offset] + flags
    process = subprocess.Popen(
        cmd,
        cwd=rip_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    captured = []
    if process.stdout is not None:
        for line in process.stdout:
            print(line, end="", flush=True)
            captured.append(line)

    return process.wait(), "".join(captured)


def _newest_flac_album(rip_dir: Path) -> str:
    """Get rip name from the newest FLAC's ALBUM tag.

    Returns "Artist - Album" or just "Album" or "-" if no FLAC/tags.
    """
    flacs = list(rip_dir.glob("*.flac"))
    if not flacs:
        return "-"

    # Find newest by mtime
    newest = max(flacs, key=lambda f: f.stat().st_mtime)

    # Run metaflac to get ALBUM and ARTIST
    album = _metaflac_tag(newest, "ALBUM")
    artist = _metaflac_tag(newest, "ARTIST")

    if not album:
        return "-"

    if artist:
        return f"{artist} - {album}"
    return album


def _metaflac_tag(flac: Path, tag: str) -> str:
    """Get a tag value from a FLAC file via metaflac."""
    try:
        result = subprocess.run(
            ["metaflac", f"--show-tag={tag}", str(flac)],
            capture_output=True,
            text=True,
            check=False
        )
        # Output format: TAG=value\n
        for line in result.stdout.strip().splitlines():
            if "=" in line:
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


def require_online() -> None:
    """Probe MusicBrainz; exit if offline."""
    request = urllib.request.Request("https://musicbrainz.org/", method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=5):
            pass
    except urllib.error.HTTPError as e:
        e.close()
    except (urllib.error.URLError, TimeoutError, OSError):
        print("skive: MusicBrainz is unreachable", file=sys.stderr)
        sys.exit(1)
