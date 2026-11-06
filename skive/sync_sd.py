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
from pathlib import Path

from rich.prompt import Prompt

from skive.common import RipLog, ensure_mounted
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


def _pick(count: int) -> int | None:
    """Ask for a 1-based choice; return the 0-based index, or None on abort."""
    choice = Prompt.ask("choice (blank to abort): ", default="")
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


def rsync_library(src: Path, card: str, run=subprocess.run) -> int:
    """Copy the library onto the card (source kept). Trailing slashes matter."""
    return run([
        "rsync", "-r", "--size-only", "--info=progress2",
        "--exclude=validation.json", "--exclude=*.log", "--exclude=*.cue",
        f"{src}/", f"{card}/",
    ]).returncode


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
            print("sync-sd: several removable devices mounted — pick one:", file=sys.stderr)
            for i, mp in enumerate(mounted, 1):
                print(f"  {i}) {mp}", file=sys.stderr)
            idx = _pick(len(mounted))
            if idx is None:
                ui.fail("no valid choice — aborting")
                sys.exit(1)
            card = mounted[idx]
        else:
            disks = list_unmounted_disks()
            if not disks:
                ui.fail("no SD card found (mounted or unmounted) — insert one and re-run")
                sys.exit(1)
            print("sync-sd: no card mounted — pick a device to mount:", file=sys.stderr)
            for i, d in enumerate(disks, 1):
                size, model = disk_info(d)
                print(f"  {i}) {d}  {size}{'  ' + model if model else ''}", file=sys.stderr)
            idx = _pick(len(disks))
            if idx is None:
                ui.fail("no valid choice — aborting")
                sys.exit(1)
            pending_disk = disks[idx]

    log = RipLog("skive sync-sd", "rsync", "-")
    log.start()
    ui.set_log(log)

    # Everything after the card is in hand: log the total and unmount a card
    # THIS run mounted on every exit — success, Ctrl-C, or failure.
    we_mounted = False
    card_mnt = None
    rc = 0
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
        rc = rsync_library(src, card)
        if rc == 0:
            log.phase_end()
    except KeyboardInterrupt:
        rc = 130
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        try:
            if we_mounted and card_mnt is not None:
                unmount_card(card_mnt)
        finally:
            log.finish(rc)
            ui.clear_log()

    sys.exit(rc)
