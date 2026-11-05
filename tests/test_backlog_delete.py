"""Tests for scripts/backlog-delete-unmarked.py — tmp trees, no real runs.

Both sweeps over a fake $VIDEOS root: staged unmarked extras die with the
main protected; library extras die only on an explicit include=false —
the unmarked library doc is skipped loudly, not swept.
"""

import importlib.util
import pathlib
import sys

_REPO = pathlib.Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location(
    "backlog_delete", _REPO / "scripts" / "backlog-delete-unmarked.py")
backlog_delete = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backlog_delete)

from skive.validation import (VALIDATION_FILENAME, load_validation,
                              write_validation)

MAIN = "Movie (2001)_t00.mkv"
EXTRA = "Movie (2001)_t01.mkv"
MARK_FALSE = {"include": False, "quality": "superfast"}


def _staged_movie(root):
    folder = root / "rip" / "movies" / "Movie (2001)"
    folder.mkdir(parents=True)
    for name, mb in ((MAIN, 2), (EXTRA, 1)):
        (folder / name).write_bytes(b"x" * 1048576 * mb)
    write_validation(folder / VALIDATION_FILENAME, {
        "main": "t0", "extras": MARK_FALSE,
        "titles": [{"t": 0, "dur_s": 6000, "file": MAIN},
                   {"t": 1, "dur_s": 300, "file": EXTRA}]})
    return folder


def _library_movie(root, name, marker):
    folder = root / "library" / "movies" / name
    enc = folder / "extras"
    enc.mkdir(parents=True)
    for n in (1, 2):
        (enc / f"{name} - Extra 0{n}.mkv").write_bytes(b"x" * 1048576)
    write_validation(folder / VALIDATION_FILENAME,
                     {"extras": marker} if marker is not None else {})
    return folder


def _run(monkeypatch, root, *argv):
    monkeypatch.setenv("VIDEOS", str(root))
    monkeypatch.setattr(sys, "argv", ["backlog-delete-unmarked.py", *argv])
    return backlog_delete.main()


def test_rip_extra_dies_main_survives(tmp_path, monkeypatch):
    folder = _staged_movie(tmp_path)
    assert _run(monkeypatch, tmp_path) == 0  # dry-run: nothing deleted
    assert (folder / EXTRA).is_file()
    assert _run(monkeypatch, tmp_path, "--go") == 0
    assert (folder / MAIN).is_file()
    assert not (folder / EXTRA).exists()


def test_rip_include_true_keeps_everything(tmp_path, monkeypatch):
    folder = _staged_movie(tmp_path)
    doc = load_validation(folder / VALIDATION_FILENAME)
    doc["extras"] = {"include": True, "quality": "full"}
    write_validation(folder / VALIDATION_FILENAME, doc)
    assert _run(monkeypatch, tmp_path, "--go") == 0
    assert (folder / EXTRA).is_file()


def test_library_sweep_only_explicit_false_dies(tmp_path, monkeypatch):
    (tmp_path / "rip" / "movies").mkdir(parents=True)  # empty staging
    retracted = _library_movie(tmp_path, "A (2001)", MARK_FALSE)
    kept = _library_movie(tmp_path, "B (2001)", {"include": True, "quality": "full"})
    unmarked = _library_movie(tmp_path, "C (2001)", None)
    assert _run(monkeypatch, tmp_path, "--go") == 0
    assert not (retracted / "extras").exists()
    for folder in (kept, unmarked):
        assert len(list((folder / "extras").iterdir())) == 2
