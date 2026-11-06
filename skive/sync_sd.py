"""SD card sync: auto-detect the card, rsync the music library onto it.

Ported from lib/sync-sd.sh: a mounted removable device (or one auto-mounted
under /media or /run/media) is used as-is; otherwise the unmounted removable
disks are listed to pick one, mounted, and unmounted again on exit. Cards that
were already mounted stay mounted. --size-only works on any card filesystem
(fat/exfat reject -a's timestamps/perms).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from rich.text import Text

from skive.common import RipLog, ensure_mounted, kill_child_after_grace
from skive import ui


def _lsblk(run, columns: str) -> list[dict]:
    """Run `lsblk -J -o <columns>` and return the blockdevices list ([] on failure)."""
    try:
        result = run(["lsblk", "-J", "-o", columns], capture_output=True, text=True)
    except OSError:
        return []
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout).get("blockdevices", [])
    except (ValueError, AttributeError):
        return []


def _walk(node: dict):
    """Yield a device node and all its descendants (jq's `.. | objects`)."""
    yield node
    for child in node.get("children") or []:
        yield from _walk(child)


def find_mounted_cards(run=subprocess.run) -> list[str]:
    """Distinct mountpoints of mounted removable cards, sorted.

    A device counts when rm==true, or its mountpoint is auto-mounted under
    /media or /run/media (catches internal mmcblk readers that don't report rm).
    Child partitions count too (a card's root may sit on a partition).
    """
    mounts = set()
    for node in _lsblk(run, "NAME,RM,MOUNTPOINT"):
        for dev in _walk(node):
            mp = dev.get("mountpoint")
            if not mp:
                continue
            if dev.get("rm") is True or re.match(r"^/(run/media/|media/)", mp):
                mounts.add(mp)
    return sorted(mounts)


def list_unmounted_disks(run=subprocess.run) -> list[str]:
    """Names of removable disks (sdx/mmcblk) with nothing mounted on them."""
    disks = []
    for node in _lsblk(run, "NAME,RM,TYPE,MOUNTPOINT"):
        if node.get("type") != "disk":
            continue
        name = node.get("name", "")
        if not (node.get("rm") is True or name.startswith("mmcblk")):
            continue
        if node.get("mountpoint"):
            continue
        if any(ch.get("mountpoint") for ch in (node.get("children") or [])):
            continue
        disks.append(name)
    return disks


def disk_info(disk: str, run=subprocess.run) -> tuple[str, str]:
    """(size, model) for a disk; ("?", "") when lsblk can't tell."""
    try:
        result = run(["lsblk", "-dno", "SIZE,MODEL", f"/dev/{disk}"], capture_output=True, text=True)
    except OSError:
        return "?", ""
    if result.returncode != 0:
        return "?", ""
    fields = result.stdout.strip().split(None, 1)
    size = fields[0] if fields else "?"
    model = fields[1].strip() if len(fields) > 1 else ""
    return size, model


def _pick(title: str, rows: list[tuple[str, ...]], columns: tuple[str, ...]) -> int | None:
    """List the candidates in the shared table and ask for a 1-based choice.
    Returns the 0-based index, or None on abort (blank, invalid, or no stdin)."""
    ui.info(title)
    table = ui.table("#", *columns)
    for i, row in enumerate(rows, 1):
        table.add_row(Text(str(i), style="bold"), *row)
    ui.console.print(table)
    try:
        choice = ui.ask("Pick one", choices="number \u00b7 Enter abort")
    except EOFError:
        return None
    count = len(rows)
    if choice.isdigit() and 1 <= int(choice) <= count:
        return int(choice) - 1
    return None


def resolve_partition(disk: str, run=subprocess.run) -> str:
    """First 'part' child of a disk, or the disk itself when partitionless."""
    try:
        result = run(["lsblk", f"/dev/{disk}", "-lno", "NAME,TYPE"], capture_output=True, text=True)
    except OSError:
        return disk
    if result.returncode != 0:
        return disk
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == "part":
            return fields[0]
    return disk


def mount_card(part: str, mnt: Path, run=subprocess.run) -> bool:
    """Mount a partition as the current user; plain mount fallback for ext4-style fs."""
    opts = f"uid={os.getuid()},gid={os.getgid()},umask=0077"
    if run(["sudo", "mount", "-o", opts, f"/dev/{part}", str(mnt)], capture_output=True).returncode == 0:
        return True
    return run(["sudo", "mount", f"/dev/{part}", str(mnt)], capture_output=True).returncode == 0


def unmount_card(mnt: Path, run=subprocess.run) -> None:
    """Best-effort unmount of a card this run mounted; warn with the manual command."""
    if run(["sudo", "umount", str(mnt)], capture_output=True).returncode != 0:
        ui.warn(f"could not unmount {mnt} — run: sudo umount {mnt}")
    try:
        mnt.rmdir()
    except OSError:
        pass


_RSYNC_PROGRESS_RX = re.compile(
    r"^\s*([\d,]+)\s+(\d+)%\s+(\S+/s)\s+(\d+):(\d{2}):(\d{2})")


def parse_rsync_progress(line: str) -> tuple[int, int, str, str] | None:
    """(bytes done, percent, rate, ETA) from an `--info=progress2` line such as
    `  1,234,567  61%  1.20GB/s  0:03:21 (xfr#3, to-chk=10/50)`; None for any
    other line. The ETA drops a zero hour: `3:21`, `1:02:03`."""
    m = _RSYNC_PROGRESS_RX.match(line)
    if m is None:
        return None
    done, pct, rate, h, mm, ss = m.groups()
    eta = f"{int(h)}:{mm}:{ss}" if int(h) else f"{int(mm)}:{ss}"
    return int(done.replace(",", "")), int(pct), rate, eta


def format_bytes(n: int) -> str:
    """`4.1 GB`: decimal units, one decimal above bytes."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000


def rsync_library(src: Path, card: str, popen=subprocess.Popen,
                  log: RipLog | None = None) -> tuple[int, int]:
    """Copy the library onto the card (source kept). Trailing slashes matter.

    rsync's overall progress drives the run view's footer (bar, percent, rate,
    ETA) on a TTY; off a TTY a plain `info` line prints every 10%. Any other
    rsync output is a problem report: it shows as a warning and, given a
    `log`, goes to the run's raw log. Returns (exit code, bytes copied so far).
    """
    proc = popen([
        "rsync", "-r", "--size-only", "--info=progress2",
        "--exclude=validation.json", "--exclude=*.log", "--exclude=*.cue",
        f"{src}/", f"{card}/",
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    view = ui.view()
    on_tty = view is not None and view.tty
    name = Path(card).name or str(card)
    done = last_step = 0

    def handle(raw: bytes) -> None:
        nonlocal done, last_step
        line = raw.decode(errors="replace").strip()
        if not line:
            return
        progress = parse_rsync_progress(line)
        if progress is None:
            ui.warn(f"rsync: {line}")
            if log is not None:
                log.raw("rsync", line)
            return
        done, pct, rate, eta = progress
        if on_tty:
            view.footer("", f"syncing to {name}", rate)
            view.progress(pct, eta)
        elif pct // 10 != last_step or pct == 100:
            last_step = pct // 10
            ui.info(f"{pct}%  {rate}  ETA {eta}")

    try:
        pending = b""
        for chunk in iter(lambda: proc.stdout.read(4096), b""):
            *lines, pending = re.split(rb"[\r\n]", pending + chunk)
            for raw in lines:
                handle(raw)
        handle(pending)
        return proc.wait(), done
    except KeyboardInterrupt:
        kill_child_after_grace(proc)
        raise


def _result_line(rc: int, synced: int | None, elapsed: int, run: str) -> None:
    """The run's closing line, from the real exit state: `✓ synced 4.1 GB ·
    2m10s`, or a failure naming the next step."""
    if rc == 0:
        ui.result_line("ok", "up to date" if not synced else f"synced {format_bytes(synced)}",
                       elapsed=elapsed)
    elif rc == 130:
        ui.result_line("fail", "interrupted", elapsed=elapsed,
                       note="re-run to resume")
    else:
        parts = [f"rsync failed (exit {rc})"]
        if synced:
            parts.append(f"{format_bytes(synced)} copied")
        ui.result_line("fail", *parts, elapsed=elapsed,
                       note=f"re-run to resume (run {run})")


def sync_sd(args) -> None:
    """Main SD card sync orchestration.

    Args:
        args: argparse.Namespace from cmd_sync_sd (mountpoint positional)

    Raises:
        SystemExit: On any error or user abort
    """
    mnt = os.environ.get("MUSIC_MOUNT", "/mnt/music")
    src = Path(mnt) / "library"

    card = args.mountpoint or ""
    pending_disk = None

    # Detection (a mountpoint argument skips it entirely)
    if not card:
        mounted = find_mounted_cards()
        if len(mounted) == 1:
            card = mounted[0]
        elif len(mounted) > 1:
            idx = _pick("Several removable devices mounted — pick one",
                        [(mp,) for mp in mounted], ("mountpoint",))
            if idx is None:
                ui.fail("no valid choice — aborting")
                sys.exit(1)
            card = mounted[idx]
        else:
            disks = list_unmounted_disks()
            if not disks:
                ui.fail("no SD card found (mounted or unmounted) — insert one and re-run")
                sys.exit(1)
            infos = [disk_info(d) for d in disks]
            idx = _pick("No card mounted — pick a device to mount",
                        [(d, size, model) for d, (size, model) in zip(disks, infos)],
                        ("device", "size", "model"))
            if idx is None:
                ui.fail("no valid choice — aborting")
                sys.exit(1)
            pending_disk = disks[idx]

    log = RipLog("skive sync-sd", "rsync", "-")
    log.start()

    # Everything after the card is in hand: log the total and unmount a card
    # THIS run mounted on every exit — success, Ctrl-C, or failure.
    we_mounted = False
    card_mnt = None
    rc = 0
    synced = None  # bytes copied once rsync has run; None before that
    sync_started = False
    started = time.monotonic()
    try:
        if pending_disk:
            part = resolve_partition(pending_disk)
            card_mnt = Path(tempfile.mkdtemp(prefix="sync-sd."))
            if not mount_card(part, card_mnt):
                try:
                    card_mnt.rmdir()
                except OSError:
                    pass
                ui.fail(f"mounting /dev/{part} failed (needs sudo? wrong fs / unreadable?)")
                sys.exit(1)
            we_mounted = True
            card = str(card_mnt)
            log.name = part
        else:
            if not Path(card).is_dir():
                ui.fail(f"{card} is not a mounted directory")
                sys.exit(1)
            log.name = Path(card).name

        # Only pay the NFS mount cost once a card is in hand.
        ensure_mounted(mnt, os.environ.get("NFS_MUSIC", ""))
        if not src.is_dir():
            ui.fail(f"no library at {src} (run tag first)")
            sys.exit(1)

        log.phase("sync")
        started = time.monotonic()
        sync_started = True
        view = ui.view() or ui.RunView(perf=False)  # sync footer is one line
        ui.set_view(view)
        try:
            rc, synced = rsync_library(src, card, log=log)
        finally:
            view.close()
            ui.clear_view()
        if rc == 0:
            log.phase_end()
    except KeyboardInterrupt:
        rc = 130
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        log.finish(rc)
        if we_mounted and card_mnt is not None:
            unmount_card(card_mnt)

    if sync_started:
        _result_line(rc, synced, int(time.monotonic() - started), log.run)
    sys.exit(rc)
