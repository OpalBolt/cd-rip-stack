"""Music rip orchestration: cyanrip wrapper, Multiple releases prompt, rip name from tags."""

import os
import sys
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from rich.prompt import Prompt

from skive import common
from skive.common import RipLog, ensure_mounted, kill_child_after_grace
from skive.cyanrip_log import parse_cyanrip_log
from skive.validation import album_document, write_validation, VALIDATION_FILENAME


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
    rip_dir = Path(music_mnt) / "rip" / "cd"
    rip_dir.mkdir(parents=True, exist_ok=True)

    # RipLog
    log = RipLog("skive rip music", f"cyanrip offset={args.offset}", "-", root=music_mnt)
    log.start()

    # Log the total on every exit — success, Ctrl-C, or failure — so the raw
    # log is unlinked on rc==0 and kept otherwise (sync_sd pattern).
    rc = 0
    try:
        # Online gate: skip if -N in cyanrip_flags
        flags = args.cyanrip_flags or []
        skip_online = "-N" in flags

        if not skip_online:
            require_online()

        log.phase("rip")

        # Run cyanrip with tee-like capture
        rc, cap_text = _cyanrip_run(args.offset, flags, rip_dir, log)

        # Handle "Multiple releases found"
        if rc != 0 and "Multiple releases found" in cap_text:
            choice = Prompt.ask("Multiple MusicBrainz releases — pick one (blank to abort): ", default="")
            if choice:
                # Re-run with -R <choice>
                rc, _ = _cyanrip_run(args.offset, ["-R", choice] + flags, rip_dir, log)
            else:
                print("skive: aborting", file=sys.stderr)
                rc = 1

        log.phase_end()

        # Rip name from newest FLAC
        name, rel_folder = _newest_flac_album(rip_dir)
        log.name = name
        log.note("rip", f"folder: {rel_folder}")
        if rc == 0:
            _write_cd_record(rip_dir, rel_folder, args.offset, log)
    except KeyboardInterrupt:
        # CONTEXT.md Interrupt: _cyanrip_run already stopped the child (SIGINT,
        # grace window, kill). Music staging has no junk labels — cyanrip writes
        # its final-named FLACs straight into rip/ — so there is no partial of
        # ours to purge; record status=interrupted (finish() below stays
        # silent), one line, exit 130.
        rc = 130
        log.interrupted()
        print("skive: interrupted", file=sys.stderr)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        log.finish(rc)

    sys.exit(rc)


def _write_cd_record(rip_dir: Path, rel_folder: str, offset: str,
                     log: RipLog) -> None:
    """Write the CD provenance record without making parsing a failure gate."""
    if rel_folder in {"", "-"}:
        return
    folder = rip_dir / rel_folder
    if not folder.is_dir():
        log.note("rip", f"artifacts missing in {folder}: folder does not exist")
        return
    files = sorted(path.name for path in folder.iterdir())
    log_files = sorted(path for path in folder.glob("*.log") if path.is_file())
    cue_files = sorted(path for path in folder.glob("*.cue") if path.is_file())
    if not log_files or not cue_files:
        log.note("rip", f"artifacts missing in {folder}: found {files}")

    parsed = {"offset": None, "tracks": []}
    if log_files:
        report_path = log_files[0]
        try:
            report = report_path.read_text(encoding="utf-8", errors="replace")
            parsed = parse_cyanrip_log(report)
        except OSError:
            report = ""
        if not parsed["tracks"]:
            log.note("rip", "accuraterip: unparsed")
            log.keep_raw()
            for line in report.splitlines()[:20]:
                log.raw("cyanrip.log", line)

    try:
        parsed_offset = parsed.get("offset")
        if parsed_offset is None:
            parsed_offset = int(offset)
    except (TypeError, ValueError):
        parsed_offset = offset
    disc = {"rip_run": log.run, "offset": parsed_offset}
    if parsed["tracks"]:
        disc["accuraterip"] = parsed["tracks"]
    doc = album_document("cd", None, [], discs={"1": disc})
    write_validation(folder / VALIDATION_FILENAME, doc)


def _progress_line(line: str) -> bool:
    """cyanrip progress heuristic for -v: its percent updates carry a literal
    '%'. The exact line shapes were never pinned down; anything misclassified
    still shows at -vv or is replayed verbatim on failure, so a miss is
    benign. Verify against a real rip on first run."""
    return "%" in line


def _cyanrip_run(offset: str, flags: list[str], rip_dir: Path, log=None) -> tuple[int, str]:
    """Run cyanrip with merged output captured, gated on the console by the
    verbosity tiers (quiet by default, progress lines at -v, everything at
    -vv; a failed run replays what was hidden, story 7), and written verbatim
    to the run's raw log (log is None in tests)."""
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
    hidden = []  # console-suppressed lines, replayed on failure
    try:
        if process.stdout is not None:
            verbose = common.VERBOSITY
            for line in process.stdout:
                stripped = line.rstrip()
                if verbose >= 2 or (verbose >= 1 and _progress_line(stripped)):
                    print(line, end="", flush=True)
                elif stripped:
                    hidden.append(stripped)
                if log is not None:
                    log.raw("cyanrip", stripped)
                captured.append(line)

        rc = process.wait()
    except KeyboardInterrupt:
        # CONTEXT.md Interrupt: forward to the child, grace window, then kill,
        # and let the interrupt rip_music's handler log status=interrupted.
        kill_child_after_grace(process)
        raise
    if rc != 0:
        for line in hidden:
            print(line, flush=True)
    return rc, "".join(captured)


def _newest_flac_album(rip_dir: Path) -> tuple[str, str]:
    """Get rip name and relative folder from the newest FLAC under rip_dir.

    Returns (name, rel_folder) where:
    - name is "Artist - Album", "Album", or "-" if no FLAC/tags.
    - rel_folder is the relative path from rip_dir to the FLAC's parent ('.' when flat, or '-' if no FLAC).
    """
    flacs = list(rip_dir.rglob("*.flac"))
    if not flacs:
        return "-", "-"

    # Find newest by mtime
    newest = max(flacs, key=lambda f: f.stat().st_mtime)

    # Relative folder to rip_dir ('.' if directly in rip_dir)
    try:
        rel_path = newest.parent.relative_to(rip_dir)
        rel_folder = str(rel_path)
    except ValueError:
        rel_folder = "."

    # Run metaflac to get ALBUM and ARTIST
    album = _metaflac_tag(newest, "ALBUM")
    artist = _metaflac_tag(newest, "ARTIST")

    if not album:
        return "-", rel_folder

    if artist:
        return f"{artist} - {album}", rel_folder
    return album, rel_folder


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
