"""MakeMKV ripping: profile injection and selection building, the rip loop with
progress driving, junk purge, renames, promotion, the subtitle note, and the
summary header."""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console


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


def _run_with_progress(cmd: list[str], desc: str, total_titles: int = 1,
                       sizes: list[int] | None = None, header: str | None = None,
                       log=None) -> int:
    """Run makemkvcon with --progress=-same and drive a rich progress bar.

    Parses robot output: PRGV (values), PRGT (phase name), MSG (messages).
    sizes (estimated bytes per title, in rip order) drives a live GiB column:
    ripped ≈ estimated size × progress fraction.
    Every output line lands verbatim in the run's raw log (log.raw).
    """
    from rich.progress import (
        BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn,
    )
    cmd = cmd[:1] + ["--progress=-same"] + cmd[1:]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    est_total = sum(sizes) if sizes else 0
    with Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TextColumn("{task.percentage:>5.1f}%"),
        TextColumn("{task.fields[gb]}"),
        TimeElapsedColumn(),
        TextColumn("ETA"),
        TimeRemainingColumn(),
        console=Console(file=sys.stderr),
    ) as progress:
        if header:
            progress.console.print(header, highlight=False)
        task = progress.add_task(desc, total=65536, gb="")
        cur_title = 0
        for line in proc.stdout:
            line = line.rstrip()
            if log is not None:
                log.raw("makemkvcon", line)
            if line.startswith("PRGV:"):
                parts = line[5:].split(",")
                if len(parts) == 3 and all(p.isdigit() for p in parts):
                    tot = int(parts[2]) or 65536
                    gb = ""
                    if est_total:
                        # ponytail: byte counts are est size × PRGV fraction, not real
                        # file sizes; stat the staged .mkv if precision ever matters.
                        cur_size = sizes[cur_title - 1] if 0 < cur_title <= len(sizes) else 0
                        cur_done = int(cur_size * int(parts[0]) / tot)
                        tot_done = int(est_total * int(parts[1]) / tot)
                        gb = f"{hsize(cur_done)}/{hsize(cur_size)} · disc {hsize(tot_done)}/{hsize(est_total)}"
                    # Clamp below total: rich freezes elapsed/ETA once a task
                    # finishes, and makemkvcon hits 100% on quick sub-phases.
                    progress.update(task, completed=min(int(parts[1]), tot - 1), total=tot, gb=gb)
            elif line.startswith("PRGC:"):
                # New sub-task; "Saving to MKV file" marks the start of each title
                val = _qval(line)
                if "Saving to MKV file" in val:
                    cur_title += 1
                    val = f"title {cur_title}/{total_titles} — {val}"
                progress.update(task, description=f"{desc} — {val}")
            elif line.startswith("PRGT:"):
                progress.update(task, description=f"{desc} — {_qval(line)}")
            elif line.startswith("MSG:"):
                progress.console.print(_qval(line), highlight=False)
            elif line:
                # Pass through anything else makemkvcon prints (drive/errors/etc.)
                progress.console.print(line, highlight=False)
    return proc.wait()


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
        for i, t in enumerate(titles, 1):
            cmd = ["makemkvcon", "mkv", "-r", "--profile", str(profile_path), f"--minlength={minlength}", f"disc:{disc}", str(t), str(stage_dir) + "/"]
            desc = f"title {t} ({i}/{len(titles)})"
            sizes = [by_num[t].size] if t in by_num else None
            rc = _run_with_progress(cmd, desc, sizes=sizes, header=header if i == 1 else None, log=log) if run is subprocess.run else run(cmd).returncode
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
