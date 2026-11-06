"""Tests for common.py: title-spec parser, JSONL run/raw logs, mount helper."""

import json
import re
import signal
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import skive.common


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


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
        with pytest.raises(ValueError, match="disc:N does not belong"):
            skive.common.parse_titles("disc:2")
        with pytest.raises(ValueError, match="disc:N does not belong"):
            skive.common.parse_titles("disc:0")

    def test_reject_garbage(self):
        with pytest.raises(ValueError, match="invalid title number"):
            skive.common.parse_titles("abc")
        with pytest.raises(ValueError, match="invalid range"):
            skive.common.parse_titles("3-")

    def test_range_start_gt_end(self):
        with pytest.raises(ValueError) as exc_info:
            skive.common.parse_titles("5-2")
        assert str(exc_info.value) == "invalid range 5-2: start > end"


class TestSanitize:
    def test_strips_smb_hostile_chars(self):
        assert skive.common.sanitize("Team America: World Police (2004)") == \
            "Team America World Police (2004)"
        assert skive.common.sanitize('A/B?C*D"E<F>G|H') == "A-BCDEFGH"


class TestRipLog:
    """JSONL run-log and raw-log tests (CONTEXT.md: Run log, Raw log)."""

    @staticmethod
    def _log(tmp_path, times):
        ts_iter = iter(times)
        return skive.common.RipLog("rip-movie", "makemkv", "Test Movie",
                                   root=str(tmp_path), now=lambda: next(ts_iter))

    @staticmethod
    def _run_lines(tmp_path) -> list[dict]:
        return _read_jsonl(tmp_path / "logs" / "rip.jsonl")

    @staticmethod
    def _raw_path(tmp_path, log) -> Path:
        return tmp_path / "logs" / "raw" / f"{log.run}.jsonl"

    def test_run_id_is_short_uuid(self, tmp_path):
        log = skive.common.RipLog("rip-movie", "makemkv", root=str(tmp_path))
        assert re.fullmatch(r"[0-9a-f]{8}", log.run)

    def test_run_log_defaults_to_videos_mount(self, tmp_path, monkeypatch):
        """Without an explicit root the run log lives on the videos share."""
        monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
        log = skive.common.RipLog("rip-movie", "makemkv", now=lambda: datetime(2024, 1, 15, 10, 30, 0))
        log.start()
        assert len(self._run_lines(tmp_path)) == 1

    def test_started_line(self, tmp_path):
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0)])
        log.start()
        lines = self._run_lines(tmp_path)
        assert len(lines) == 1
        assert lines[0] == {
            "ts": "2024-01-15T10:30:00", "run": log.run, "script": "rip-movie",
            "software": "makemkv", "name": "Test Movie", "phase": "-",
            "status": "started", "elapsed_s": 0,
        }

    def test_phase_line(self, tmp_path):
        # start, phase start, phase end
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 31, 0), datetime(2024, 1, 15, 10, 35, 30)])
        log.start()
        log.phase("scan")
        log.phase_end()
        lines = self._run_lines(tmp_path)
        assert len(lines) == 2
        assert lines[1]["phase"] == "scan"
        assert lines[1]["status"] == "ok"
        assert lines[1]["elapsed_s"] == 270  # 10:31:00 -> 10:35:30 = 4m30

    def test_note_line(self, tmp_path):
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 30, 5)])
        log.start()
        log.note("scan", "audio kept=eng detected=eng")
        lines = self._run_lines(tmp_path)
        assert len(lines) == 2  # started + note
        assert lines[1]["phase"] == "scan"
        assert lines[1]["status"] == "audio kept=eng detected=eng"
        assert lines[1]["elapsed_s"] == 0

    def test_note_tees_to_stderr(self, tmp_path, capsys):
        """Note lines also surface on stderr (the old WARNING tee)."""
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 30, 5)])
        log.start()
        log.note("scan", "audio kept=eng detected=eng")
        err = capsys.readouterr().err
        assert "audio kept=eng detected=eng" in err
        assert "scan" in err

    def test_total_skipped_on_single_successful_phase(self, tmp_path):
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 31, 0), datetime(2024, 1, 15, 10, 35, 0), datetime(2024, 1, 15, 10, 40, 0)])
        log.start()
        log.phase("rip")
        log.phase_end()
        log.finish(0)
        lines = self._run_lines(tmp_path)
        assert len(lines) == 2  # started + phase, NO total
        assert "total" not in [line["phase"] for line in lines]

    def test_total_logged_on_failure(self, tmp_path):
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 31, 0), datetime(2024, 1, 15, 10, 35, 0), datetime(2024, 1, 15, 10, 40, 0)])
        log.start()
        log.phase("rip")
        log.phase_end()
        log.finish(3)
        lines = self._run_lines(tmp_path)
        assert len(lines) == 3  # started + phase + total
        assert lines[2] == {
            "ts": "2024-01-15T10:40:00", "run": log.run, "script": "rip-movie",
            "software": "makemkv", "name": "Test Movie", "phase": "total",
            "status": "FAIL(3)", "elapsed_s": 600,
        }

    def test_total_logged_on_multi_phase(self, tmp_path):
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 31, 0), datetime(2024, 1, 15, 10, 32, 0), datetime(2024, 1, 15, 10, 35, 0), datetime(2024, 1, 15, 10, 36, 0), datetime(2024, 1, 15, 10, 40, 0)])
        log.start()
        log.phase("scan")
        log.phase_end()
        log.phase("rip")
        log.phase_end()
        log.finish(0)
        lines = self._run_lines(tmp_path)
        assert len(lines) == 4  # started + phase1 + phase2 + total
        assert lines[3]["phase"] == "total"
        assert lines[3]["status"] == "ok"
        assert lines[3]["elapsed_s"] == 600

    # -- raw log: logs/raw/<run>.jsonl ---------------------------------------

    def test_raw_line_schema(self, tmp_path):
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 30, 1)])
        log.start()
        log.raw("cyanrip", "Ripping track 1")
        lines = _read_jsonl(self._raw_path(tmp_path, log))
        assert lines == [{
            "ts": "2024-01-15T10:30:01", "run": log.run,
            "tool": "cyanrip", "line": "Ripping track 1",
        }]

    def test_raw_log_deleted_on_clean_exit(self, tmp_path):
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 30, 1), datetime(2024, 1, 15, 10, 30, 2)])
        log.start()
        log.raw("makemkvcon", "MSG: done")
        log.finish(0)
        assert not self._raw_path(tmp_path, log).exists()

    def test_raw_log_deleted_even_when_total_skipped(self, tmp_path):
        """rc 0 + exactly one phase skips the total line — the raw log still goes."""
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 31, 0), datetime(2024, 1, 15, 10, 35, 0), datetime(2024, 1, 15, 10, 35, 1)])
        log.start()
        log.phase("rip")
        log.phase_end()
        log.raw("makemkvcon", "MSG: done")
        log.finish(0)
        assert not self._raw_path(tmp_path, log).exists()
        assert len(self._run_lines(tmp_path)) == 2  # started + phase, no total

    def test_raw_log_kept_on_failure(self, tmp_path):
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 30, 1), datetime(2024, 1, 15, 10, 30, 2)])
        log.start()
        log.raw("makemkvcon", "MSG: failed")
        log.finish(3)
        assert self._raw_path(tmp_path, log).exists()
        assert len(self._run_lines(tmp_path)) == 2  # started + FAIL(3) total

    def test_scan_dump_survives_clean_exit(self, tmp_path):
        """finish(0) deletes the raw log but never the scan dump — the dump
        is the one log written before anyone knows a run will go wrong."""
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 30, 1), datetime(2024, 1, 15, 10, 30, 2)])
        log.start()
        log.raw("makemkvcon", "MSG: done")
        log.scan_dump("ROGUE_ONE", "DRV:0,2,0,0,\"DVD-ROM\"\n")
        log.finish(0)
        assert not self._raw_path(tmp_path, log).exists()
        dump = tmp_path / "logs" / "scan" / f"{log.run}_ROGUE_ONE.txt"
        assert dump.read_text(encoding="utf-8") == 'DRV:0,2,0,0,"DVD-ROM"\n'

    def test_scan_dump_labelless_run_gets_disc_name(self, tmp_path):
        """An empty label (disc reported nothing) still dumps, as <run>_disc.txt."""
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0)])
        log.scan_dump("", "TCOUNT:0\n")
        assert (tmp_path / "logs" / "scan" / f"{log.run}_disc.txt").exists()


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


def test_set_verbosity_clamps_to_tiers(monkeypatch):
    """set_verbosity stores the console level clamped to 0/1/2."""
    monkeypatch.setattr(skive.common, "VERBOSITY", 0)  # snapshot for restore
    skive.common.set_verbosity(-1)
    assert skive.common.VERBOSITY == 0
    skive.common.set_verbosity(2)
    assert skive.common.VERBOSITY == 2
    skive.common.set_verbosity(5)  # -vvv and beyond clamps to 2
    assert skive.common.VERBOSITY == 2


# -- Interrupt (Ctrl+C): handler, second-SIGINT, child kill (CONTEXT.md:
# Interrupt). Deterministic: the handler and the kill helper are called
# directly — the tests never deliver a real signal.


class FakeChild:
    """A stub Popen: records signals, waits, kills; can stall past the grace
    window (wait raises TimeoutExpired once) or already be dead."""

    def __init__(self, stalls=False, alive=True):
        self.stalls = stalls
        self.alive = alive
        self.sent = []
        self.waits = []
        self.killed = False

    def poll(self):
        return None if self.alive else 0

    def send_signal(self, sig):
        self.sent.append(sig)

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.stalls:
            self.stalls = False
            raise subprocess.TimeoutExpired(cmd="child", timeout=timeout)
        return 0

    def kill(self):
        self.killed = True


def test_interrupted_is_a_keyboard_interrupt():
    """Interrupted subclasses KeyboardInterrupt so existing catches keep working."""
    assert issubclass(skive.common.Interrupted, KeyboardInterrupt)


def test_sigint_handler_first_raises_interrupted():
    """One Ctrl+C becomes the clean Interrupted exception path."""
    skive.common._sigint_count = 0
    with pytest.raises(skive.common.Interrupted):
        skive.common._sigint_handler(2, None)


def test_sigint_handler_second_exits_without_cleanup(monkeypatch):
    """A second Ctrl+C restores the default disposition and re-raises: instant
    exit, no cleanup, no traceback (the next junk purge is the safety net)."""
    calls = []
    monkeypatch.setattr(skive.common, "_exit_immediately", lambda: calls.append(1))
    skive.common._sigint_count = 1
    skive.common._sigint_handler(2, None)  # must not raise
    assert calls == [1]


def test_install_interrupt_handler_resets_counter(monkeypatch):
    """Installing the handler resets the second-SIGINT counter from any prior run."""
    installed = []
    monkeypatch.setattr("signal.signal",
                        lambda sig, handler: installed.append((sig, handler)))
    skive.common._sigint_count = 3
    skive.common.install_interrupt_handler()
    assert installed == [(2, skive.common._sigint_handler)]
    assert skive.common._sigint_count == 0


@pytest.mark.parametrize("grace", [0.1, 5])
def test_kill_child_forwards_sigint_and_waits_grace(grace):
    """The child is SIGINTed and awaited exactly grace seconds; a child that
    exits inside the window is reaped, never killed."""
    child = FakeChild()
    skive.common.kill_child_after_grace(child, grace=grace)
    assert child.sent == [signal.SIGINT]
    assert child.waits == [grace]
    assert not child.killed


def test_kill_child_kills_after_grace_window():
    """A child wedged past the grace window gets killed and reaped."""
    child = FakeChild(stalls=True)
    skive.common.kill_child_after_grace(child, grace=0.1)
    assert child.sent == [signal.SIGINT]
    assert child.killed
    # the grace wait, then the post-kill reap
    assert child.waits == [0.1, None]


def test_kill_child_skips_already_dead():
    """A child that already exited is not signalled again."""
    child = FakeChild(alive=False)
    skive.common.kill_child_after_grace(child)
    assert child.sent == [] and child.waits == [] and not child.killed


class TestInterruptedRunLog:
    """RipLog.interrupted(): the run's closing line says status=interrupted and
    keeps the raw log; a later finish() stays silent (CONTEXT.md: Interrupt)."""

    def test_interrupted_line(self, tmp_path):
        ts = iter([datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 42, 0)])
        log = skive.common.RipLog("rip-movie", "makemkv", "Test Movie",
                                  root=str(tmp_path), now=lambda: next(ts))
        log.start()
        log.interrupted()
        lines = _read_jsonl(tmp_path / "logs" / "rip.jsonl")
        assert len(lines) == 2  # started + interrupted total
        assert lines[1] == {
            "ts": "2024-01-15T10:42:00", "run": log.run, "script": "rip-movie",
            "software": "makemkv", "name": "Test Movie", "phase": "total",
            "status": "interrupted", "elapsed_s": 720,
        }

    def test_interrupted_keeps_raw_log_and_finish_stays_silent(self, tmp_path):
        ts = iter(datetime(2024, 1, 15, 10, 30, 0) + timedelta(seconds=n) for n in range(5))
        log = skive.common.RipLog("rip-movie", "makemkv", "Test Movie",
                                  root=str(tmp_path), now=lambda: next(ts))
        log.start()
        log.raw("makemkvcon", "MSG: mid-title")
        log.interrupted()
        log.finish(130)  # the finally's finish must not add a second total
        lines = _read_jsonl(tmp_path / "logs" / "rip.jsonl")
        assert [line["status"] for line in lines] == ["started", "interrupted"]
        assert (tmp_path / "logs" / "raw" / f"{log.run}.jsonl").exists()


def test_note_echo_false_logs_without_the_stderr_tee(tmp_path, capsys):
    log = skive.common.RipLog("skive tag", "beets", root=tmp_path)
    log.start()
    log.note("cd", "filed", echo=False)
    assert capsys.readouterr().err == ""
    assert "filed" in (tmp_path / "logs" / "rip.jsonl").read_text()
