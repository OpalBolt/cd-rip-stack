"""Tests for sync_sd.py — no real lsblk/sudo/rsync; stub the binaries."""

import json
import shutil
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from skive.sync_sd import (
    sync_sd, find_mounted_cards, list_unmounted_disks, disk_info,
    resolve_partition, mount_card, unmount_card, rsync_library,
)


class FakeArgs:
    """Minimal argparse.Namespace for sync_sd."""
    def __init__(self, mountpoint=None):
        self.mountpoint = mountpoint


class FakeResult:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def scripted_run(*responses):
    """Build a run callable returning a FakeResult per command substring."""
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        joined = " ".join(cmd)
        for sub, result in responses:
            if sub in joined:
                return result
        raise AssertionError(f"unexpected command: {joined}")

    return run, calls


class QueueRun:
    """run callable consuming FakeResults in order (mount fallback, exceptions)."""
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@contextmanager
def stub(**replacements):
    """Patch skive.sync_sd.<name> for the duration of the with block."""
    with ExitStack() as stack:
        for name, value in replacements.items():
            stack.enter_context(patch(f"skive.sync_sd.{name}", value))
        yield


@pytest.fixture(autouse=True)
def _stage_root(tmp_path, monkeypatch):
    """Keep staging and the JSONL run log off the real mounts."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    # spill: slice-02 RipLog writes logs under VIDEOS_MOUNT; spec requires tmp_path
    # logs roots (no real mounts) and sync_sd exercises the real RipLog.
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))


# --- detection: find_mounted_cards -------------------------------------------

def test_mounted_detection_single_card():
    j = '{"blockdevices":[{"name":"sdb","rm":true,"mountpoint":"/media/CARD"}]}'
    run, _ = scripted_run(("lsblk", FakeResult(stdout=j)))
    assert find_mounted_cards(run=run) == ["/media/CARD"]


def test_mounted_detection_media_path_counts_when_rm_false():
    """Internal mmcblk readers don't report rm; the /media mountpoint carries it."""
    j = '{"blockdevices":[{"name":"mmcblk0","rm":false,"mountpoint":"/media/CARD"}]}'
    run, _ = scripted_run(("lsblk", FakeResult(stdout=j)))
    assert find_mounted_cards(run=run) == ["/media/CARD"]


def test_mounted_detection_child_partition_mountpoint_counts():
    """A card's root sits on a partition; nested children must count."""
    j = ('{"blockdevices":[{"name":"sdb","rm":true,"mountpoint":null,'
         '"children":[{"name":"sdb1","rm":true,"mountpoint":"/run/media/user/CARD"}]}]}')
    run, _ = scripted_run(("lsblk", FakeResult(stdout=j)))
    assert find_mounted_cards(run=run) == ["/run/media/user/CARD"]


def test_mounted_detection_distinct_sorted():
    j = ('{"blockdevices":['
         '{"name":"sdb","rm":true,"mountpoint":"/media/B"},'
         '{"name":"sdc","rm":true,"mountpoint":"/media/A"},'
         '{"name":"sdd","rm":true,"mountpoint":null}]}')
    run, _ = scripted_run(("lsblk", FakeResult(stdout=j)))
    assert find_mounted_cards(run=run) == ["/media/A", "/media/B"]


# --- detection: list_unmounted_disks -----------------------------------------

def test_unmounted_disks_listing():
    """sdx + mmcblk shown; non-removable, non-disk, and partially-mounted excluded."""
    j = ('{"blockdevices":['
         '{"name":"sda","rm":false,"type":"disk","mountpoint":null,'
         '  "children":[{"name":"sda1","type":"part","mountpoint":"/"}]},'
         '{"name":"sdb","rm":true,"type":"disk","mountpoint":null,'
         '  "children":[{"name":"sdb1","type":"part","mountpoint":null}]},'
         '{"name":"mmcblk0","rm":false,"type":"disk","mountpoint":null},'
         '{"name":"sr0","rm":true,"type":"rom","mountpoint":null},'
         '{"name":"sdc","rm":true,"type":"disk","mountpoint":null,'
         '  "children":[{"name":"sdc1","type":"part","mountpoint":"/media/x"}]}]}')
    run, _ = scripted_run(("lsblk", FakeResult(stdout=j)))
    # sda: non-removable. sr0: not a disk. sdc: child mounted. Only sdb + mmcblk0.
    assert list_unmounted_disks(run=run) == ["sdb", "mmcblk0"]


def test_disk_info():
    run, _ = scripted_run(("lsblk", FakeResult(stdout="32G  QEMU HARDDISK  \n")))
    assert disk_info("sdb", run=run) == ("32G", "QEMU HARDDISK")


def test_disk_info_failure():
    run, _ = scripted_run(("lsblk", FakeResult(returncode=1)))
    assert disk_info("sdb", run=run) == ("?", "")


# --- partition resolution ----------------------------------------------------

def test_resolve_partition_first_part():
    run, _ = scripted_run(("lsblk", FakeResult(stdout="sdb1 part\nsdb2 part\n")))
    assert resolve_partition("sdb", run=run) == "sdb1"


def test_resolve_partition_partitionless_falls_back_to_disk():
    run, _ = scripted_run(("lsblk", FakeResult(stdout="sdb disk\n")))
    assert resolve_partition("sdb", run=run) == "sdb"


# --- mount/unmount -----------------------------------------------------------

def test_mount_card_uid_opts_first():
    run, calls = scripted_run(("sudo", FakeResult(returncode=0)))
    assert mount_card("sdb1", Path("/tmp/mnt"), run=run) is True
    cmd = calls[0]
    assert cmd[:2] == ["sudo", "mount"]
    assert cmd[3].startswith("uid=")
    assert cmd[4] == "/dev/sdb1" and cmd[5] == "/tmp/mnt"


def test_mount_card_falls_back_to_plain_mount():
    run = QueueRun([FakeResult(returncode=1), FakeResult(returncode=0)])
    assert mount_card("sdb1", Path("/tmp/mnt"), run=run) is True
    assert run.calls[1] == ["sudo", "mount", "/dev/sdb1", "/tmp/mnt"]


def test_mount_card_both_fail():
    run = QueueRun([FakeResult(returncode=1), FakeResult(returncode=1)])
    assert mount_card("sdb1", Path("/tmp/mnt"), run=run) is False
    assert len(run.calls) == 2


def test_unmount_card_removes_mountpoint(tmp_path):
    mnt = tmp_path / "card"
    mnt.mkdir()
    run, calls = scripted_run(("sudo", FakeResult(returncode=0)))
    unmount_card(mnt, run=run)
    assert calls[0] == ["sudo", "umount", str(mnt)]
    assert not mnt.exists()


def test_unmount_card_failure_warns(capsys):
    run, _ = scripted_run(("sudo", FakeResult(returncode=1)))
    unmount_card(Path("/tmp/does-not-matter"), run=run)
    assert "could not unmount" in capsys.readouterr().err


# --- rsync argv --------------------------------------------------------------

def test_rsync_argv_excludes_sidecars():
    """Rsync copies playable files but not album records or rip artifacts."""
    run, calls = scripted_run(("rsync", FakeResult(returncode=0)))
    rc = rsync_library(Path("/mnt/music/library"), "/media/CARD", run=run)
    assert rc == 0
    assert calls[0] == [
        "rsync", "-r", "--size-only", "--info=progress2",
        "--exclude=validation.json", "--exclude=*.log", "--exclude=*.cue",
        "/mnt/music/library/", "/media/CARD/",
    ]


# --- sync_sd orchestration ---------------------------------------------------

def test_sync_sd_mountpoint_argument_bypasses_detection(tmp_path):
    """No lsblk calls at all when a mountpoint is given."""
    card = tmp_path / "card"
    card.mkdir()
    (tmp_path / "library").mkdir()
    detect = Mock(side_effect=AssertionError("detection ran"))
    with stub(
        find_mounted_cards=detect,
        list_unmounted_disks=detect,
        ensure_mounted=Mock(),
        rsync_library=Mock(return_value=0),
    ):
        try:
            sync_sd(FakeArgs(mountpoint=str(card)))
        except SystemExit as e:
            assert e.code == 0
    detect.assert_not_called()


def test_sync_sd_mounted_card_used(tmp_path):
    card = tmp_path / "card"
    card.mkdir()
    (tmp_path / "library").mkdir()
    rsync = Mock(return_value=0)
    with stub(
        find_mounted_cards=Mock(return_value=[str(card)]),
        ensure_mounted=Mock(),
        rsync_library=rsync,
    ):
        try:
            sync_sd(FakeArgs())
        except SystemExit as e:
            assert e.code == 0
    rsync.assert_called_once_with(tmp_path / "library", str(card))


def test_sync_sd_several_mounted_prompt_list_and_pick(capsys, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (tmp_path / "library").mkdir()
    rsync = Mock(return_value=0)
    with stub(
        find_mounted_cards=Mock(return_value=[str(a), str(b)]),
        ensure_mounted=Mock(),
        rsync_library=rsync,
        Prompt=Mock(ask=Mock(return_value="2")),
    ):
        try:
            sync_sd(FakeArgs())
        except SystemExit as e:
            assert e.code == 0
    err = capsys.readouterr().err
    assert "several removable devices mounted — pick one:" in err
    assert "1)" in err and "2)" in err
    rsync.assert_called_once_with(tmp_path / "library", str(b))


def test_sync_sd_several_mounted_blank_aborts(tmp_path):
    with stub(
        find_mounted_cards=Mock(return_value=[str(tmp_path / "a"), str(tmp_path / "b")]),
        Prompt=Mock(ask=Mock(return_value="")),
    ):
        with pytest.raises(SystemExit) as e:
            sync_sd(FakeArgs())
        assert e.value.code == 1


def test_sync_sd_no_card_no_disks(tmp_path):
    with stub(
        find_mounted_cards=Mock(return_value=[]),
        list_unmounted_disks=Mock(return_value=[]),
    ):
        with pytest.raises(SystemExit) as e:
            sync_sd(FakeArgs())
        assert e.value.code == 1


def test_sync_sd_unmounted_pick_then_mount_then_unmount(capsys, tmp_path):
    (tmp_path / "library").mkdir()
    cardmnt = tmp_path / "cardmnt"
    cardmnt.mkdir()
    umount = Mock()
    with stub(
        find_mounted_cards=Mock(return_value=[]),
        list_unmounted_disks=Mock(return_value=["sdb"]),
        disk_info=Mock(return_value=("16G", "Model X")),
        Prompt=Mock(ask=Mock(return_value="1")),
        resolve_partition=Mock(return_value="sdb1"),
        mount_card=Mock(return_value=True),
        unmount_card=umount,
        ensure_mounted=Mock(),
        rsync_library=Mock(return_value=0),
        tempfile=Mock(mkdtemp=Mock(return_value=str(cardmnt))),
    ):
        try:
            sync_sd(FakeArgs())
        except SystemExit as e:
            assert e.code == 0
    err = capsys.readouterr().err
    assert "1) sdb  16G  Model X" in err
    umount.assert_called_once_with(cardmnt)


def test_sync_sd_unmount_on_rsync_failure(tmp_path):
    (tmp_path / "library").mkdir()
    cardmnt = tmp_path / "cardmnt"
    cardmnt.mkdir()
    umount = Mock()
    with stub(
        find_mounted_cards=Mock(return_value=[]),
        list_unmounted_disks=Mock(return_value=["sdb"]),
        Prompt=Mock(ask=Mock(return_value="1")),
        resolve_partition=Mock(return_value="sdb1"),
        mount_card=Mock(return_value=True),
        unmount_card=umount,
        ensure_mounted=Mock(),
        rsync_library=Mock(return_value=5),
        tempfile=Mock(mkdtemp=Mock(return_value=str(cardmnt))),
    ):
        with pytest.raises(SystemExit) as e:
            sync_sd(FakeArgs())
        assert e.value.code == 5
    umount.assert_called_once_with(cardmnt)


def test_sync_sd_failed_unmount_warning_is_kept_in_run_log(tmp_path, capsys):
    """Cleanup warnings remain attached to the run until the card unmounts."""
    from skive import ui

    (tmp_path / "library").mkdir()
    cardmnt = tmp_path / "cardmnt"
    cardmnt.mkdir()

    def failed_unmount(path):
        ui.warn(f"could not unmount {path} — run: sudo umount {path}")

    with stub(
        find_mounted_cards=Mock(return_value=[]),
        list_unmounted_disks=Mock(return_value=["sdb"]),
        Prompt=Mock(ask=Mock(return_value="1")),
        resolve_partition=Mock(return_value="sdb1"),
        mount_card=Mock(return_value=True),
        unmount_card=failed_unmount,
        ensure_mounted=Mock(),
        rsync_library=Mock(return_value=0),
        tempfile=Mock(mkdtemp=Mock(return_value=str(cardmnt))),
    ):
        with pytest.raises(SystemExit) as e:
            sync_sd(FakeArgs())
        assert e.value.code == 0

    lines = [json.loads(line) for line in
             (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert any(line["phase"] == "warn" and "could not unmount" in line["status"]
               for line in lines)
    assert "could not unmount" in capsys.readouterr().err


def test_sync_sd_unmount_on_keyboard_interrupt(tmp_path):
    (tmp_path / "library").mkdir()
    cardmnt = tmp_path / "cardmnt"
    cardmnt.mkdir()
    umount = Mock()
    with stub(
        find_mounted_cards=Mock(return_value=[]),
        list_unmounted_disks=Mock(return_value=["sdb"]),
        Prompt=Mock(ask=Mock(return_value="1")),
        resolve_partition=Mock(return_value="sdb1"),
        mount_card=Mock(return_value=True),
        unmount_card=umount,
        ensure_mounted=Mock(),
        rsync_library=Mock(side_effect=KeyboardInterrupt),
        tempfile=Mock(mkdtemp=Mock(return_value=str(cardmnt))),
    ):
        with pytest.raises(SystemExit) as e:
            sync_sd(FakeArgs())
        assert e.value.code == 130
    umount.assert_called_once_with(cardmnt)


def test_sync_sd_mount_failure_exits_and_cleans_tmpdir(tmp_path):
    cardmnt = tmp_path / "cardmnt"
    cardmnt.mkdir()
    umount = Mock()
    with stub(
        find_mounted_cards=Mock(return_value=[]),
        list_unmounted_disks=Mock(return_value=["sdb"]),
        Prompt=Mock(ask=Mock(return_value="1")),
        resolve_partition=Mock(return_value="sdb1"),
        mount_card=Mock(return_value=False),
        unmount_card=umount,
        tempfile=Mock(mkdtemp=Mock(return_value=str(cardmnt))),
    ):
        with pytest.raises(SystemExit) as e:
            sync_sd(FakeArgs())
        assert e.value.code == 1
    assert not cardmnt.exists()
    umount.assert_not_called()


def test_sync_sd_preexisting_card_never_unmounted(tmp_path):
    card = tmp_path / "card"
    card.mkdir()
    (tmp_path / "library").mkdir()
    umount = Mock()
    with stub(
        find_mounted_cards=Mock(return_value=[str(card)]),
        ensure_mounted=Mock(),
        rsync_library=Mock(return_value=0),
        unmount_card=umount,
    ):
        try:
            sync_sd(FakeArgs())
        except SystemExit as e:
            assert e.code == 0
    umount.assert_not_called()


def test_sync_sd_no_library_dir(tmp_path):
    card = tmp_path / "card"
    card.mkdir()
    with stub(
        find_mounted_cards=Mock(return_value=[str(card)]),
        ensure_mounted=Mock(),
    ):
        with pytest.raises(SystemExit) as e:
            sync_sd(FakeArgs())
        assert e.value.code == 1


def test_sync_sd_end_to_end_copies_files(tmp_path):
    """Real-filesystem run: stubbed rsync that actually copies the tree."""
    album = tmp_path / "library" / "Album"
    album.mkdir(parents=True)
    (album / "track.flac").write_text("flacdata")
    card = tmp_path / "card"
    card.mkdir()

    def fake_rsync(src, card):
        shutil.copytree(src, card, dirs_exist_ok=True)
        return 0

    with stub(
        find_mounted_cards=Mock(return_value=[str(card)]),
        ensure_mounted=Mock(),
        rsync_library=Mock(side_effect=fake_rsync),
    ):
        try:
            sync_sd(FakeArgs())
        except SystemExit as e:
            assert e.code == 0

    assert (card / "Album" / "track.flac").read_text() == "flacdata"
