"""MakeMKV ripping: profile injection and selection building, the rip loop with
progress driving, junk purge, renames, promotion, the subtitle note, and the
summary header."""

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from rich.console import Console

from skive import common, ui  # ui: event lines only — no view logic here


# Port of bash constants
BASELINE = '-sel:all,+sel:(eng|dan|nolang|single),-sel:(havemulti|havecore),-sel:mvcvideo'
CONFIG_DIR = Path(__file__).parent.parent / "config"


def build_selection(keep_lang: str) -> str:
    """Build the MakeMKV selection string.

    Args:
        keep_lang: ISO 639-2/B code or "" for keep-all

    Returns:
        Selection string like "-sel:all,+sel:(eng|dan|nolang|single),-sel:(havemulti|havecore),-sel:mvcvideo"
    """
    if not keep_lang or keep_lang in ("dan", "eng"):
        # Keep exact order for the baseline case
        return BASELINE
    # Prepend the film's original language
    langs = f"{keep_lang}|dan|eng|nolang|single"
    return f"-sel:all,+sel:({langs}),-sel:(havemulti|havecore),-sel:mvcvideo"


def inject_profile(selection: str) -> Path:
    """Copy config/custom.mmcp.xml and replace BASELINE with selection.

    Args:
        selection: The selection string to inject

    Returns:
        Path to the temporary profile file
    """
    profile_path = CONFIG_DIR / "custom.mmcp.xml"
    content = profile_path.read_text(encoding="utf-8")
    modified = content.replace(BASELINE, selection)

    fd, tmp_path = tempfile.mkstemp(suffix=".mmcp.xml")
    os.write(fd, modified.encode("utf-8"))
    os.close(fd)
    return Path(tmp_path)


def rip_header(display: str, infos: list["TitleInfo"]) -> str:
    """Multi-line summary of what is being ripped: name, count, est size, languages."""
    strip = lambda s: re.sub(r"\(\d+\)", "", s)
    audio = sorted({strip(l) for t in infos for l in t.audio.split(",") if l})
    subs = sorted({l for t in infos for l in t.subtitles.split(",") if l})
    total = sum(t.size for t in infos)
    return (f"{display}\n"
            f"  titles: {len(infos)}  est total: {hsize(total)}\n"
            f"  audio: {','.join(audio) or '?'}\n"
            f"  subs:  {','.join(subs) or 'none'}")


def status_block(what: str, drive: str, phase: str, title_x: int, title_y: int,
                 cur: str = "", total: str = "", disc: str = "", eta: str = "") -> tuple[str, str]:
    """Render lines 1 and 2 of the rip status block (the bar is line 3).

    Line 1 is what is being ripped: display title and drive. Line 2 is the
    detail: phase · title x/y · cur / total · disc · ETA. The size, disc, and
    ETA segments appear only when known (empty-string inputs), so fast scans
    and sizeless rips get a shorter line instead of invented numbers.

    Args:
        what: Display title being ripped (the header's first line).
        drive: Drive designator, e.g. "disc:0".
        phase: Current makemkvcon phase (PRGT/PRGC value), "" before the first.
        title_x: Title currently being saved (1-based; 0 before the first).
        title_y: Total titles in this rip.
        cur: Ripped bytes of the current title, human-readable, or "" if unknown.
        total: Estimated size of the current title, human-readable, or "" if unknown.
        disc: Whole-disc bytes "done/total", human-readable, or "" if unknown.
        eta: Human-readable ETA, or "" if unknown.

    Returns:
        (line1, line2) as plain strings.
    """
    line1 = f"{what} — {drive}"
    bits = [phase or "starting", f"title {title_x}/{title_y}"]
    if cur or total:
        bits.append(f"{cur} / {total}")
    if disc:
        bits.append(f"disc {disc}")
    if eta:
        bits.append(f"ETA {eta}")
    return line1, " · ".join(bits)


def _fmt_eta(seconds: float) -> str:
    """Format a duration as m:ss, or h:mm:ss once it passes an hour."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _run_with_progress(cmd: list[str], desc: str, total_titles: int = 1,
                       sizes: list[int] | None = None, header: str | None = None,
                       log=None, what: str | None = None,
                       title_offset: int = 0, device: str | None = None,
                       disc_total: int | None = None, disc_offset: int = 0,
                       on_title_start=None, names: list[str] | None = None) -> int:
    """Run makemkvcon with --progress=-same and drive the run view (ADR 0012:
    absorption — the parsing stays, only the rendering moved into the view).

    With a TTY run view live: no rich objects here at all — the view's
    status panel carries line 1 (what + drive) and line 2 (status_block:
    phase · title x/y · current/total size · disc total · ETA) and its
    progress line the bar's percent/ETA. Without one (piped, or tests):
    tagged/plain forms — PRGC/PRGT transitions and per-title "Saving to MKV
    file" become one info event each, plus a plain ``makemkv  NN% · ETA``
    progress line at the transcode cadence (60 s quiet, every line at -vv);
    tool lines are ``makemkv  {text}`` on stderr. The static header prints
    only in the fallback (under a view the status line 1 carries the what).

    Parses robot output: PRGV (values), PRGT (phase name), PRGC (sub-task),
    MSG (messages). sizes (estimated bytes per title, in rip order) drive
    the size figures: ripped ≈ estimated size × progress fraction; the ETA
    is a simple elapsed-rate projection from bytes done/bytes total. Every
    output line lands verbatim in the run's raw log (log.raw). Console
    verbosity (common.VERBOSITY) gates only the printing: MSG lines show at
    -v, raw/unknown lines at -vv, and anything the level hid is replayed
    (makemkv-prefixed) when makemkvcon fails (story 7). One info summary
    line records the outcome.

    Args:
        what: Display title for status line 1; None falls back to the
            header's first line, then to desc.
        title_offset: Titles already saved in this session before this call,
            so per-title calls show the true title x/y.
        device: The physical device to retain in the status footer.
        disc_total: Total estimated bytes across the whole selected rip.
        disc_offset: Estimated bytes completed before this call.
        on_title_start: Called with the MakeMKV title number when saving starts.
        names: Footer name per rip position (1-based position - 1); when the
            title at that position starts saving it replaces what.
    """
    cmd = cmd[:1] + ["--progress=-same"] + cmd[1:]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    est_total = sum(sizes) if sizes else 0
    whole_total = disc_total if disc_total is not None else est_total
    verbose = common.VERBOSITY
    hidden: list[str] = []  # console-suppressed lines, replayed on failure
    view = ui.view()
    live_view = view is not None and view.tty
    console = Console(file=sys.stderr)
    if header and not live_view:
        console.print(header, highlight=False)
    what = what or (header.split("\n", 1)[0] if header else desc)
    drive = device or next((a for a in cmd if a.startswith("disc:")), "?")
    phase = ""
    cur_title = 0
    cur_size = sizes[0] if sizes else 0
    cur_done = 0
    tot_done = disc_offset
    started = time.monotonic()
    title_t0 = started  # the current title's start: the call's, then each next title's save
    last_prog = started  # fallback progress-line throttle (transcode cadence)

    def tool_out(text: str) -> None:
        """A tool line: view log panel when one is set, ``makemkv  {text}``
        on stderr otherwise."""
        ui.tool("makemkv", text)

    def push_status(eta: str = "") -> None:
        """Feed the status panel: line 1 what + drive, line 2 status_block."""
        l1, l2 = status_block(
            what=what, drive=drive, phase=phase, title_x=cur_title + title_offset,
            title_y=total_titles,
            cur=hsize(cur_done) if est_total else "",
            total=hsize(cur_size) if est_total else "",
            disc=f"{hsize(tot_done)}/{hsize(whole_total)}" if whole_total else "",
            eta=eta)
        if whole_total:
            disc_label = f"disc {hsize(tot_done)}/{hsize(whole_total)}"
            l2 = l2.replace(f" · {disc_label}", "")
            view.bg("disc", f"{hsize(tot_done)}/{hsize(whole_total)}")
        view.status(l1, l2)

    try:
        for line in proc.stdout:
            line = line.rstrip()
            if log is not None:
                log.raw("makemkvcon", line)
            if line.startswith("PRGV:"):
                parts = line[5:].split(",")
                if len(parts) == 3 and all(p.isdigit() for p in parts):
                    tot = int(parts[2]) or 65536
                    # Clamp below total: makemkvcon hits 100% on quick sub-phases.
                    done = min(int(parts[1]), tot - 1)
                    pct = 100.0 * done / tot
                    eta = ""
                    if est_total:
                        # ponytail: byte counts are est size × PRGV fraction, not real
                        # file sizes; stat the staged .mkv if precision ever matters.
                        cur_size = sizes[cur_title - 1] if 0 < cur_title <= len(sizes) else 0
                        cur_done = int(cur_size * int(parts[0]) / tot)
                        tot_done = disc_offset + int(est_total * int(parts[1]) / tot)
                        if tot_done:
                            elapsed = time.monotonic() - started
                            eta = _fmt_eta(elapsed * (whole_total - tot_done) / tot_done)
                    if live_view:
                        push_status(eta)
                        view.progress(round(pct, 1), eta or None)
                    else:
                        now = time.monotonic()
                        if verbose >= 2 or now - last_prog >= 60:
                            tool_out(f"{pct:.0f}%" + (f" · {eta}" if eta else ""))
                            last_prog = now
            elif line.startswith("PRGC:"):
                # New sub-task; "Saving to MKV file" marks the start of each title
                val = _qval(line)
                if "Saving to MKV file" in val:
                    if cur_title:
                        # the next title started; this one's outcome is not reported
                        now = time.monotonic()
                        if on_title_start is not None and live_view:
                            ui.info(f"title {cur_title + title_offset}/{total_titles}: saved",
                                    phase="rip", elapsed=ui._hms(int(now - title_t0)))
                        ui.unindent()
                        title_t0 = now
                    cur_title += 1
                    if names and cur_title + title_offset <= len(names):
                        what = names[cur_title + title_offset - 1]
                    if on_title_start is not None:
                        on_title_start(cur_title + title_offset)
                phase = val.strip()
                if live_view:
                    push_status()
                else:
                    ui.info(f"{desc}: {phase} · title {cur_title + title_offset}/{total_titles}")
            elif line.startswith("PRGT:"):
                phase = _qval(line).strip()
                if live_view:
                    push_status()
                else:
                    ui.info(f"{desc}: {phase} · title {cur_title + title_offset}/{total_titles}")
            elif line.startswith("MSG:"):
                if verbose >= 1:
                    tool_out(_qval(line))
                else:
                    hidden.append(line)
            elif line:
                # Pass through anything else makemkvcon prints (drive/errors/etc.)
                if verbose >= 2:
                    tool_out(line)
                else:
                    hidden.append(line)
        rc = proc.wait()
    except KeyboardInterrupt:
        # CONTEXT.md Interrupt: stop the child — SIGINT, grace window, then
        # kill — and pass the interrupt up for the partial purge and the 130
        # exit. No summary line: the interrupt summary owns the console.
        common.kill_child_after_grace(proc)
        raise
    if rc != 0:
        # Story 7: the captured lines the console never saw are replayed so
        # a crash still tells the operator what the tool said.
        for line in hidden:
            tool_out(line)
    outcome = "done" if rc == 0 else f"failed (rc={rc})"
    summary = f"{desc}: {outcome} · title {cur_title + title_offset}/{total_titles}"
    if whole_total:
        summary += f" · disc {hsize(tot_done)}/{hsize(whole_total)}"
    took = ui._hms(int(time.monotonic() - title_t0))
    kind = "ok" if rc == 0 else "fail"
    phase_line = on_title_start is not None and cur_title and live_view
    if phase_line:  # TTY: the summary is the last title's timed rip phase line, inside its frame
        (ui.ok if rc == 0 else ui.fail)(summary, phase="rip", elapsed=took)
    if on_title_start is not None:  # close the last title's item frame
        ui.item_end(kind, "done" if rc == 0 else f"failed: rip (rc={rc})", took)
    if not phase_line:
        ui.info(summary)
    return rc

def rip_titles(titles: list[int] | None, profile_path: Path, minlength: int, stage_dir: Path, run=subprocess.run, disc: int = 0, total: int = 0, infos: list["TitleInfo"] | None = None, header: str | None = None, log=None, device: str | None = None, on_title_start=None, names: list[str] | None = None, outcomes: dict[int, int] | None = None) -> int:
    """Rip titles with makemkvcon, collecting the worst return code.

    Args:
        titles: None for "all", or list of title numbers
        profile_path: Path to MakeMKV profile XML
        minlength: Minimum title length in seconds
        stage_dir: Staging directory
        run: subprocess.run callable for monkeypatching
        log: RipLog for the run's raw tool log (None in tests)
        device: Physical device for the live status footer.
        on_title_start: Callback receiving each MakeMKV title number at start.
        names: Footer name per rip position (see _run_with_progress).
        outcomes: Filled with title number -> rc on a selected-title rip
            (one makemkvcon call per title); an all-titles rip has one rc and
            records nothing.

    Returns:
        Worst (highest) return code from all rip attempts
    """
    worst_rc = 0
    by_num = {t.number: t for t in infos or []}
    if titles is None:
        # Rip all in one call
        cmd = ["makemkvcon", "mkv", "-r", "--profile", str(profile_path), f"--minlength={minlength}", f"disc:{disc}", "all", str(stage_dir) + "/"]
        sizes = [t.size for t in sorted(infos, key=lambda t: t.number)] if infos else None
        rc = _run_with_progress(
            cmd, "all titles", total_titles=total or 1, sizes=sizes, header=header,
            log=log, device=device, on_title_start=on_title_start, names=names) if run is subprocess.run else run(cmd).returncode
        if rc > worst_rc:
            worst_rc = rc
    else:
        # Seed line 1 and the title position once: every call's block keeps
        # the header's display title and shows true x/y across the selection,
        # while the static header still prints only above the first Live region.
        what = header.split("\n", 1)[0] if header else None
        total_titles = len(titles)
        selection_sizes = [by_num[t].size for t in titles if t in by_num]
        selection_total = sum(selection_sizes)
        completed_size = 0
        for i, t in enumerate(titles, 1):
            cmd = ["makemkvcon", "mkv", "-r", "--profile", str(profile_path), f"--minlength={minlength}", f"disc:{disc}", str(t), str(stage_dir) + "/"]
            desc = f"title {t} ({i}/{total_titles})"
            sizes = [by_num[t].size] if t in by_num else None
            callback = (lambda _position, title=t: on_title_start(title)) if on_title_start else None
            rc = _run_with_progress(
                cmd, desc, total_titles=total_titles, sizes=sizes,
                header=header if i == 1 else None, log=log, what=what,
                title_offset=i - 1, device=device, disc_total=selection_total,
                disc_offset=completed_size, on_title_start=callback, names=names
            ) if run is subprocess.run else run(cmd).returncode
            if outcomes is not None:
                outcomes[t] = rc
            completed_size += by_num[t].size if t in by_num else 0
            if rc > worst_rc:
                worst_rc = rc
    return worst_rc


def promote_staged(stage_dir: Path) -> None:
    """Move finished .mkv files from the d{disc} rip subdir up into the title
    folder (CONTEXT.md: Staging). A name collision leaves the file in d{disc}/
    with a note — never clobbered; the junk purge sweeps it on the same
    drive's next run (another drive's junk rests in its own d{disc} — the
    show-scoped purge covers the title tree's own dirs only).
    """
    parent = stage_dir.parent
    for mkv in stage_dir.glob("*.mkv"):
        target = parent / mkv.name
        if target.exists():
            ui.warn(f"{target.name} already exists — left in {stage_dir.name}/")
            continue
        mkv.rename(target)


def purge_junk(stage_dirs: list[Path], quiet: bool = False) -> None:
    """Delete junk-labeled .mkv files from staging dirs.

    Junk pattern: ^[A-Z][0-9]+_t[0-9]+$

    Args:
        stage_dirs: List of directories to purge
        quiet: Skip the per-file stderr line (the interrupt exit purges
            silently so its one summary line stays the only output)
    """
    # shortcut: a year-less TMDB title shaped like a bare code ("B2" renames to "B2_t01.mkv") full-matches this pattern, so a completed file would be purged like a partial; if TMDB ever returns year-less titles or bare codes, anchor junk matching to the current run instead of the name pattern
    junk_pat = re.compile(r'^[A-Z][0-9]+_t\d+$')
    for stage_dir in stage_dirs:
        if not stage_dir.is_dir():
            continue
        for mkv in stage_dir.glob("*.mkv"):
            base = mkv.stem
            if junk_pat.match(base):
                if not quiet:
                    ui.info(f"removing stale staged file from an earlier session: {base}.mkv")
                mkv.unlink()


def looks_real(name: str) -> bool:
    """Heuristic: does a name look like a real title?

    Args:
        name: File name without extension

    Returns:
        True if it looks like a real title (len>3, has space, not ^[A-Z][0-9]+, not short all-caps)
    """
    if len(name) <= 3:
        return False
    if re.match(r'^[A-Z][0-9]+$', name):
        return False
    if " " not in name:
        return False
    if re.match(r'^[^a-z]*$', name) and len(name) <= 16:
        return False
    return True


def rename_staged(display: str, stage_dir: Path, suffix: str = "t", part: int | None = None,
                  preexisting: set[Path] | None = None) -> None:
    """Rename junk-labeled .mkv files.

    Titles become <display>_tNN[.pN].mkv; extras become <display> - Extra NN[.pN].mkv.
    Files already matching display or looking real are untouched.
    Rolls numbers forward past existing files (never clobber).

    Args:
        display: Display name (Title (Year), / already replaced)
        stage_dir: Staging directory
        suffix: "t" for titles, "e" for extras
        part: Part number for .pN suffix
        preexisting: Files present before the rip started — never renamed.
            Guards against --extras renaming leftovers from earlier rips.
    """
    for mkv in stage_dir.glob("*.mkv"):
        if preexisting and mkv in preexisting:
            continue
        base = mkv.stem
        # Extract _tNN suffix
        m = re.search(r'_t(\d+)$', base)
        if not m:
            continue
        title_num = int(m.group(1))
        cur = base[:m.start()]

        # Skip if already this display; extras (-E) rename everything else —
        # bonus discs label files from the disc name ("... Bonus Disc_t00"),
        # which looks real but must still become "<display> - Extra NN".
        if cur == display or (suffix != "e" and looks_real(cur)):
            continue

        # Build target name
        part_suffix = f".p{part}" if part is not None else ""
        if suffix == "e":
            stem, sep = f"{display} - Extra", " "
        else:
            stem, sep = f"{display}_{suffix}", ""
        m_val = title_num
        while (stage_dir / f"{stem}{sep}{m_val:02d}{part_suffix}.mkv").exists():
            m_val += 1

        target = stage_dir / f"{stem}{sep}{m_val:02d}{part_suffix}.mkv"
        mkv.rename(target)
        ui.info(f"{cur} → {target.stem}")


def subtitle_note(stage_dir: Path, run=subprocess.run) -> str:
    """Run ffprobe on staged .mkvs, collect subtitle languages.

    Args:
        stage_dir: Staging directory
        run: subprocess.run callable for monkeypatching

    Returns:
        Note text: "subs=dan langs=...", "subs=eng langs=... (no dan)", or "subs=... (no dan/eng)"
    """
    langs = set()
    for mkv in stage_dir.glob("*.mkv"):
        cmd = ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "stream_tags=language", "-of", "csv=p=0", str(mkv)]
        result = run(cmd, capture_output=True, text=True)
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                line = "und"
            langs.add(line)

    if not langs:
        return "subs=none"

    langs_list = sorted(langs)
    langs_str = ",".join(langs_list)

    if "dan" in langs_list:
        return f"subs=dan langs={langs_str}"
    elif "eng" in langs_list:
        return f"subs=eng langs={langs_str} (no dan)"
    else:
        return f"subs={langs_str} (no dan/eng)"


def hsize(bytes_val: int) -> str:
    """Format bytes to human readable (4.0 GiB)."""
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    val = float(bytes_val)
    unit_idx = 0
    while val >= 1024 and unit_idx < len(units) - 1:
        val /= 1024
        unit_idx += 1
    return f"{val:.1f} {units[unit_idx]}"


def _qval(line: str) -> str:
    """The quoted value on a makemkvcon -r info line (awk: strip to first/last quote)."""
    a = line.find('"')
    b = line.rfind('"')
    if a < 0 or b <= a:
        return ""
    return line[a + 1:b]
