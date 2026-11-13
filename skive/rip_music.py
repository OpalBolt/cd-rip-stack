"""Music rip orchestration: cyanrip wrapper, MusicBrainz miss box, rip name from tags."""

import os
import re
import shlex
import sys
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from skive import common, ui
from skive.common import RipLog, ensure_mounted, kill_child_after_grace
from skive.scan import sniff_disc
from skive.cyanrip_log import (
    album_header_field,
    album_header_name,
    disc_number_from_log,
    parse_cyanrip_log,
    parse_musicbrainz_miss,
    parse_track_durations,
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
        ui.fail("usage: skive rip music [-d N] <offset> [cyanrip_flags...]")
        ui.fail("  offset       drive sample offset (see drive table in README)")
        ui.fail("  -d N         drive number, same as rip movie/tv (default 0)")
        ui.fail("  cyanrip_flags passed through to cyanrip (e.g. -N, -R x)")
        sys.exit(2)

    # argparse.REMAINDER swallows a `-d N` typed after the offset into the
    # cyanrip pass-through, where cyanrip reads the bare number as an image
    # filename ("can't open nrg image file 1"). Claim numeric -d/--disc as
    # our drive number before it reaches cyanrip; non-numeric values stay
    # passthrough (cyanrip's own -d takes a device path).
    flags = args.cyanrip_flags or []
    kept: list[str] = []
    i = 0
    while i < len(flags):
        tok = flags[i]
        nxt = flags[i + 1] if i + 1 < len(flags) else None
        if tok in ("-d", "--disc") and nxt is not None and nxt.isdigit():
            args.disc = int(nxt)
            i += 2
            continue
        # A `--` typed after a trailing -d n survives the REMAINDER; cyanrip's
        # getopt stops there and reads what follows (-R n) as an image filename.
        if tok != "--":
            kept.append(tok)
        i += 1
    args.cyanrip_flags = kept

    # Environment
    nfs_music = os.environ.get("NFS_MUSIC", "")
    music_mnt = os.environ.get("MUSIC_MOUNT", "/mnt/music")

    # Mount staging dir
    ensure_mounted(music_mnt, nfs_music)
    rip_dir = Path(music_mnt) / "rip" / "cd"
    rip_dir.mkdir(parents=True, exist_ok=True)

    # RipLog
    # Same -d N as rip movie/tv: makemkv's disc index resolved to a real device
    # by the DRV line (a bare /dev/srN guess can point at the OTHER drive).
    _, _, device = sniff_disc(disc=args.disc)
    device = device or f"/dev/sr{args.disc}"
    log = RipLog("skive rip music", f"cyanrip offset={args.offset} drive={device}",
                 "-", root=music_mnt)
    log.start()
    view = ui.view() or ui.RunView()  # a test may pre-set a tty/live view
    ui.set_view(view)
    summary_tracks: list[dict] = []
    header: dict = {}  # filled mid-rip by the stream watch; survives an interrupt
    miss = None  # the MusicBrainz miss (CONTEXT.md): a box, no summary table
    holed = False  # final pass clean but earlier stalls skipped (a failure)
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

            # Stall guard (ADR 0018): arm per-track budgets from a TOC-only
            # pre-pass. -Z is slow by design — disarmed. Any pre-pass failure
            # leaves the guard unarmed and the run proceeds normally.
            budgets = None
            if "-Z" not in flags:
                durations = _toc_durations(args.offset, flags, device)
                if durations is None:
                    ui.info("stall guard unarmed: TOC pre-pass failed", phase="rip")
                else:
                    budgets = {n: max(600.0, 3.0 * d) for n, d in durations.items()}

            # Run cyanrip with tee-like capture. The stream watch fills `header`
            # with the album header pair mid-rip and names the run as soon as the
            # pair is complete. A tripped pass (stall guard, ADR 0018) does not
            # fail the run: its 0-byte stubs are purged and the pass re-runs with
            # `-l` = the track universe minus every stalled track so far, until
            # the pass is clean, the cap (3 stalled tracks) is reached, or a
            # continuation pass fails on its own (the existing failure path).
            pass_flags = list(flags)
            while True:
                stalled_before = set(header.get("_stalled", []))
                pass_t0 = time.time()
                rc, cap_text = _cyanrip_run(args.offset, pass_flags, rip_dir, log,
                                             header, drive=device, budgets=budgets)
                stalled = header.get("_stalled", [])
                continuation = None
                if rc != 0 and set(stalled) > stalled_before:
                    continuation = _continuation_pass(args.cyanrip_flags or [], header)
                if continuation:
                    _purge_pass_stubs(rip_dir, pass_t0)
                    pass_flags = continuation
                    continue
                if rc != 0 and set(stalled) > stalled_before:
                    log.note("rip", "wipe or replace the disc: tracks "
                             + ", ".join(str(n) for n in sorted(stalled))
                             + " stalled")
                break

            # A MusicBrainz miss never prompts: the run fails and the box
            # (after the view closes) tells the operator what to fix.
            miss = parse_musicbrainz_miss(cap_text) if rc != 0 else None
            if miss and miss["case"] == "multiple":
                log.note("rip", f"several MusicBrainz releases: re-run with -R <n> — {miss['url']}",
                         echo=False)
            elif miss:
                log.note("rip", f"no MusicBrainz release: submit this disc — {miss['url']}",
                         echo=False)

            # Name before phase_end so the rip ok line already carries it. The
            # album-artist header pair wins over the staging scan (a Various
            # Artists disc is not named after whichever track artist encoded
            # last); on success everything else falls back to the FLAC tag scan,
            # which also yields the staging folder. A failed run never scans
            # staging — its name is the header's (pair, or album alone) or '-'.
            # An interrupt keeps whatever the stream set mid-rip (no header: '-').
            # A holed run (final pass clean, earlier stalls skipped) is a
            # failure: nonzero rc, failed result line, raw log kept — but it
            # keeps the success path's naming and CD record.
            holed = rc == 0 and bool(header.get("_stalled"))
            rip_ok = rc == 0 or holed
            name, rel_folder = "-", "-"
            if rip_ok:
                name, rel_folder = _newest_flac_album(rip_dir)
            header_name = album_header_name(header)
            if header_name and header.get("album_artist"):
                name = header_name
            elif rc != 0:
                name = header_name or "-"
            log.name = name
            if rip_ok:
                log.phase_end()
            elif miss:
                log.fail_open_phase("FAIL: " + _miss_result(miss))
            else:
                log.fail_open_phase(f"FAIL({rc})")
            if rel_folder != "-":  # nothing ripped: no folder to name
                log.note("rip", f"folder: {rel_folder}")
            if rip_ok:
                summary_tracks = _write_cd_record(rip_dir, rel_folder,
                                                   args.offset, log,
                                                   header.get("_stalled"))
            if holed:
                # One note per stalled track: the pre-filled hand re-rip
                # command (offset, drive, pass-through without any -l, then
                # the full stalled list) — the miss box's rerun hint shape.
                holed_flags = list(args.cyanrip_flags or [])
                if "-l" in holed_flags:
                    r = holed_flags.index("-l")
                    del holed_flags[r:r + 2]
                rerun = ["skive", "rip", "music"]
                if args.disc:
                    rerun += ["-d", str(args.disc)]
                rerun_cmd = shlex.join(rerun + [args.offset, "--", *holed_flags]
                                       + ["-l", ",".join(str(n) for n in
                                                         sorted(header["_stalled"]))])
                for number in sorted(header["_stalled"]):
                    log.note("rip", f"track {number} stalled — hand re-rip: {rerun_cmd}")
                rc = 1
        except KeyboardInterrupt:
            # CONTEXT.md Interrupt: _cyanrip_run already stopped the child (SIGINT,
            # grace window, kill). Music staging has no junk labels — cyanrip writes
            # its final-named FLACs straight into rip/ — so there is no partial of
            # ours to purge; record status=interrupted (finish() below stays
            # silent), one line, exit 130. The open track's frame closes, then
            # the view, so its Live never garbles the fail line.
            ui.item_end("fail", "interrupted")
            view.close()
            rc = 130
            log.interrupted()
            ui.fail("interrupted")
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        finally:
            log.finish(rc)
        # Close the view before the summary prints (its Live redraw would
        # garble it); the outer close is idempotent. Every exit path gets a
        # durable summary, including an interrupted rip.
        view.close()
        if holed:
            # One row per track: real track numbers from _tracks, durations
            # from the record's staged files; n_failed counts the holes.
            dur_by_number: dict[int, int | None] = {}
            for position, t in enumerate(summary_tracks, start=1):
                number_match = _TRACK_FILE_NUMBER.match(t["file"])
                number = int(number_match.group(1)) if number_match else position
                dur_by_number[number] = t["dur_s"]
            rows = [(str(number),
                     _summary_hms(dur_by_number[number])
                     if dur_by_number.get(number) is not None else "?",
                     result)
                    for number, result in sorted(header.get("_tracks", {}).items())]
            interrupt_note = None
        elif rc == 0:
            rows = [(str(i + 1),
                     _summary_hms(t["dur_s"]) if t["dur_s"] is not None else "?",
                     "ok")
                    for i, t in enumerate(summary_tracks)]
            interrupt_note = None
        else:
            # An interrupted rip keeps what the stream saw: finished tracks ok,
            # the one in flight interrupted. Nothing is deleted — cyanrip's
            # files stay in staging. A stalled rip builds the same rows: ok
            # for finished tracks, stalled for the tripped one.
            rows = [(str(number), "?", result)
                    for number, result in sorted(header.get("_tracks", {}).items())
                    ] if (rc == 130 or header.get("_stalled")) else []
            interrupt_note = "nothing deleted \u2014 staged files left in place" if rc == 130 else None
        n_ok = sum(row[2] == "ok" for row in rows)
        n_failed = sum(row[2] in ("failed", "stalled") for row in rows)
        if miss:
            # Keep the drive in the re-run: the claim above moved a trailing
            # -d n out of cyanrip_flags, and disc 0 is the other drive. Drop a
            # stale -R pair so the hint ends in exactly one -R <n>.
            flags = list(args.cyanrip_flags or [])
            if "-R" in flags:
                r = flags.index("-R")
                del flags[r:r + 2]
            rerun = ["skive", "rip", "music"]
            if args.disc:
                rerun += ["-d", str(args.disc)]
            ui.musicbrainz_miss(miss, shlex.join(
                rerun + [args.offset, "--", *flags]) + " -R <n>")
            ui.result_line("fail", _miss_result(miss),
                           elapsed=int((log._now() - log._start).total_seconds()))
        else:
            ui.run_summary(rows, n_ok, n_failed or (1 if rc not in (0, 130) else 0),
                           columns=("track", "duration", "result"),
                           replay=view.replay_lines(), log=log,
                           interrupt_note=interrupt_note,
                           failed=rc not in (0, 130))
    finally:
        view.close()
        ui.clear_view()

    sys.exit(rc)


def _miss_result(miss: dict) -> str:
    """The miss's result phrase (screen result line and run-log FAIL)."""
    return ("several MusicBrainz releases" if miss["case"] == "multiple"
            else "not on MusicBrainz")


def _summary_hms(secs: int) -> str:
    """Duration as H:MM:SS (the TV/movie summary format)."""
    return f"{secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def _write_cd_record(rip_dir: Path, rel_folder: str, offset: str,
                     log: RipLog, stalled: list[int] | None = None) -> list[dict]:
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
    if stalled:
        disc["stalled"] = sorted(stalled)
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


_TRACK_DONE = re.compile(r"^Track (\d+) ripped and encoded successfully")
# cyanrip prints each track's metadata block (indented) after the track
# finishes, e.g. "    title:                         The Healer".
_TRACK_TITLE = re.compile(r"^\s*title:\s*(.+)$", re.IGNORECASE)
# The live progress line, matched whole: "Ripping and encoding track 3,
# progress - 41.50%, ETA - 1h 2m" (the disc ETA: "27m", "45s", or absent).
_TRACK_PROGRESS = re.compile(
    r"Ripping(?: and encoding)? track (\d+), progress - (\d+(?:\.\d+)?)%"
    r"(?:, ETA - ((?:\d+h )?\d+[hms]))?")
# libmusicbrainz parser chatter ("Unrecognised disc element: 'offset-list'")
_UNRECOGNISED = re.compile(r"Unrecognised .*(?:element|attribute): '")
# cyanrip's QR code of the attach URL: rows of block characters only.
_QR_ROW = re.compile(r"[ \u2580\u2584\u2588]*[\u2580\u2584\u2588][ \u2580\u2584\u2588]*")
_RAW_PROGRESS_SECS = 5  # a progress line reaches the raw log at least this often
STALL_CAP = 3  # stalled tracks per run before the stall guard stops continuing (ADR 0018)


def _toc_durations(offset: str, flags: list[str],
                   drive: str | None) -> dict[int, float] | None:
    """Stall-guard TOC pre-pass: ``cyanrip -I`` (info only, no audio read)
    with the same offset, drive and release flags as the main run. Returns
    per-track durations from its ``Track n info:`` blocks. None on any
    failure: guard unarmed."""
    cmd = ["cyanrip", "-I", "-s", offset] + (["-d", drive] if drive else []) + flags
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                check=False, timeout=300)
        durations = parse_track_durations(result.stdout)
        if result.returncode != 0 or not durations:
            return None
    except Exception:  # any failure: OSError, timeout, tests' Popen mocks
        return None
    return durations


def _operator_l_tracks(flags: list[str]) -> set[int] | None:
    """The operator's own `-l` pass-through as a track-number set, None when
    no `-l` was given (cyanrip-style comma list)."""
    if "-l" not in flags:
        return None
    i = flags.index("-l")
    if i + 1 >= len(flags):
        return None
    try:
        return {int(tok) for tok in flags[i + 1].split(",") if tok.strip()}
    except ValueError:
        return None


def _continuation_pass(flags: list[str], header: dict) -> list[str] | None:
    """Flags for the continuation pass after a tripped pass: any operator `-l`
    replaced by the track universe (Disc tracks:, intersected with the
    operator's own -l) minus every stalled track so far. None when the
    remaining set is empty or three tracks have stalled — that is the cap."""
    if len(set(header.get("_stalled", []))) >= STALL_CAP:
        return None
    universe = set(range(1, (header.get("_disc_tracks") or 0) + 1))
    operator_l = _operator_l_tracks(flags)
    if operator_l is not None:
        universe &= operator_l
    remaining = sorted(universe - set(header.get("_stalled", [])))
    if not remaining:
        return None
    kept = list(flags)
    if "-l" in kept:
        r = kept.index("-l")
        del kept[r:r + 2]
    return kept + ["-l", ",".join(str(n) for n in remaining)]


def _purge_pass_stubs(rip_dir: Path, since: float) -> None:
    """Delete the killed pass's 0-byte *.flac/*.log/*.cue stubs under rip_dir
    (recursive) written since `since` — the pass's start wall clock, so
    concurrent or older albums are untouched. Nonzero files stay."""
    for path in rip_dir.rglob("*"):
        if path.suffix not in (".flac", ".log", ".cue") or not path.is_file():
            continue
        try:
            st = path.stat()
            if st.st_size == 0 and st.st_mtime >= since:
                path.unlink()
        except OSError:
            continue


def _cyanrip_run(offset: str, flags: list[str], rip_dir: Path, log=None,
                 header: dict | None = None, drive: str | None = None,
                 budgets: dict[int, float] | None = None) -> tuple[int, str]:
    """Run cyanrip with merged output captured, gated on the console by the
    verbosity tiers (quiet by default, progress lines at -v, everything at
    -vv; a failed run replays what was hidden, story 7, except a MusicBrainz
    miss), and written to the run's raw log (log is None in tests) with
    progress lines throttled and libmusicbrainz chatter dropped.

    When header is a dict, the stream watch fills it with the Album /
    Album artist header pair as those lines print mid-rip, setting log.name
    the moment the pair is complete, and records each track's outcome under
    header["_tracks"] ({number: "ok" | "interrupted"}) for the interrupt
    summary.

    With budgets (stall guard, ADR 0018), each track may not rip+encode
    longer than its budget, wall clock from its first progress line; a trip
    kills the pass (SIGINT, grace, kill) and forces a nonzero rc."""
    cmd = ["cyanrip", "-s", offset] + (["-d", drive] if drive else []) + flags
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
    on_tty = view is not None and view.tty
    track_total = None
    eta = None  # cyanrip's disc ETA from the last progress line
    raw_kept = (None, -1, 0.0)  # (track, whole percent, monotonic time) last raw-logged
    raw_held = None  # (track, line): the newest progress line not raw-logged yet
    pending_track = None  # completed, its title line not seen yet
    headed_tracks: set[int] = set()
    finished_open = None  # completed, frame left open for its trailing metadata lines
    track_t0: dict[int, float] = {}  # first progress line per track
    track_took: dict[int, str] = {}  # rip time per completed track; absent with no progress seen
    outcomes = header.setdefault("_tracks", {}) if header is not None else {}
    tripped: int | None = None  # the stalled track that killed this pass

    def emit_opener() -> None:
        """The run opener, once, from whatever header has streamed (an album
        with no Album artist line is named by the album alone)."""
        if header is None or header.get("_opener_emitted"):
            return
        header_name = album_header_name(header)
        if header_name:
            ui.opener("skive rip music", header_name, f"offset {offset}")
            header["_opener_emitted"] = True

    def set_footer(number: int, title: str, pct: float) -> None:
        if on_tty:
            position = track_total or number
            view.footer(f"track {number}/{position}", title, f"offset {offset}")
            view.progress(pct, eta)

    def close_track(number: int) -> None:
        """TTY: the track's timed rip phase line; then its frame's done rule
        (no duration when no progress line timed it)."""
        took = track_took.get(number)
        if on_tty:
            ui.ok(f"track {number}", phase="rip", elapsed=took)
        ui.item_end("ok", "done", took or "")

    def close_finished() -> None:
        nonlocal finished_open
        if finished_open is not None:
            close_track(finished_open)
            finished_open = None

    def show_track(number: int, title: str, pct: float) -> None:
        nonlocal finished_open
        close_finished()  # the previous track's frame closes only as this one opens
        emit_opener()
        ui.item(number, track_total or number, title)
        headed_tracks.add(number)
        set_footer(number, title, pct)
        if outcomes.get(number) == "ok":  # headed only once it finished
            finished_open = number

    try:
        if process.stdout is not None:
            verbose = common.VERBOSITY
            for line in process.stdout:
                stripped = line.rstrip()
                if _UNRECOGNISED.match(stripped):
                    continue  # libmusicbrainz chatter: neither screen nor raw log
                progress = _TRACK_PROGRESS.fullmatch(stripped)
                # cyanrip's progress lines feed the footer on a TTY and never
                # reach scrollback; off a TTY they follow the verbosity tiers
                # (any '%' line at -v). QR rows never reach the screen.
                if (on_tty and progress) or _QR_ROW.fullmatch(stripped):
                    pass
                elif verbose >= 2 or (verbose >= 1 and "%" in stripped):
                    ui.tool("cyanrip", stripped)
                elif stripped:
                    hidden.append(stripped)
                if log is not None:
                    # Progress reaches the raw log once per whole percent per
                    # track or every few seconds; each track's last line is kept.
                    if progress:
                        number, now = int(progress.group(1)), time.monotonic()
                        whole = int(float(progress.group(2)))
                        if raw_held is not None and raw_held[0] != number:
                            log.raw("cyanrip", raw_held[1])
                        if ((number, whole) != raw_kept[:2]
                                or now - raw_kept[2] >= _RAW_PROGRESS_SECS):
                            log.raw("cyanrip", stripped)
                            raw_kept, raw_held = (number, whole, now), None
                        else:
                            raw_held = (number, stripped)
                    else:
                        if raw_held is not None:
                            log.raw("cyanrip", raw_held[1])
                            raw_held = None
                        log.raw("cyanrip", stripped)
                if stripped.startswith("Disc tracks:"):
                    try:
                        track_total = int(stripped.split(":", 1)[1].strip())
                    except ValueError:
                        track_total = None
                    if header is not None and track_total is not None:
                        header["_disc_tracks"] = track_total
                if stripped == "Tracks:":
                    emit_opener()  # before any track work begins
                if progress:
                    number = int(progress.group(1))
                    pct = float(progress.group(2))  # cyanrip's own track percent
                    eta = progress.group(3)
                    outcomes.setdefault(number, "interrupted")  # in flight
                    track_t0.setdefault(number, time.monotonic())
                    if number not in headed_tracks:
                        if pending_track is not None and pending_track not in headed_tracks:
                            # its frame first: it must not close this track's frame
                            show_track(pending_track, f"track {pending_track}", 100.0)
                            pending_track = None
                        show_track(number, f"track {number}", pct)
                    elif on_tty:
                        view.progress(pct, eta)
                    budget = budgets.get(number) if budgets else None
                    if (budget is not None and tripped is None
                            and time.monotonic() - track_t0[number] > budget):
                        # Stall guard trip: bounded kill of a track that stops
                        # being readable (ADR 0018). No timer thread — a grind
                        # emits progress lines constantly, this inline check
                        # suffices. cyanrip's own ETA is display-only.
                        tripped = number
                        outcomes[number] = "stalled"
                        if header is not None:
                            header.setdefault("_stalled", []).append(number)
                        n_stalled = len(set(header.get("_stalled", []))) if header else 1
                        ui.warn(f"track {number} stalled: over its "
                                f"{ui._hms(int(budget))} budget, killing the pass "
                                f"({n_stalled}/{STALL_CAP} stalled)",
                                phase="rip")
                        close_finished()
                        ui.item_end("fail", "stalled")
                        kill_child_after_grace(process)
                        break  # the killed child's stdout EOF ends the loop; break for fakes too
                    if budget is not None and on_tty:
                        # shortcut: ticks per progress line, not per second; a
                        # silent cyanrip freezes it (a grind still emits lines).
                        left = budget - (time.monotonic() - track_t0[number])
                        n_stalled = len(set(header.get("_stalled", []))) if header else 0
                        view.bg("stall guard", f"{ui._hms(max(int(left), 0))} left "
                                f"\u00b7 {n_stalled}/{STALL_CAP} stalled")
                title_match = _TRACK_TITLE.match(stripped)
                if title_match and pending_track is not None:
                    title = title_match.group(1).strip()
                    if pending_track in headed_tracks:
                        set_footer(pending_track, title, 100.0)
                    else:
                        show_track(pending_track, title, 100.0)
                    pending_track = None
                if header is not None:
                    field = album_header_field(stripped)
                    if field is not None:
                        key, value = field
                        header[key] = value
                        header_name = album_header_name(header)
                        if header_name:
                            if log is not None:
                                log.name = header_name
                            if header.get("album_artist"):
                                emit_opener()
                done = _TRACK_DONE.match(stripped)
                if done:
                    if pending_track is not None and pending_track not in headed_tracks:
                        show_track(pending_track, f"track {pending_track}", 100.0)
                    pending_track = int(done.group(1))
                    outcomes[pending_track] = "ok"
                    if on_tty:
                        view.bg_clear("stall guard")
                    if pending_track in track_t0:
                        track_took[pending_track] = ui._hms(
                            int(time.monotonic() - track_t0[pending_track]))
                    if pending_track in headed_tracks:
                        finished_open = pending_track
                    if on_tty:
                        view.progress(100.0, eta)
                captured.append(line)

        rc = process.wait()
        if tripped is not None and rc == 0:
            # A SIGINT'd child may still exit cleanly; the trip fails the pass.
            rc = 1
        emit_opener()
        if pending_track is not None and pending_track not in headed_tracks:
            show_track(pending_track, f"track {pending_track}", 100.0)
        close_finished()
    except KeyboardInterrupt:
        close_finished()  # a completed track closes done, not interrupted
        # CONTEXT.md Interrupt: forward to the child, grace window, then kill,
        # and let the interrupt rip_music's handler log status=interrupted.
        kill_child_after_grace(process)
        raise
    finally:
        if on_tty:
            view.bg_clear("stall guard")
        if log is not None and raw_held is not None:
            log.raw("cyanrip", raw_held[1])
    # A MusicBrainz miss gets its own box (rip_music); no replay of the chatter.
    if rc != 0 and parse_musicbrainz_miss("".join(captured)) is None:
        for line in hidden:
            ui.tool("cyanrip", line)
    if rc != 0:  # the track in flight (if any) failed with the run
        ui.item_end("fail", f"failed: rip (rc={rc})")
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
