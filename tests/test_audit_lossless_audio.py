"""Tests for scripts/audit_lossless_audio.py — fake probes, stubbed tools, no real runs.

subprocess.run is replaced wholesale in the script module: the fake ffprobe
answers canned stream json by path shape (the remux temp gets the post-fix
inventory), the fake ffmpeg writes the temp it was asked for. shutil.which
is faked so the startup probes never depend on the box.
"""

import importlib.util
import json
import os
import pathlib
import shutil

import pytest

_REPO = pathlib.Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location(
    "audit_lossless_audio", _REPO / "scripts" / "audit_lossless_audio.py"
)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


class R:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def streams(*tracks):
    """ffprobe stream json from (codec_name, profile) audio pairs."""
    return json.dumps(
        {
            "streams": [
                {"index": i, "codec_type": "audio", "codec_name": c, "profile": p}
                for i, (c, p) in enumerate(tracks)
            ]
        }
    )


FLAC_EAC3 = streams(("flac", ""), ("eac3", ""))
DTS_MA = streams(("dts", "DTS-HD MA"))
FIXED = streams(("ac3", ""), ("eac3", ""))


def make_run(
    calls,
    *,
    before=FLAC_EAC3,
    after=FIXED,
    ffmpeg_rc=0,
):
    """Fake subprocess.run dispatching on the tool basename, recording every
    invocation as (exe, cmd). ffprobe answers the scenario inventory for
    originals and `after` for the remux temp; ffmpeg writes that temp."""

    def run(cmd, capture_output=False, text=False, **kw):
        exe = pathlib.Path(cmd[0]).name
        calls.append((exe, list(cmd)))
        if exe == "ffprobe":
            return R(0, after if cmd[-1].endswith(audit._TMP_SUFFIX) else before, "")
        if exe == "ffmpeg":
            if ffmpeg_rc == 0:
                pathlib.Path(cmd[-1]).write_bytes(b"fixed-mkv")
            return R(ffmpeg_rc, "", "ffmpeg boom" if ffmpeg_rc else "")
        raise AssertionError(f"unexpected tool: {cmd}")

    return run


@pytest.fixture
def lib(tmp_path):
    return tmp_path / "lib"


@pytest.fixture
def stub(monkeypatch):
    """Faked shutil.which + a swappable fake run. Returns the state dict:
    set state['run'] to swap the scenario."""
    calls: list[tuple[str, list]] = []
    state = {"calls": calls, "run": make_run(calls)}
    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/{name}")

    def run(cmd, **kw):
        return state["run"](cmd, **kw)

    monkeypatch.setattr(audit.subprocess, "run", run)
    return state


def touch(path, content=b"orig-mkv"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def spy_replace(monkeypatch):
    seen = []
    real = os.replace
    monkeypatch.setattr(audit.os, "replace", lambda a, b: seen.append((a, b)) or real(a, b))
    return seen


def ffmpeg_cmds(state):
    return [cmd for exe, cmd in state["calls"] if exe == "ffmpeg"]


# -- dry-run report ---------------------------------------------------------------
def test_dry_run_reports_track_and_codec_changes_nothing(lib, stub, capsys):
    video = touch(lib / "M" / "M.mkv")
    rc = audit.run_audit(str(lib))
    assert rc == 0
    out = capsys.readouterr().out
    assert "M.mkv: audio track 0 flac" in out  # file, track, codec -- flac only
    assert "audio track 1" not in out  # eac3 is lossy: not reported
    assert "1 file(s): 0 compliant, 1 with lossless audio, 0 failed" in out
    assert ffmpeg_cmds(stub) == []  # dry-run never runs ffmpeg
    assert video.read_bytes() == b"orig-mkv"


def test_dry_run_reports_dtshd_ma_profile(lib, stub, capsys):
    touch(lib / "M" / "M.mkv")
    stub["run"] = make_run(stub["calls"], before=DTS_MA)
    audit.run_audit(str(lib))
    assert "M.mkv: audio track 0 dts (DTS-HD MA)" in capsys.readouterr().out


# -- fix argv + verify-then-replace -------------------------------------------------
def test_fix_remuxes_audio_only_and_replaces(lib, stub, capsys, monkeypatch):
    video = touch(lib / "M" / "M.mkv")
    replaced = spy_replace(monkeypatch)
    rc = audit.run_audit(str(lib), fix=True)
    assert rc == 0
    assert "fixed M.mkv" in capsys.readouterr().out
    cmds = ffmpeg_cmds(stub)
    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd[0] == "/fake/ffmpeg" and cmd[-1].endswith(audit._TMP_SUFFIX)
    assert cmd[-1] != str(video)  # writes the temp, not the original
    for flag in ("-map", "0:v", "-c:v", "copy", "-map", "0:s?", "-c:s", "copy"):
        assert flag in cmd
    i = cmd.index("0:a:0")
    assert cmd[i - 1] == "-map" and cmd[i + 1] == "-c:a:0" and cmd[i + 2] == "ac3"  # lossless
    i = cmd.index("0:a:1")
    assert cmd[i - 1] == "-map" and cmd[i + 1] == "-c:a:1" and cmd[i + 2] == "copy"  # lossy
    # the temp was ffprobe-verified (no lossless remains) before the replace
    probes_of_temp = [
        c for _e, c in stub["calls"] if _e == "ffprobe" and c[-1].endswith(".tmp.mkv")
    ]
    assert probes_of_temp
    assert video.read_bytes() == b"fixed-mkv"
    assert replaced and replaced[0][1] == str(video)
    assert not list(lib.rglob("*.tmp.mkv"))  # no temp left behind


def test_fix_skips_compliant_file_entirely(lib, stub, capsys):
    video = touch(lib / "M" / "M.mkv")
    stub["run"] = make_run(stub["calls"], before=FIXED)
    rc = audit.run_audit(str(lib), fix=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "ok M.mkv (no lossless audio)" in out
    assert "1 file(s): 1 compliant, 0 fixed, 0 failed" in out
    assert ffmpeg_cmds(stub) == []  # no ffmpeg at all
    assert video.read_bytes() == b"orig-mkv"


def test_fix_verify_failure_keeps_original_and_deletes_temp(lib, stub, capsys):
    video = touch(lib / "M" / "M.mkv")
    stub["run"] = make_run(stub["calls"], before=FLAC_EAC3, after=FLAC_EAC3)  # temp still lossless
    rc = audit.run_audit(str(lib), fix=True)
    assert rc == 1
    assert "FAIL M.mkv (verify failed: lossless audio remains) -- original kept" in (
        capsys.readouterr().out
    )
    assert video.read_bytes() == b"orig-mkv"
    assert not list(lib.rglob("*.tmp.mkv"))


def test_fix_ffmpeg_failure_keeps_original(lib, stub, capsys):
    video = touch(lib / "M" / "M.mkv")
    stub["run"] = make_run(stub["calls"], ffmpeg_rc=1)
    rc = audit.run_audit(str(lib), fix=True)
    assert rc == 1
    assert "FAIL M.mkv (ffmpeg rc=1) -- original kept" in capsys.readouterr().out
    assert video.read_bytes() == b"orig-mkv"
    assert not list(lib.rglob("*.tmp.mkv"))
