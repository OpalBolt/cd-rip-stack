"""Music rip orchestration: cyanrip wrapper, Multiple releases prompt, rip name from tags."""

import os
import re
import sys
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from rich.prompt import Prompt

from skive import common, ui
from skive.common import RipLog, ensure_mounted, kill_child_after_grace
from skive.cyanrip_log import (
    album_header_field,
    album_header_name,
    disc_number_from_log,
    parse_cyanrip_log,
)
from skive.validation import (
    VALIDATION_FILENAME,
    album_document,
    load_validation,
    merge_album_document,
    write_validation,
)


def rip_music(args) -> None:
    """Main music rip orchestration.

    Args:
        args: argparse.Namespace from cmd_rip_music

    Raises:
        SystemExit: On any error or user abort
    """
    # Validate offset
    if args.offset is None:
        ui.fail("usage: skive rip music <offset> [cyanrip_flags...]")
        ui.fail("  offset       drive sample offset (see drive table in README)")
        ui.fail("  cyanrip_flags passed through to cyanrip (e.g. -N, -R x)")
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
    view = ui.view() or ui.RunView()  # a test may pre-set a tty/live view
    ui.set_view(view)
    summary_tracks: list[dict] = []
    try:
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

            # Run cyanrip with tee-like capture. The stream watch fills `header`
            # with the album header pair mid-rip and names the run as soon as the
            # pair is complete.
            header: dict = {}
            rc, cap_text = _cyanrip_run(args.offset, flags, rip_dir, log, header)

            # Handle "Multiple releases found" — suspended: the prompt prints
            # normally (scrollback), the view redraws after the answer.
            if rc != 0 and "Multiple releases found" in cap_text:
                with ui.suspend():
                    choice = Prompt.ask("Multiple MusicBrainz releases — pick one (blank to abort): ", default="")
                if choice:
                    log.note("rip", f"release pick: {choice}")
                    # Re-run with -R <choice>; a fresh header, so the re-run's
                    # own header re-print renames the run.
                    header = {}
                    rc, _ = _cyanrip_run(args.offset, ["-R", choice] + flags, rip_dir, log, header)
                else:
                    ui.info("aborting")
                    rc = 1

            # cyanrip found no MusicBrainz release for this TOC — hand the user
            # the attach URL it printed so the disc can be submitted.
            if rc != 0 and "Unable to find release info" in cap_text:
                attach = re.search(r"https://musicbrainz\.org/cdtoc/attach\?\S+", cap_text)
                if attach:
                    note = f"no MusicBrainz release: submit this disc — {attach.group(0)}"
                    ui.warn(note)
                    log.note("rip", note)

            # Name before phase_end so the rip ok line already carries it. The
            # album-artist header pair wins over the staging scan (a Various
            # Artists disc is not named after whichever track artist encoded
            # last); on success everything else falls back to the FLAC tag scan,
            # which also yields the staging folder. A failed run never scans
            # staging — its name is the header's (pair, or album alone) or '-'.
            # An interrupt keeps whatever the stream set mid-rip (no header: '-').
            name, rel_folder = "-", "-"
            if rc == 0:
                name, rel_folder = _newest_flac_album(rip_dir)
            header_name = album_header_name(header)
            if header_name and header.get("album_artist"):
                name = header_name
            elif rc != 0:
                name = header_name or "-"
            log.name = name
            log.phase_end()
            log.note("rip", f"folder: {rel_folder}")
            if rc == 0:
                summary_tracks = _write_cd_record(rip_dir, rel_folder, args.offset, log)
        except KeyboardInterrupt:
            # CONTEXT.md Interrupt: _cyanrip_run already stopped the child (SIGINT,
            # grace window, kill). Music staging has no junk labels — cyanrip writes
            # its final-named FLACs straight into rip/ — so there is no partial of
            # ours to purge; record status=interrupted (finish() below stays
            # silent), one line, exit 130. The view closes first so its Live
            # never garbles the fail line.
            view.close()
            rc = 130
            log.interrupted()
            ui.fail("interrupted")
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        finally:
            log.finish(rc)
        # Close the view before the summary prints (its Live redraw would
        # garble it); the outer close is idempotent. No summary on interrupt —
        # rip_movie/rip_tv precedent.
        view.close()
        if rc != 130:
            # The run summary: per-track rows (track, duration, result) with the
            # count. Rows exist only for a clean rip's parsed report; a failed
            # run never scans staging and has no per-track truth, so its count
            # line reports the run itself (1 failed) instead of phantom rows.
            rows = [(str(i + 1),
                     _summary_hms(t["dur_s"]) if t["dur_s"] is not None else "?",
                     "ok")
                    for i, t in enumerate(summary_tracks)]
            ui.run_summary(rows, len(rows), 0 if rc == 0 else 1,
                           columns=("track", "duration", "result"),
                           replay=view.replay_lines())
    finally:
        view.close()
        ui.clear_view()

    sys.exit(rc)


def _summary_hms(secs: int) -> str:
    """Duration as H:MM:SS (the TV/movie summary format)."""
    return f"{secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def _write_cd_record(rip_dir: Path, rel_folder: str, offset: str,
                     log: RipLog) -> list[dict]:
    """Write the CD provenance record without making parsing a failure gate.
    Returns the record's track list (empty when no album folder) for the run
    summary."""
    if rel_folder in {"", "-"}:
        return []
    folder = rip_dir / rel_folder
    if not folder.is_dir():
        log.note("rip", f"artifacts missing in {folder}: folder does not exist")
        return []
    files = sorted(path.name for path in folder.iterdir())
    log_files = [path for path in folder.glob("*.log") if path.is_file()]
    cue_files = [path for path in folder.glob("*.cue") if path.is_file()]
    if not log_files or not cue_files:
        log.note("rip", f"artifacts missing in {folder}: found {files}")

    parsed = {"offset": None, "tracks": []}
    disc_number = "1"
    if log_files:
        # cyanrip appends each rip's report to the same album folder.  The
        # newest report is the one produced by this invocation; its cue has
        # the same stem when the pair is complete.
        report_path = max(log_files, key=lambda path: path.stat().st_mtime)
        try:
            report = report_path.read_text(encoding="utf-8", errors="replace")
            parsed = parse_cyanrip_log(report)
            disc_number = disc_number_from_log(report, report_path.stem)
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
    tracks = _staged_tracks(folder, parsed["tracks"])
    doc = album_document("cd", parsed.get("release"), tracks,
                         discs={disc_number: disc})
    old = load_validation(folder / VALIDATION_FILENAME) or {}
    write_validation(folder / VALIDATION_FILENAME, merge_album_document(old, doc))
    if parsed["tracks"]:
        # Proof summary, never a gate: counts over the parsed tracks, with the
        # not_found/partial counts named only when nonzero.
        accurate = sum(1 for track in parsed["tracks"] if track["status"] == "accurate")
        confidence = max((track["confidence"] or 0) for track in parsed["tracks"])
        note = f"validation: {len(tracks)} tracks, {accurate} accurate (conf {confidence})"
        not_found = sum(1 for track in parsed["tracks"] if track["status"] == "not_found")
        partial = sum(1 for track in parsed["tracks"] if track["status"] == "partial")
        if not_found:
            note += f", {not_found} not_found"
        if partial:
            note += f", {partial} partial"
        log.note("rip", note)
    return tracks


_TRACK_FILE_NUMBER = re.compile(r"^(\d+)\s*-\s")


def _staged_tracks(folder: Path, parsed_tracks: list[dict]) -> list[dict]:
    """The staged record's track list, born at rip from the folder's FLACs
    (cyanrip Outputs: flac) plus the parsed report: a file pairs to its
    parsed track by the leading ``NN - `` number in its name, positionally
    without one. The parsed durations are the record — no ffprobe."""
    by_number = {track["track"]: track for track in parsed_tracks}
    tracks = []
    for position, path in enumerate(sorted(folder.glob("*.flac")), start=1):
        number_match = _TRACK_FILE_NUMBER.match(path.name)
        number = int(number_match.group(1)) if number_match else position
        parsed = by_number.get(number)
        tracks.append({"file": path.name,
                       "dur_s": parsed["dur_s"] if parsed else None,
                       "format": path.suffix.lstrip(".").lower()})
    return tracks


def _progress_line(line: str) -> bool:
    """cyanrip progress heuristic for -v: its percent updates carry a literal
    '%'. The exact line shapes were never pinned down; anything misclassified
    still shows at -vv or is replayed verbatim on failure, so a miss is
    benign. Verify against a real rip on first run."""
    return "%" in line


_TRACK_DONE = re.compile(r"^Track (\d+) ripped and encoded successfully")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _panel_status(view, header: dict | None, offset: str, track: int | None) -> None:
    """Status panel: the album name ('-' until the header pair names the run,
    mirroring the log.name rule), the offset, and the track position once
    cyanrip reports one."""
    name = album_header_name(header) if header else ""
    lines = [f"ripping {name or '-'}", f"offset {offset}"]
    if track is not None:
        lines.append(f"track {track}")
    view.status(*lines)


def _cyanrip_run(offset: str, flags: list[str], rip_dir: Path, log=None,
                 header: dict | None = None) -> tuple[int, str]:
    """Run cyanrip with merged output captured, gated on the console by the
    verbosity tiers (quiet by default, progress lines at -v, everything at
    -vv; a failed run replays what was hidden, story 7), and written verbatim
    to the run's raw log (log is None in tests).

    When header is a dict, the stream watch fills it with the Album /
    Album artist header pair as those lines print mid-rip, setting log.name
    the moment the pair is complete. A -R re-run gets a fresh dict from its
    caller, so the re-run's own header names the run."""
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
    view = ui.view()  # None in the direct-call tests: plain prints, as before
    track_no = None
    if view is not None:
        _panel_status(view, header, offset, None)
    try:
        if process.stdout is not None:
            verbose = common.VERBOSITY
            for line in process.stdout:
                stripped = line.rstrip()
                surfaced = verbose >= 2 or (verbose >= 1 and _progress_line(stripped))
                if surfaced:
                    if view is not None:
                        view.tool("cyanrip", stripped)
                    else:
                        print(line, end="", flush=True)
                    pct = _PCT.search(stripped)
                    if view is not None and pct:
                        view.progress(float(pct.group(1)), None)
                elif stripped:
                    hidden.append(stripped)
                if log is not None:
                    log.raw("cyanrip", stripped)
                done = _TRACK_DONE.match(stripped)
                renamed = False
                if header is not None:
                    field = album_header_field(stripped)
                    if field is not None:
                        key, value = field
                        header[key] = value
                        if header.get("album_artist"):
                            header_name = album_header_name(header)
                            if header_name:
                                if log is not None:
                                    log.name = header_name
                                renamed = True
                if done:
                    track_no = int(done.group(1))
                if view is not None and (done or renamed):
                    _panel_status(view, header, offset, track_no)
                captured.append(line)

        rc = process.wait()
    except KeyboardInterrupt:
        # CONTEXT.md Interrupt: forward to the child, grace window, then kill,
        # and let the interrupt rip_music's handler log status=interrupted.
        kill_child_after_grace(process)
        raise
    if rc != 0:
        for line in hidden:
            if view is not None:
                view.tool("cyanrip", line)
            else:
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
        ui.fail("MusicBrainz is unreachable")
        sys.exit(1)
