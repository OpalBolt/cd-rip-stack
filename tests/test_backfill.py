"""Tests for scripts/backfill.py — fake inventories + stubbed tools, no real runs.

subprocess.run is replaced wholesale in BOTH modules (backfill and the subs
module it loads by path): the fake ffprobe returns a canned inventory / stream
counts by path shape, the fake mkvmerge writes the stripped temp it was asked
for, the fake seconv writes an SRT into its --output-folder. shutil.which is
faked so the startup probes never depend on the box.
"""

import importlib.util
import json
import os
import pathlib
import shutil

import pytest

_REPO = pathlib.Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location("backfill", _REPO / "scripts" / "backfill.py")
backfill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill)
subs = backfill.subs

INV_ENG = {"streams": [
    {"index": 3, "codec_name": "dvd_subtitle",
     "disposition": {"forced": 0}, "tags": {"language": "eng"}},
]}
INV_NOR = {"streams": [
    {"index": 2, "codec_name": "dvd_subtitle",
     "disposition": {"forced": 0}, "tags": {"language": "nor"}},
]}


class R:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def write_srt(path, cues=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i + 1:02d},000\ncue {i}\n\n"
                for i in range(1, cues + 1)),
        encoding="utf-8",
    )


def make_run(calls, *, inv=INV_ENG, seconv_rc=0, mkvmerge_rc=0,
             temp_counts="video\naudio\n", tess_langs="dan\neng\nosd\n"):
    """Fake subprocess.run dispatching on the tool basename, recording every
    invocation as (exe, cmd). ffprobe answers json inventories for originals
    and csv type counts otherwise (the stripped temp's counts = temp_counts)."""
    def run(cmd, capture_output=False, text=False, **kw):
        exe = pathlib.Path(cmd[0]).name
        calls.append((exe, list(cmd)))
        if exe == "ffprobe":
            if "-of" in cmd and cmd[cmd.index("-of") + 1] == "csv=p=0":
                counts = temp_counts if cmd[-1].endswith(".stripped.tmp.mkv") else "video\naudio\n"
                return R(0, counts, "")
            return R(0, json.dumps(inv), "")
        if exe == "mkvmerge":
            if mkvmerge_rc == 0:
                pathlib.Path(cmd[cmd.index("-o") + 1]).write_bytes(b"stripped-mkv")
            return R(mkvmerge_rc, "", "mkvmerge boom" if mkvmerge_rc else "")
        if exe == "seconv":
            out = next(a.split(":", 1)[1] for a in cmd if a.startswith("--output-folder:"))
            lang = next(a.split(":", 1)[1] for a in cmd if a.startswith("--ocr-language:"))
            if seconv_rc == 0:
                write_srt(pathlib.Path(out) / f"whatever.{lang}.srt")
            return R(seconv_rc, "", "seconv boom" if seconv_rc else "")
        if exe == "tesseract":
            return R(0, f"List of languages (3):\n{tess_langs}", "")
        raise AssertionError(f"unexpected tool: {cmd}")
    return run


@pytest.fixture
def lib(tmp_path):
    return tmp_path / "lib"


@pytest.fixture
def stub(monkeypatch):
    """Faked shutil.which + a swappable fake run installed on both modules.
    Returns the state dict: set state['run'] to swap the scenario."""
    calls: list[tuple[str, list]] = []
    state = {"calls": calls, "run": make_run(calls)}

    def run(cmd, **kw):
        return state["run"](cmd, **kw)

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(backfill.subprocess, "run", run)
    monkeypatch.setattr(subs.subprocess, "run", run)
    return state


def touch(path, content=b"orig-mkv"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def tool_names(state):
    return [exe for exe, _cmd in state["calls"]]


# -- startup probes ---------------------------------------------------------------
def test_missing_tool_exits_before_touching_library(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "seconv" else f"/fake/{name}")
    video = touch(tmp_path / "lib" / "M" / "M.mkv")
    calls: list = []
    monkeypatch.setattr(backfill.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or R(0, "", ""))
    rc = backfill.run_backfill(str(tmp_path / "lib"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "seconv" in err and "Non-Python prerequisites" in err
    # only the tesseract language check may run; no library file is ever probed
    assert [cmd for _e, cmd in calls if cmd[0].endswith("ffprobe")] == []
    assert not any(str(tmp_path) in str(cmd) for cmd in calls)
    assert not (tmp_path / "lib" / "backfill-report.txt").exists()
    assert video.read_bytes() == b"orig-mkv"


def test_tesseract_missing_dan_stops(tmp_path, stub, capsys):
    stub["run"] = make_run(stub["calls"], tess_langs="eng\nosd\n")
    touch(tmp_path / "lib" / "M" / "M.mkv")
    rc = backfill.run_backfill(str(tmp_path / "lib"))
    assert rc == 1
    assert "dan" in capsys.readouterr().err


# -- walk -------------------------------------------------------------------------
def test_extras_trees_skipped(lib, stub, capsys):
    movie = touch(lib / "Movie (2001)" / "Movie (2001).mkv")
    touch(lib / "Movie (2001)" / "extras" / "Movie (2001) - Extra 1.mkv")
    touch(lib / "TV" / "Show (2020)" / "extras" / "Show (2020)_t1.mkv")
    stub["run"] = make_run(stub["calls"], inv={"streams": []})

    rc = backfill.run_backfill(str(lib))
    assert rc == 0
    probed = [cmd[-1] for exe, cmd in stub["calls"] if exe == "ffprobe"]
    assert str(movie) in probed
    assert not any("/extras/" in p for p in probed)
    assert "skip Movie (2001).mkv (no subtitle streams)" in capsys.readouterr().out


# -- per-file dispositions ----------------------------------------------------------
def test_subless_file_skipped_fast(lib, stub, capsys):
    video = touch(lib / "M" / "M.mkv")
    stub["run"] = make_run(stub["calls"], inv={"streams": []})
    rc = backfill.run_backfill(str(lib))
    assert rc == 0
    assert "skip M.mkv (no subtitle streams)" in capsys.readouterr().out
    assert sorted(set(tool_names(stub))) == ["ffprobe", "tesseract"]  # one probe, no seconv/mkvmerge
    assert video.read_bytes() == b"orig-mkv"


def test_straggler_only_not_stripped_and_reported(lib, stub, capsys):
    video = touch(lib / "M" / "M.mkv")
    stub["run"] = make_run(stub["calls"], inv=INV_NOR)
    rc = backfill.run_backfill(str(lib))
    assert rc == 0  # skips are not failures
    assert "skip M.mkv (straggler-only: nor -- no eng/dan, never stripped)" in capsys.readouterr().out
    assert "mkvmerge" not in tool_names(stub)
    report = (lib / "backfill-report.txt").read_text(encoding="utf-8")
    assert "M.mkv" in report and "nor" in report
    assert video.read_bytes() == b"orig-mkv"


def test_done_file_is_a_noop(lib, stub, capsys):
    video = touch(lib / "M" / "M.mkv")
    write_srt(lib / "M" / "M.eng.srt")
    stub["run"] = make_run(stub["calls"], inv={"streams": []})
    rc = backfill.run_backfill(str(lib))
    assert rc == 0
    assert "skip M.mkv (already done)" in capsys.readouterr().out
    assert not set(tool_names(stub)) & {"seconv", "mkvmerge"}
    assert video.read_bytes() == b"orig-mkv"


def test_sidecars_exist_streams_remain_strips_without_ocr(lib, stub, capsys, monkeypatch):
    video = touch(lib / "M" / "M.mkv")
    write_srt(lib / "M" / "M.eng.srt")
    stub["run"] = make_run(stub["calls"])
    replaced = spy_replace(monkeypatch)
    rc = backfill.run_backfill(str(lib))
    assert rc == 0
    assert "done M.mkv (+0 sidecars)" in capsys.readouterr().out
    assert "seconv" not in tool_names(stub)
    assert video.read_bytes() == b"stripped-mkv"
    assert replaced  # os.replace carried the temp over the original
    assert not list(lib.rglob("*.tmp.mkv"))
    assert (lib / "M" / "M.eng.srt").exists()


def test_pass_failure_keeps_tracks_and_reports(lib, stub, capsys):
    video = touch(lib / "M" / "M.mkv")
    stub["run"] = make_run(stub["calls"], seconv_rc=1)
    rc = backfill.run_backfill(str(lib))
    assert rc == 1
    assert "FAIL M.mkv (eng (track 3): seconv failed) -- embedded tracks kept" in capsys.readouterr().out
    assert "mkvmerge" not in tool_names(stub)
    assert video.read_bytes() == b"orig-mkv"
    report = (lib / "backfill-report.txt").read_text(encoding="utf-8")
    assert "FAIL M.mkv" in report and "1 failed" in report


def test_strip_verify_failure_leaves_original_intact(lib, stub, capsys):
    video = touch(lib / "M" / "M.mkv")
    stub["run"] = make_run(stub["calls"], temp_counts="video\naudio\nsubtitle\n")
    rc = backfill.run_backfill(str(lib))
    assert rc == 1
    assert "FAIL M.mkv (strip verify failed: 1 subtitle stream(s) left) -- sidecars kept" \
        in capsys.readouterr().out
    assert video.read_bytes() == b"orig-mkv"
    assert not list(lib.rglob("*.tmp.mkv"))  # temp deleted
    assert (lib / "M" / "M.eng.srt").exists()  # good sidecars stay
    assert "FAIL M.mkv" in (lib / "backfill-report.txt").read_text(encoding="utf-8")


def test_mkvmerge_hard_failure_leaves_original_intact(lib, stub, capsys):
    video = touch(lib / "M" / "M.mkv")
    stub["run"] = make_run(stub["calls"], mkvmerge_rc=2)
    rc = backfill.run_backfill(str(lib))
    assert rc == 1
    assert "FAIL M.mkv (mkvmerge failed (rc=2)) -- sidecars kept" in capsys.readouterr().out
    assert video.read_bytes() == b"orig-mkv"


def test_happy_path_sidecars_beside_video_and_replace(lib, stub, capsys, monkeypatch):
    video = touch(lib / "M" / "M.mkv")
    stub["run"] = make_run(stub["calls"])
    replaced = spy_replace(monkeypatch)
    rc = backfill.run_backfill(str(lib))
    assert rc == 0
    assert "done M.mkv (+1 sidecars)" in capsys.readouterr().out
    sidecar = lib / "M" / "M.eng.srt"
    assert sidecar.exists()  # shipped beside the video
    assert sidecar.read_text(encoding="utf-8").count(" --> ") == 3
    assert video.read_bytes() == b"stripped-mkv"
    assert replaced and replaced[0][1] == str(video)
    assert "seconv" in tool_names(stub) and "mkvmerge" in tool_names(stub)
    assert "0 failed" in (lib / "backfill-report.txt").read_text(encoding="utf-8")


def spy_replace(monkeypatch):
    seen = []
    real = os.replace
    monkeypatch.setattr(backfill.os, "replace",
                        lambda a, b: seen.append((a, b)) or real(a, b))
    return seen
