"""MakeMKV interaction: media detection, profile injection, rip, rename, subtitle note."""

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Port of bash constants
BASELINE = '-sel:all,+sel:(eng|dan|nolang|single),-sel:(havemulti|havecore),-sel:mvcvideo'


def iso2b(code: str) -> str:
    """ISO 639-1 → ISO 639-2/B for MakeMKV."""
    mapping = {
        "en": "eng", "de": "deu", "da": "dan", "fr": "fra",
        "es": "spa", "it": "ita", "sv": "swe", "no": "nor",
        "fi": "fin", "nl": "nld",
    }
    return mapping.get(code, code)


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


def drive_media(run=subprocess.run) -> str:
    """Get disc:0's media type token.

    Honors MAKEMKV_INFO env var to replay a captured dump.

    Args:
        run: subprocess.run callable for monkeypatching

    Returns:
        Media token (first word), or "" if no disc
    """
    override = os.environ.get("MAKEMKV_INFO")
    if override:
        # Replay captured dump via the command in override
        result = run(override, shell=True, capture_output=True, text=True)
        out = result.stdout
    else:
        result = run(["makemkvcon", "-r", "info", "disc:0"], capture_output=True, text=True)
        out = result.stdout

    # Parse DRV:0,2,...,"<media> ..." line
    for line in out.splitlines():
        m = re.match(r'^DRV:0,2,\d+,\d+,"([^"]*)"', line)
        if m:
            token = m.group(1)
            return token.split()[0] if token else ""
    return ""


def is_recordable(media: str) -> bool:
    """Check if media token is a recordable disc.

    Matches the bash case statement patterns:
    - DVD+R*, DVD+RW*, DVD-RW*, DVD-RAM*, BD-RE*, CD-RW*
    - Exact: DVD-R, CD-R, BD-R
    - Trailing variants: "DVD-R ", "DVD-R-", "BD-R "

    Args:
        media: Media token from drive_media()

    Returns:
        True if the media is recordable (DVD±R/RW/BD-R/CD-R), False otherwise
    """
    if not media:
        return False

    # Pressed or unknown — fail OPEN (bash: returns 1)
    if media in ("DVD-ROM", "BD-ROM", "CD-ROM", "HD-DVD", "CD", "DVD", "BD", ""):
        return False

    # Recordable families — bash patterns
    # DVD+R*, DVD+RW*, DVD-RW*, DVD-RAM*, BD-RE*, CD-RW*
    if re.match(r'^(DVD\+R|DVD\+RW|DVD-RW|DVD-RAM|BD-RE|CD-RW)', media):
        return True

    # Exact matches: DVD-R, CD-R, BD-R
    if media in ("DVD-R", "CD-R", "BD-R"):
        return True

    # Trailing variants: "DVD-R ", "DVD-R-", "BD-R "
    if re.match(r'^(DVD-R|BD-R) ', media):
        return True
    if re.match(r'^DVD-R-', media):
        return True

    return False


def inject_profile(selection: str) -> Path:
    """Copy config/custom.mmcp.xml and replace BASELINE with selection.

    Args:
        selection: The selection string to inject

    Returns:
        Path to the temporary profile file
    """
    profile_path = Path("config/custom.mmcp.xml")
    content = profile_path.read_text(encoding="utf-8")
    modified = content.replace(BASELINE, selection)

    import tempfile
    fd, tmp_path = tempfile.mkstemp(suffix=".mmcp.xml")
    os.write(fd, modified.encode("utf-8"))
    os.close(fd)
    return Path(tmp_path)


def rip_titles(titles: list[int] | None, profile_path: Path, minlength: int, stage_dir: Path, run=subprocess.run) -> int:
    """Rip titles with makemkvcon, collecting the worst return code.

    Args:
        titles: None for "all", or list of title numbers
        profile_path: Path to MakeMKV profile XML
        minlength: Minimum title length in seconds
        stage_dir: Staging directory
        run: subprocess.run callable for monkeypatching

    Returns:
        Worst (highest) return code from all rip attempts
    """
    worst_rc = 0
    if titles is None:
        # Rip all in one call
        cmd = ["makemkvcon", "mkv", "-r", "--profile", str(profile_path), f"--minlength={minlength}", "disc:0", "all", str(stage_dir) + "/"]
        rc = run(cmd).returncode
        if rc > worst_rc:
            worst_rc = rc
    else:
        for t in titles:
            cmd = ["makemkvcon", "mkv", "-r", "--profile", str(profile_path), f"--minlength={minlength}", "disc:0", str(t), str(stage_dir) + "/"]
            rc = run(cmd).returncode
            if rc > worst_rc:
                worst_rc = rc
    return worst_rc


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


def rename_staged(display: str, stage_dir: Path, suffix: str = "t", part: int | None = None) -> None:
    """Rename junk-labeled .mkv files to <display>_<suffix>NN[.pN].mkv.

    Files already matching display or looking real are untouched.
    Rolls numbers forward past existing files (never clobber).

    Args:
        display: Display name (Title (Year), / already replaced)
        stage_dir: Staging directory
        suffix: "t" for titles, "e" for extras
        part: Part number for .pN suffix
    """
    for mkv in stage_dir.glob("*.mkv"):
        base = mkv.stem
        # Extract _tNN suffix
        m = re.search(r'_t(\d+)$', base)
        if not m:
            continue
        title_num = int(m.group(1))
        cur = base[:m.start()]

        # Skip if already this display or looks real
        if cur == display or looks_real(cur):
            continue

        # Build target name
        part_suffix = f".p{part}" if part is not None else ""
        m_val = title_num
        while (stage_dir / f"{display}_{suffix}{m_val:02d}{part_suffix}.mkv").exists():
            m_val += 1

        target = stage_dir / f"{display}_{suffix}{m_val:02d}{part_suffix}.mkv"
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


def scan_disc(minlength: int, run=subprocess.run) -> list[tuple[int, int, int]]:
    """Scan disc:0 with makemkvcon info, return ordered title list.

    Honors MAKEMKV_INFO env var to replay a captured dump.
    Returns list of (title_number, seconds, size_bytes) ordered by title number.

    Args:
        minlength: Minimum title length in seconds
        run: subprocess.run callable for monkeypatching

    Returns:
        List of (title_number, seconds, size_bytes) sorted by title_number.
        A title counts only when both duration and size are present.
    """
    override = os.environ.get("MAKEMKV_INFO")
    if override:
        result = run(override, shell=True, capture_output=True, text=True)
        out = result.stdout
    else:
        cmd = ["makemkvcon", "-r", "info", f"--minlength={minlength}", "disc:0"]
        result = run(cmd, capture_output=True, text=True)
        out = result.stdout

    # Parse TINFO lines
    # Format: TINFO:<title>,9,"H:MM:SS" or "MM:SS" for duration
    #         TINFO:<title>,10,"<bytes>" for size
    titles = {}  # title -> (duration_seconds, size_bytes)
    for line in out.splitlines():
        m = re.match(r'^TINFO:(\d+),9,"([^"]*)"', line)
        if m:
            title_num = int(m.group(1))
            time_str = m.group(2)
            # Parse H:MM:SS or MM:SS
            if time_str:
                parts = time_str.split(":")
                if len(parts) == 3:
                    dur = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                else:  # len == 2
                    dur = int(parts[0]) * 60 + int(parts[1])
            else:
                dur = 0
            if title_num in titles:
                titles[title_num] = (dur, titles[title_num][1])
            else:
                titles[title_num] = (dur, 0)
        else:
            m = re.match(r'^TINFO:(\d+),10,"([^"]*)"', line)
            if m:
                title_num = int(m.group(1))
                size_str = m.group(2)
                size = int(size_str) if size_str.isdigit() else 0
                if title_num in titles:
                    titles[title_num] = (titles[title_num][0], size)
                else:
                    titles[title_num] = (0, size)

    # Filter to titles with both duration and size
    result_list = [(t, dur, sz) for t, (dur, sz) in titles.items() if dur > 0 and sz > 0]
    result_list.sort(key=lambda x: x[0])  # Sort by title number
    return result_list