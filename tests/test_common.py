"""Tests for common.py: title-spec parser, rip.log, mount helper."""

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import skive.common


class TestParseTitles:
    """title-spec parser tests."""

    def test_all(self):
        assert skive.common.parse_titles("all") is None
        assert skive.common.parse_titles("") is None

    def test_single(self):
        assert skive.common.parse_titles("5") == [5]
        assert skive.common.parse_titles("0") == [0]

    def test_range(self):
        assert skive.common.parse_titles("2-4") == [2, 3, 4]
        assert skive.common.parse_titles("0-2") == [0, 1, 2]

    def test_comma_list(self):
        assert skive.common.parse_titles("0,5-7") == [0, 5, 6, 7]
        assert skive.common.parse_titles("1,3,5") == [1, 3, 5]
        assert skive.common.parse_titles("0,5-7,10") == [0, 5, 6, 7, 10]

    def test_reject_disc_n(self):
        with pytest.raises(ValueError, match="disc:N is not supported"):
            skive.common.parse_titles("disc:2")
        with pytest.raises(ValueError, match="disc:N is not supported"):
            skive.common.parse_titles("disc:0")

    def test_reject_garbage(self):
        with pytest.raises(ValueError, match="invalid title number"):
            skive.common.parse_titles("abc")
        with pytest.raises(ValueError, match="invalid range"):
            skive.common.parse_titles("3-")

    def test_range_start_gt_end(self):
        with pytest.raises(ValueError, match="invalid range.*start > end"):
            skive.common.parse_titles("5-2")


class TestSanitize:
    def test_strips_smb_hostile_chars(self):
        assert skive.common.sanitize("Team America: World Police (2004)") == \
            "Team America World Police (2004)"
        assert skive.common.sanitize('A/B?C*D"E<F>G|H') == "A-BCDEFGH"


class TestRipLog:
    """rip.log 8-column tests."""

    def test_started_line(self, tmp_path):
        log_path = tmp_path / "rip.log"
        fixed_ts = datetime(2024, 1, 15, 10, 30, 0)
        log = skive.common.RipLog("rip-movie", "makemkv", "Test Movie", str(log_path), now=lambda: fixed_ts)
        log.start()
        lines = log_path.read_text().splitlines()
        assert len(lines) == 1
        assert "2024-01-15 10:30:00" in lines[0]
        assert "rip-movie  makemkv  Test Movie  -  0m00  started" in lines[0]

    def test_phase_line(self, tmp_path):
        log_path = tmp_path / "rip.log"
        timestamps = [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 31, 0), datetime(2024, 1, 15, 10, 35, 30)]  # start, phase start, phase end
        ts_iter = iter(timestamps)
        log = skive.common.RipLog("rip-movie", "makemkv", "Test Movie", str(log_path), now=lambda: next(ts_iter))
        log.start()
        log.phase("scan")
        log.phase_end()
        lines = log_path.read_text().splitlines()
        assert len(lines) == 2
        assert "2024-01-15 10:31:00  2024-01-15 10:35:30  rip-movie  makemkv  Test Movie  scan  4m30  ok" in lines[1]

    def test_note_line_dashes(self, tmp_path):
        log_path = tmp_path / "rip.log"
        fixed_ts = datetime(2024, 1, 15, 10, 30, 0)
        log = skive.common.RipLog("rip-movie", "makemkv", "Test Movie", str(log_path), now=lambda: fixed_ts)
        log.start()
        log.note("scan", "audio kept=eng detected=eng")
        lines = log_path.read_text().splitlines()
        assert len(lines) == 2  # started + note
        assert "2024-01-15 10:30:00  -  rip-movie  makemkv  Test Movie  scan  -  audio kept=eng detected=eng" in lines[1]

    def test_note_tees_to_stderr(self, tmp_path, capsys):
        """Note lines also go to stderr (loguru WARNING sink)."""
        log_path = tmp_path / "rip.log"
        fixed_ts = datetime(2024, 1, 15, 10, 30, 0)
        log = skive.common.RipLog("rip-movie", "makemkv", "Test Movie", str(log_path), now=lambda: fixed_ts)
        log.start()
        log.note("scan", "audio kept=eng detected=eng")
        err = capsys.readouterr().err
        assert "audio kept=eng detected=eng" in err
        assert "rip-movie  makemkv  Test Movie  scan  -" in err

    def test_total_skipped_on_single_successful_phase(self, tmp_path, capsys):
        log_path = tmp_path / "rip.log"
        timestamps = [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 31, 0), datetime(2024, 1, 15, 10, 35, 0), datetime(2024, 1, 15, 10, 40, 0)]
        ts_iter = iter(timestamps)
        log = skive.common.RipLog("rip-movie", "makemkv", "Test Movie", str(log_path), now=lambda: next(ts_iter))
        log.start()
        log.phase("rip")
        log.phase_end()
        log.finish(0)
        lines = log_path.read_text().splitlines()
        assert len(lines) == 2  # started + phase, NO total
        assert "total" not in "\n".join(lines)

    def test_total_logged_on_failure(self, tmp_path):
        log_path = tmp_path / "rip.log"
        timestamps = [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 31, 0), datetime(2024, 1, 15, 10, 35, 0), datetime(2024, 1, 15, 10, 40, 0)]
        ts_iter = iter(timestamps)
        log = skive.common.RipLog("rip-movie", "makemkv", "Test Movie", str(log_path), now=lambda: next(ts_iter))
        log.start()
        log.phase("rip")
        log.phase_end()
        log.finish(3)
        lines = log_path.read_text().splitlines()
        assert len(lines) == 3  # started + phase + total
        # start_ts from finish(3) is 10:30:00, end_ts is 10:40:00 = 10m00
        assert "2024-01-15 10:30:00  2024-01-15 10:40:00  rip-movie  makemkv  Test Movie  total  10m00  FAIL(3)" in lines[2]

    def test_total_logged_on_multi_phase(self, tmp_path):
        log_path = tmp_path / "rip.log"
        timestamps = [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 31, 0), datetime(2024, 1, 15, 10, 32, 0), datetime(2024, 1, 15, 10, 35, 0), datetime(2024, 1, 15, 10, 36, 0), datetime(2024, 1, 15, 10, 40, 0)]
        ts_iter = iter(timestamps)
        log = skive.common.RipLog("rip-movie", "makemkv", "Test Movie", str(log_path), now=lambda: next(ts_iter))
        log.start()
        log.phase("scan")
        log.phase_end()
        log.phase("rip")
        log.phase_end()
        log.finish(0)
        lines = log_path.read_text().splitlines()
        assert len(lines) == 4  # started + phase1 + phase2 + total
        # start_ts from finish(0) is 10:30:00, end_ts is 10:40:00 = 10m00
        assert "total  10m00  ok" in lines[3]


class TestEnsureMounted:
    """NFS mount-on-demand tests."""

    def test_already_mounted_no_mount(self, tmp_path, monkeypatch):
        """Already mounted -> no mkdir, no mount."""
        mnt = "/mnt/videos"
        export = "host:/videos"
        monkeypatch.setattr(Path, "read_text", lambda self: "none /proc proc rw 0 0\nhost:/videos /mnt/videos nfs rw 0 0")
        mkdir_called, mount_called = False, False

        def fake_mkdir_p(parents, exist_ok):
            nonlocal mkdir_called
            mkdir_called = True

        def fake_run(cmd, *args, **kwargs):
            nonlocal mount_called
            mount_called = True
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(Path, "mkdir", fake_mkdir_p)
        monkeypatch.setattr(subprocess, "run", fake_run)
        # Call should exit early without error
        skive.common.ensure_mounted(mnt, export)
        assert not mkdir_called
        assert not mount_called

    def test_not_mounted_empty_export_errors(self, tmp_path, monkeypatch):
        """Unmounted but export empty -> clear error, exits 1."""
        mnt = "/mnt/videos"
        monkeypatch.setattr(Path, "read_text", lambda self: "none /proc proc rw 0 0")
        mkdir_called = False

        def fake_mkdir_p(parents, exist_ok):
            nonlocal mkdir_called
            mkdir_called = True

        monkeypatch.setattr(Path, "mkdir", fake_mkdir_p)
        with pytest.raises(SystemExit) as exc:
            skive.common.ensure_mounted(mnt, "")
        assert exc.value.code == 1
        assert not mkdir_called

    def test_not_mounted_mounts(self, tmp_path, monkeypatch):
        """Unmounted + export -> mkdir + mount called."""
        mnt = "/mnt/videos"
        export = "host:/videos"
        monkeypatch.setattr(Path, "read_text", lambda self: "none /proc proc rw 0 0")
        mkdir_called, mount_called = False, []

        def fake_mkdir(self, parents=False, exist_ok=False):
            nonlocal mkdir_called
            mkdir_called = True

        def fake_run(cmd, *args, **kwargs):
            nonlocal mount_called
            mount_called.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(Path, "mkdir", fake_mkdir)
        monkeypatch.setattr(subprocess, "run", fake_run)
        skive.common.ensure_mounted(mnt, export)
        assert mkdir_called
        assert mount_called
        assert mount_called[0] == ["sudo", "mount", "-t", "nfs", "host:/videos", "/mnt/videos"]