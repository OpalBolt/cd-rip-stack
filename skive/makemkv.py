"""MakeMKV interaction: media detection, profile injection, rip, rename, subtitle note."""

import io
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from skive.common import MENU, parse_titles

# Port of bash constants
BASELINE = '-sel:all,+sel:(eng|dan|nolang|single),-sel:(havemulti|havecore),-sel:mvcvideo'
CONFIG_DIR = Path(__file__).parent.parent / "config"


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


def _info(minlength: int | None, run=subprocess.run, disc: int = 0) -> str:
    """One `makemkvcon -r info` run (honoring MAKEMKV_INFO); minlength=None omits the flag."""
    override = os.environ.get("MAKEMKV_INFO")
    if override:
        # Replay captured dump via the command in override
        return run(override, shell=True, capture_output=True, text=True).stdout
    cmd = ["makemkvcon", "-r", "info"]
    if minlength is not None:
        cmd.append(f"--minlength={minlength}")
    cmd.append(f"disc:{disc}")
    return run(cmd, capture_output=True, text=True).stdout


def _parse_media(out: str, disc: int = 0) -> str:
    """Media token from the DRV:N line (first word), or "" if no disc."""
    for line in out.splitlines():
        m = re.match(rf'^DRV:{disc},2,\d+,\d+,"([^"]*)"', line)
        if m:
            token = m.group(1)
            return token.split()[0] if token else ""
    return ""


def drive_media(run=subprocess.run) -> str:
    """Get disc:0's media type token.

    Honors MAKEMKV_INFO env var to replay a captured dump.

    Args:
        run: subprocess.run callable for monkeypatching

    Returns:
        Media token (first word), or "" if no disc
    """
    return _parse_media(_info(None, run))


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
    profile_path = CONFIG_DIR / "custom.mmcp.xml"
    content = profile_path.read_text(encoding="utf-8")
    modified = content.replace(BASELINE, selection)

    fd, tmp_path = tempfile.mkstemp(suffix=".mmcp.xml")
    os.write(fd, modified.encode("utf-8"))
    os.close(fd)
    return Path(tmp_path)


def _run_with_progress(cmd: list[str], desc: str) -> int:
    """Run makemkvcon with --progress=-same and drive a rich progress bar.

    Parses robot output: PRGV (values), PRGT (phase name), MSG (messages).
    """
    from rich.progress import (
        BarColumn, Progress, TextColumn, TimeElapsedColumn, TimeRemainingColumn,
    )
    cmd = cmd[:1] + ["--progress=-same"] + cmd[1:]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    with Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TextColumn("{task.percentage:>5.1f}%"),
        TimeElapsedColumn(),
        TextColumn("ETA"),
        TimeRemainingColumn(),
        console=Console(file=sys.stderr),
    ) as progress:
        task = progress.add_task(desc, total=65536)
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("PRGV:"):
                parts = line[5:].split(",")
                if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                    progress.update(task, completed=int(parts[1]), total=int(parts[2]) or 65536)
            elif line.startswith("PRGT:"):
                progress.update(task, description=f"{desc} — {_qval(line)}")
            elif line.startswith("MSG:"):
                progress.console.print(_qval(line), highlight=False)
            elif not line.startswith("PRGC:") and line:
                # Pass through anything else makemkvcon prints (drive/errors/etc.)
                progress.console.print(line, highlight=False)
    return proc.wait()


def rip_titles(titles: list[int] | None, profile_path: Path, minlength: int, stage_dir: Path, run=subprocess.run, disc: int = 0) -> int:
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
        cmd = ["makemkvcon", "mkv", "-r", "--profile", str(profile_path), f"--minlength={minlength}", f"disc:{disc}", "all", str(stage_dir) + "/"]
        rc = _run_with_progress(cmd, "all titles") if run is subprocess.run else run(cmd).returncode
        if rc > worst_rc:
            worst_rc = rc
    else:
        for i, t in enumerate(titles, 1):
            cmd = ["makemkvcon", "mkv", "-r", "--profile", str(profile_path), f"--minlength={minlength}", f"disc:{disc}", str(t), str(stage_dir) + "/"]
            desc = f"title {t} ({i}/{len(titles)})"
            rc = _run_with_progress(cmd, desc) if run is subprocess.run else run(cmd).returncode
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


def rename_staged(display: str, stage_dir: Path, suffix: str = "t", part: int | None = None) -> None:
    """Rename junk-labeled .mkv files.

    Titles become <display>_tNN[.pN].mkv; extras become <display> - Extra NN[.pN].mkv.
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


@dataclass
class TitleInfo:
    """One disc title, as shown in the interactive menu."""
    number: int
    seconds: int
    size: int
    audio: str
    subtitles: str
    scrambled: bool


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


def _dur_seconds(val: str) -> int:
    if not val:
        return 0
    parts = val.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return int(parts[0]) * 60 + int(parts[1])


def _scrambled(val: str) -> bool:
    """Non-sequential segment map (TINFO 26) flags a decoy/alternate cut."""
    if not val:
        return False
    try:
        segs = [int(s) for s in val.split(",") if s.strip()]
    except ValueError:
        return False
    return any(segs[i] < segs[i - 1] for i in range(1, len(segs)))


def _stream_keys(stypes: dict, slangs: dict) -> list[str]:
    keys = set(stypes) | set(slangs)
    return sorted((k for k in keys if k.isdigit()), key=int)


def _audio_str(stypes: dict, slangs: dict) -> str:
    order: list[str] = []
    count: dict[str, int] = {}
    for st in _stream_keys(stypes, slangs):
        if stypes.get(st) == "Audio" and slangs.get(st, ""):
            lang = slangs[st]
            if lang not in count:
                order.append(lang)
            count[lang] = count.get(lang, 0) + 1
    return ",".join(l if count[l] <= 1 else f"{l}({count[l]})" for l in order)


def _subs_str(stypes: dict, slangs: dict) -> str:
    order: list[str] = []
    for st in _stream_keys(stypes, slangs):
        if stypes.get(st) == "Subtitles" and slangs.get(st, ""):
            lang = slangs[st]
            if lang not in order:
                order.append(lang)
    return ",".join(order)


def _parse_titles(out: str) -> list[TitleInfo]:
    """Parse TINFO/SINFO lines into TitleInfo per title, ascending by number.

    A title counts only when both duration (TINFO 9) and size (TINFO 10) are present.
    """
    data = {}  # number -> per-title accumulators
    for line in out.splitlines():
        if line.startswith("TINFO:"):
            f = line.split(",")
            head = f[0].split(":")
            if len(head) < 2:
                continue
            try:
                t = int(head[1])
            except ValueError:
                continue
            rec = data.setdefault(t, {"secs": 0, "size": 0, "scrambled": False,
                                      "stypes": {}, "slangs": {}})
            code = f[1] if len(f) > 1 else ""
            val = _qval(line)
            if code == "9":
                rec["secs"] = _dur_seconds(val)
            elif code == "11":
                rec["size"] = int(val) if val.isdigit() else 0
            elif code == "26":
                rec["scrambled"] = _scrambled(val)
        elif line.startswith("SINFO:"):
            f = line.split(",")
            head = f[0].split(":")
            if len(head) < 2:
                continue
            try:
                t = int(head[1])
            except ValueError:
                continue
            rec = data.setdefault(t, {"secs": 0, "size": 0, "scrambled": False,
                                      "stypes": {}, "slangs": {}})
            stream = f[1] if len(f) > 1 else ""
            code = f[2] if len(f) > 2 else ""
            val = _qval(line)
            if code == "1":
                rec["stypes"][stream] = val
            elif code == "3":
                rec["slangs"][stream] = val

    result = []
    for t in sorted(data):
        rec = data[t]
        if rec["secs"] <= 0 or rec["size"] <= 0:
            continue
        result.append(TitleInfo(
            number=t,
            seconds=rec["secs"],
            size=rec["size"],
            audio=_audio_str(rec["stypes"], rec["slangs"]),
            subtitles=_subs_str(rec["stypes"], rec["slangs"]),
            scrambled=rec["scrambled"],
        ))
    return result


def scan_disc(minlength: int, run=subprocess.run, disc: int = 0) -> tuple[str, list[TitleInfo]]:
    """One `makemkvcon info` run: media token + parsed titles."""
    out = _info(minlength, run, disc)
    media, titles = _parse_media(out, disc), _parse_titles(out)
    if media and not titles:
        # A disc with zero parseable titles means the info scan failed (e.g.
        # expired-eval makemkvcon, read errors). Keep the raw output for diagnosis.
        dump = Path(tempfile.gettempdir()) / "skive-scan-failed.txt"
        dump.write_text(out)
        print(f"skive: disc scan returned no titles ({media}) — raw output saved to {dump}", file=sys.stderr)
        print("  refusing to rip blind (this is how scrambled duplicates get ripped).", file=sys.stderr)
        sys.exit(1)
    return media, titles


def _getch() -> str:
    """Read one key from stdin (raw termios); arrows -> UP/DOWN."""
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        b = sys.stdin.buffer.read(1)
        if b == b"\x1b":
            seq = sys.stdin.buffer.read(2)
            if seq == b"[A":
                return "UP"
            if seq == b"[B":
                return "DOWN"
            return b.decode(errors="replace")
        return b.decode(errors="replace")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _menu_state(n: int, selected: list[bool], cursor: int, key: str) -> tuple[list[bool], int, bool, bool]:
    """Pure menu transition. Returns (selected, cursor, done, abort)."""
    done = abort = False
    if key == " ":
        if cursor == 0:
            new = not all(selected)
            selected = [new] * n
        else:
            selected[cursor - 1] = not selected[cursor - 1]
    elif key in ("UP", "k"):
        cursor = (cursor - 1) % (n + 1)
    elif key in ("DOWN", "j"):
        cursor = (cursor + 1) % (n + 1)
    elif key in ("\r", "\n"):
        done = True
    elif key in ("q", "Q"):
        abort = True
    return selected, cursor, done, abort


def _fmt_dur(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _render(titles: list[TitleInfo], selected: list[bool], cursor: int) -> list[str]:
    """Render the menu (header + title table + hint) to plain lines."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=110)
    console.print(("> " if cursor == 0 else "  ") + "Rip all titles")
    table = Table(show_header=False, box=None, pad_edge=False, padding=(0, 1))
    table.add_column("cur")
    table.add_column("sel")
    table.add_column("title", justify="right")
    table.add_column("dur", justify="right")
    table.add_column("size", justify="right")
    table.add_column("flag")
    table.add_column("audio")
    table.add_column("subs")
    for i, t in enumerate(titles):
        table.add_row(
            ">" if cursor == i + 1 else " ",
            Text("[x]" if selected[i] else "[ ]"),
            f"title {t.number}",
            _fmt_dur(t.seconds),
            hsize(t.size),
            "S" if t.scrambled else "",
            f"A:{t.audio}",
            f"subs:{t.subtitles}",
        )
    console.print(table)
    console.print("space=toggle  Enter=rip  ↑/↓ or j/k=move  q=quit")
    return buf.getvalue().rstrip("\n").split("\n")


def title_menu(titles: list[TitleInfo], getch=_getch, out=sys.stderr) -> list[int] | None:
    """Interactive checkbox menu, longest-first.

    Returns None when every title is selected (rip 'all' in one call), else the
    sorted selected title numbers. q aborts with SystemExit(1).
    """
    if not titles:
        return None
    disp = sorted(titles, key=lambda t: -t.seconds)
    n = len(disp)
    selected = [True] * n
    cursor = 0
    while True:
        out.write("\x1b[2J\x1b[H")  # clear + home
        for line in _render(disp, selected, cursor):
            print(line, file=out)
        key = getch()
        selected, cursor, done, abort = _menu_state(n, selected, cursor, key)
        if abort:
            print("skive: quit — nothing ripped.", file=out)
            sys.exit(1)
        if done:
            break
    if all(selected):
        return None
    return sorted(disp[i].number for i in range(n) if selected[i])


def _dedupe(titles: list[TitleInfo]) -> list[TitleInfo]:
    """Drop playlist-obfuscation duplicates: titles with identical duration.

    Keeps the richest copy per duration (most subtitle languages, then
    largest size, then lowest title number).
    # ponytail: exact-seconds key; use a tolerance if decoys ever differ by 1s.
    """
    best: dict[int, TitleInfo] = {}
    for t in sorted(titles, key=lambda t: (-len(t.subtitles.split(",")) if t.subtitles else 0,
                                           -t.size, t.number)):
        best.setdefault(t.seconds, t)
    return sorted(best.values(), key=lambda t: t.number)


def resolve_titles(titles_arg, titles: list[TitleInfo], media: str = "") -> list[int] | None:
    """Resolve the -T flag to a rip list (None = rip all), using the titles
    already scanned by the caller.

    MENU (bare -T) opens the interactive menu; a SPEC string is parsed; None
    means rip all — except on Blu-ray, where scrambled (decoy) titles are
    dropped by default.
    """
    if titles_arg is MENU:
        return title_menu(titles)
    if titles_arg is not None:
        return parse_titles(titles_arg)
    if media.startswith("BD"):
        keep = _dedupe([t for t in titles if not t.scrambled])
        if len(keep) != len(titles):
            return sorted(t.number for t in keep)
    return None
