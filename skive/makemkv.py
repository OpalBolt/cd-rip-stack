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

from skive import common


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
                       title_offset: int = 0) -> int:
    """Run makemkvcon with --progress=-same and drive the status block.

    The block is a rich Live region of three lines under the static disc
    summary header: line 1 what is being ripped (display title + drive),
    line 2 the detail (phase · title x/y · current/total size · disc total ·
    ETA), line 3 the progress bar. The Live region clears on exit and one
    static summary line records the outcome, so no stale bar is left on
    screen.

    Parses robot output: PRGV (values), PRGT (phase name), PRGC (sub-task),
    MSG (messages). sizes (estimated bytes per title, in rip order) drives
    the live GiB figures: ripped ≈ estimated size × progress fraction; the
    ETA is a simple elapsed-rate projection from bytes done/bytes total.
    Every output line lands verbatim in the run's raw log (log.raw). Console
    verbosity (common.VERBOSITY) gates only the printing: MSG lines show at
    -v, raw/unknown lines at -vv, and anything the level hid is replayed
    verbatim to stderr when makemkvcon fails (story 7). At level 0 the block
    is the only thing moving on screen; at level ≥1 tool lines print through
    the same console inside the live context, landing above the block.

    Args:
        what: Display title for line 1; None falls back to the header's first
            line, then to desc. Per-title rips seed it once in rip_titles so
            every Live region names the rip without reprinting the header.
        title_offset: Titles already saved in this session before this call,
            so per-title calls show the true title x/y. The bar's sizes stay
            indexed within the call (cur_title still counts from 0).
    """
    from rich.console import Group
    from rich.live import Live
    from rich.progress import (
        BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn,
    )
    from rich.text import Text
    cmd = cmd[:1] + ["--progress=-same"] + cmd[1:]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    est_total = sum(sizes) if sizes else 0
    verbose = common.VERBOSITY
    hidden: list[str] = []  # console-suppressed lines, replayed on failure
    console = Console(file=sys.stderr)
    if header:
        console.print(header, highlight=False)  # static, above the Live region
    what = what or (header.split("\n", 1)[0] if header else desc)
    drive = next((a for a in cmd if a.startswith("disc:")), "?")
    phase = ""
    cur_title = 0
    cur_size = sizes[0] if sizes else 0
    cur_done = tot_done = 0
    started = time.monotonic()

    l1, l2 = Text(style="bold"), Text()
    progress = Progress(
        # desc stays static on the bar line: line 1/2 now carry what + phase.
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TextColumn("{task.percentage:>5.1f}%"),
        TimeElapsedColumn(),
        TextColumn("ETA"),
        TimeRemainingColumn(),
        console=console,
    )
    task = progress.add_task(desc, total=65536)

    def redraw(eta: str = "") -> None:
        """Refresh the two text lines of the status block."""
        l1.plain, l2.plain = status_block(
            what=what, drive=drive, phase=phase, title_x=cur_title + title_offset,
            title_y=total_titles,
            cur=hsize(cur_done) if est_total else "",
            total=hsize(cur_size) if est_total else "",
            disc=f"{hsize(tot_done)}/{hsize(est_total)}" if est_total else "",
            eta=eta)

    live = Live(Group(l1, l2, progress), console=console, transient=True)

    def live_refresh() -> None:
        """Re-render the block after a phase-only change (no bar update)."""
        live.refresh()

    with live:
        for line in proc.stdout:
            line = line.rstrip()
            if log is not None:
                log.raw("makemkvcon", line)
            if line.startswith("PRGV:"):
                parts = line[5:].split(",")
                if len(parts) == 3 and all(p.isdigit() for p in parts):
                    tot = int(parts[2]) or 65536
                    eta = ""
                    if est_total:
                        # ponytail: byte counts are est size × PRGV fraction, not real
                        # file sizes; stat the staged .mkv if precision ever matters.
                        cur_size = sizes[cur_title - 1] if 0 < cur_title <= len(sizes) else 0
                        cur_done = int(cur_size * int(parts[0]) / tot)
                        tot_done = int(est_total * int(parts[1]) / tot)
                        if tot_done:
                            elapsed = time.monotonic() - started
                            eta = _fmt_eta(elapsed * (est_total - tot_done) / tot_done)
                    redraw(eta)
                    # Clamp below total: rich freezes elapsed/ETA once a task
                    # finishes, and makemkvcon hits 100% on quick sub-phases.
                    progress.update(task, completed=min(int(parts[1]), tot - 1), total=tot)
            elif line.startswith("PRGC:"):
                # New sub-task; "Saving to MKV file" marks the start of each title
                val = _qval(line)
                if "Saving to MKV file" in val:
                    cur_title += 1
                phase = val.strip()
                redraw()
                live_refresh()
            elif line.startswith("PRGT:"):
                phase = _qval(line).strip()
                redraw()
                live_refresh()
            elif line.startswith("MSG:"):
                if verbose >= 1:
                    console.print(_qval(line), highlight=False)
                else:
                    hidden.append(line)
            elif line:
                # Pass through anything else makemkvcon prints (drive/errors/etc.)
                if verbose >= 2:
                    console.print(line, highlight=False)
                else:
                    hidden.append(line)
    rc = proc.wait()
    if rc != 0:
        # Story 7: the captured lines the console never saw are replayed
        # verbatim so a crash still tells the operator what the tool said.
        for line in hidden:
            print(line, file=sys.stderr)
    outcome = "done" if rc == 0 else f"failed (rc={rc})"
    summary = f"{desc}: {outcome} · title {cur_title + title_offset}/{total_titles}"
    if est_total:
        summary += f" · disc {hsize(tot_done)}/{hsize(est_total)}"
    console.print(summary, highlight=False)
    return rc


def rip_titles(titles: list[int] | None, profile_path: Path, minlength: int, stage_dir: Path, run=subprocess.run, disc: int = 0, total: int = 0, infos: list["TitleInfo"] | None = None, header: str | None = None, log=None) -> int:
    """Rip titles with makemkvcon, collecting the worst return code.

    Args:
        titles: None for "all", or list of title numbers
        profile_path: Path to MakeMKV profile XML
        minlength: Minimum title length in seconds
        stage_dir: Staging directory
        run: subprocess.run callable for monkeypatching
        log: RipLog for the run's raw tool log (None in tests)

    Returns:
        Worst (highest) return code from all rip attempts
    """
    worst_rc = 0
    by_num = {t.number: t for t in infos or []}
    if titles is None:
        # Rip all in one call
        cmd = ["makemkvcon", "mkv", "-r", "--profile", str(profile_path), f"--minlength={minlength}", f"disc:{disc}", "all", str(stage_dir) + "/"]
        sizes = [t.size for t in sorted(infos, key=lambda t: t.number)] if infos else None
        rc = _run_with_progress(cmd, "all titles", total_titles=total or 1, sizes=sizes, header=header, log=log) if run is subprocess.run else run(cmd).returncode
        if rc > worst_rc:
            worst_rc = rc
    else:
        # Seed line 1 and the title position once: every call's block keeps
        # the header's display title and shows true x/y across the selection,
        # while the static header still prints only above the first Live region.
        what = header.split("\n", 1)[0] if header else None
        total_titles = len(titles)
        for i, t in enumerate(titles, 1):
            cmd = ["makemkvcon", "mkv", "-r", "--profile", str(profile_path), f"--minlength={minlength}", f"disc:{disc}", str(t), str(stage_dir) + "/"]
            desc = f"title {t} ({i}/{total_titles})"
            sizes = [by_num[t].size] if t in by_num else None
            rc = _run_with_progress(cmd, desc, total_titles=total_titles, sizes=sizes,
                                    header=header if i == 1 else None, log=log,
                                    what=what, title_offset=i - 1) if run is subprocess.run else run(cmd).returncode
            if rc > worst_rc:
                worst_rc = rc
    return worst_rc


def promote_staged(stage_dir: Path) -> None:
    """Move finished .mkv files from a per-disc subdir up to the parent staging dir.

    No-op for the parent dir itself. Never clobbers.
    """
    parent = stage_dir.parent
    for mkv in stage_dir.glob("*.mkv"):
        target = parent / mkv.name
        if target.exists():
            print(f"skive: {target.name} already exists — left in {stage_dir.name}/", file=sys.stderr)
            continue
        mkv.rename(target)


def purge_junk(stage_dirs: list[Path]) -> None:
    """Delete junk-labeled .mkv files from staging dirs.

    Junk pattern: ^[A-Z][0-9]+_t[0-9]+$

    Args:
        stage_dirs: List of directories to purge
    """
    junk_pat = re.compile(r'^[A-Z][0-9]+_t\d+$')
    for stage_dir in stage_dirs:
        if not stage_dir.is_dir():
            continue
        for mkv in stage_dir.glob("*.mkv"):
            base = mkv.stem
            if junk_pat.match(base):
                print(f"skive: removing stale staged file from an earlier session: {base}.mkv", file=sys.stderr)
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
        print(f"skive: {cur} → {target.stem}", file=sys.stderr)


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
