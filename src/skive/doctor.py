"""skive doctor — the green table: the box's readiness.

Eight read-only checks, one table, one result line: optical device nodes,
makemkvcon drive enumeration (through scan's MAKEMKV_INFO replay seam),
the two share roots (common.share_status), the MakeMKV key, the TMDB key
(validated against the API's validate-key endpoint — a bad key fails, an
unreachable API only warns), tesseract's languages, and VA-API encode
entrypoints (informational — VCN hardware has them, CPU-only boxes do
not). A drive-less box never rips, so the drive, enumeration, and key
checks report a skip there instead of failing. Doctor fixes nothing.
Exit 1 only when a prerequisite is missing; a warn (local share, missing
vainfo, offline TMDB) never fails the run.
"""

import os
import re
import subprocess
from pathlib import Path

from skive import ui
from skive.common import share_status
from skive.scan import _info
from skive.tmdb import validate_key

SHARES = (
    ("videos share", "VIDEOS_MOUNT", "/mnt/videos"),
    ("music share", "MUSIC_MOUNT", "/mnt/music"),
)


def check_devices(dev: str = "/dev") -> tuple[str, str]:
    """Optical device nodes: any /dev/sr* or /dev/sg*. Their absence means
    a drive-less box (ripping happens elsewhere) — a warn, not a fail."""
    nodes = sorted(p.name for pat in ("sr*", "sg*") for p in Path(dev).glob(pat))
    if nodes:
        return "ok", f"{len(nodes)} nodes: {' '.join(nodes)}"
    return "warn", (
        "no /dev/sr* or /dev/sg* — drive-less box (rip checks skipped); "
        "on a box with drives: load the sg module, join the cdrom group"
    )


def check_drives(run=subprocess.run) -> tuple[str, str]:
    """makemkvcon's own drive enumeration: `makemkvcon -r info` through the
    scan seam (honors MAKEMKV_INFO), counting DRV: lines."""
    try:
        out = _info(None, run=run)
    except OSError as e:
        return "fail", f"makemkvcon did not run: {e}"
    n = sum(1 for line in out.splitlines() if line.startswith("DRV:"))
    if n:
        return "ok", f"{n} drive(s) enumerated"
    return "fail", "makemkvcon lists no drives — check the /dev nodes and the cdrom group"


def check_key(settings: Path | None = None) -> tuple[str, str]:
    """The MakeMKV key: MAKEMKV_KEY in the environment, else app_Key on disk."""
    if os.environ.get("MAKEMKV_KEY"):
        return "ok", "MAKEMKV_KEY set in the environment"
    path = settings if settings is not None else Path.home() / ".MakeMKV" / "settings.conf"
    try:
        text = path.read_text() if path.is_file() else ""
    except OSError:
        text = ""
    if re.search(r'^app_Key\s*=\s*"[^"]+"', text, re.MULTILINE):
        return "ok", f"app_Key present in {path}"
    return "fail", (
        "no MakeMKV key — set MAKEMKV_KEY or app_Key in ~/.MakeMKV/settings.conf (BD ripping dead)"
    )


def check_tesseract(run=subprocess.run) -> tuple[str, str]:
    """tesseract's language list must contain eng and dan (OCR subtitles)."""
    try:
        out = run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
    except OSError:
        return "fail", "tesseract not found — install it (OCR subtitles need it)"
    missing = {"eng", "dan"} - set(out.split())
    if missing:
        return "fail", f"tesseract missing language data: {' '.join(sorted(missing))}"
    return "ok", "eng + dan installed"


def check_vainfo(run=subprocess.run) -> tuple[str, str]:
    """VA-API encode entrypoints — informational, never a fail: CPU boxes
    have none and encode in software."""
    try:
        proc = run(["vainfo"], capture_output=True, text=True)
        out = proc.stdout + proc.stderr  # vainfo prints its profile list to stderr
    except OSError:
        return "warn", "vainfo not found — software encode only (CPU box)"
    if "VAEntrypointEncSlice" in out:
        return "ok", "VA-API encode entrypoints present"
    return "warn", "no VA-API encode entrypoints — software encode only (CPU box)"


def _render(rows: list[tuple[str, str, str]]) -> None:
    """One table on a TTY; piped, one plain event line per row (the
    event-line rule: no box characters)."""
    if ui._styled():
        table = ui.table("check", "status", "detail")
        for name, kind, detail in rows:
            table.add_row(name, ui.mark(kind, kind), detail)
        ui.console.print(table)
        return
    emit = {"ok": ui.ok, "warn": ui.warn, "fail": ui.fail}
    for name, kind, detail in rows:
        emit[kind](f"{name}: {detail}")


def run_doctor(dev: str = "/dev", run=subprocess.run, settings: Path | None = None) -> int:
    """Every check in one table, then the result line. Returns the exit
    code: 0 when no prerequisite is missing, 1 otherwise. Read-only."""
    rows: list[tuple[str, str, str]] = [
        ("optical devices", *check_devices(dev)),
    ]
    # drives and key are rip prerequisites — only a box with optical nodes
    # can rip, so a drive-less box skips both instead of failing them
    if rows[0][1] == "ok":
        rows += [
            ("makemkvcon drives", *check_drives(run)),
            ("makemkv key", *check_key(settings)),
        ]
    rows += [
        # the shares read the same env override the rip commands use
        *(
            (name, *share_status(os.environ.get(env, default), env))
            for name, env, default in SHARES
        ),
        ("tesseract langs", *check_tesseract(run)),
        ("vaapi encode", *check_vainfo(run)),
        # every rip's metadata pick and every validation walk need TMDB, so
        # the key is checked on every box — not only drive-ful ones
        ("tmdb key", *validate_key()),
    ]
    _render(rows)
    fails = sum(1 for _, kind, _ in rows if kind == "fail")
    if fails:
        ui.fail(f"{fails} prerequisite(s) missing — box not ready")
        return 1
    if any(kind == "warn" for _, kind, _ in rows):
        ui.ok("box ready — with warnings")
    else:
        ui.ok("box ready")
    return 0
