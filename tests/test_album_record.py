"""Album validation records and the beets filing hook."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from beetsplug.skive_album import SkiveAlbumPlugin, handle_import_task_files
from skive.validation import album_document, merge_album_document


def test_album_document_shape():
    doc = album_document("bandcamp", "https://x.bandcamp.com/y",
                         [{"file": "01.flac", "dur_s": 12, "format": "FLAC"}],
                         disc_number=1, tag_run="tag1", filing_date="2025-01-02",
                         mode="as-is")
    assert list(doc) == ["kind", "source", "release", "tracks", "discs", "reconstructed"]
    assert doc["discs"] == {"1": {"tag_run": "tag1", "filed_at": "2025-01-02",
                                   "mode": "as-is"}}
    assert doc["reconstructed"] is False


def test_merge_album_document_replaces_one_disc_and_keeps_another():
    old = album_document("cd", "mb-old", [], disc_number=1, tag_run="one",
                         filing_date="2025-01-01")
    old["discs"]["2"] = {"tag_run": "two", "filed_at": "2025-01-01",
                           "mode": "autotag"}
    new = album_document("cd", "mb-new", [{"file": "02.flac", "dur_s": 20,
                                             "format": "FLAC"}], disc_number=1,
                         tag_run="replacement", filing_date="2025-01-03")
    merged = merge_album_document(old, new)
    assert merged["discs"]["1"]["tag_run"] == "replacement"
    assert merged["discs"]["2"]["tag_run"] == "two"
    assert merged["tracks"] == new["tracks"]


def _task(source: Path, destination: Path, name="01.flac"):
    destination.mkdir(parents=True, exist_ok=True)
    item_path = destination / name
    item_path.write_bytes(b"audio")
    item = SimpleNamespace(path=str(item_path), length=123.4, format="FLAC",
                           comment="https://artist.bandcamp.com/album/release")
    return SimpleNamespace(paths=[str(source)], items=[item])


def test_plugin_writes_record_and_moves_sidecars(tmp_path, monkeypatch):
    source = tmp_path / "rip" / "bandcamp" / "Album"
    destination = tmp_path / "library" / "Artist" / "Album"
    source.mkdir(parents=True)
    (source / "cyanrip.log").write_text("log")
    (source / "disc.cue").write_text("cue")
    task = _task(source, destination)
    monkeypatch.setenv("SKIVE_TAG_RUN", "tag-run")

    handle_import_task_files(task)

    doc = json.loads((destination / "validation.json").read_text())
    assert doc["source"] == "bandcamp"
    assert doc["release"] == "https://artist.bandcamp.com/album/release"
    assert doc["tracks"] == [{"file": "01.flac", "dur_s": 123, "format": "FLAC"}]
    assert doc["discs"]["1"]["tag_run"] == "tag-run"
    assert (destination / "cyanrip.log").exists()
    assert (destination / "disc.cue").exists()
    assert not (source / "cyanrip.log").exists()


def test_plugin_second_disc_keeps_first_and_suffixes_sidecar(tmp_path):
    destination = tmp_path / "library" / "Artist" / "Album"
    first = tmp_path / "rip" / "cd" / "Album-1"
    second = tmp_path / "rip" / "cd" / "Album-2"
    for source, number in ((first, "1"), (second, "2")):
        source.mkdir(parents=True)
        (source / "validation.json").write_text(json.dumps({
            "kind": "album", "source": "cd", "release": "mbid",
            "tracks": [], "discs": {number: {"rip_run": "rip-" + number}},
            "reconstructed": False,
        }))
        (source / "cyanrip.log").write_text(number)
    handle_import_task_files(_task(first, destination), tag_run="tag-1")
    handle_import_task_files(_task(second, destination, name="02.flac"), tag_run="tag-2")
    doc = json.loads((destination / "validation.json").read_text())
    assert set(doc["discs"]) == {"1", "2"}
    assert doc["discs"]["1"]["tag_run"] == "tag-1"
    assert doc["discs"]["2"]["tag_run"] == "tag-2"
    assert (destination / "cyanrip.log").exists()
    assert (destination / "cyanrip.disc2.log").exists()


def test_plugin_multiple_source_dirs_merges_discs_and_sidecars(tmp_path):
    destination = tmp_path / "library" / "Artist" / "Album"
    first = tmp_path / "rip" / "bandcamp" / "Album" / "disc1"
    second = tmp_path / "rip" / "bandcamp" / "Album" / "disc2"
    for source, number in ((first, "1"), (second, "2")):
        source.mkdir(parents=True)
        (source / "validation.json").write_text(json.dumps({
            "kind": "album", "source": "bandcamp", "release": "url",
            "tracks": [], "discs": {number: {"rip_run": "rip-" + number}},
            "reconstructed": False,
        }))
        (source / "cyanrip.log").write_text(number)
        (source / "disc.cue").write_text(number)
    task = _task(first, destination)
    second_item = SimpleNamespace(path=str(destination / "02.flac"), length=124,
                                  format="FLAC", comment="https://artist.bandcamp.com/album/release")
    second_item_path = Path(second_item.path)
    second_item_path.write_bytes(b"audio")
    task.paths = [str(first), str(second)]
    task.items.append(second_item)

    handle_import_task_files(task, tag_run="tag-run")

    doc = json.loads((destination / "validation.json").read_text())
    assert set(doc["discs"]) == {"1", "2"}
    assert doc["source"] == "bandcamp"
    assert (destination / "cyanrip.disc1.log").exists()
    assert (destination / "cyanrip.disc2.log").exists()
    assert (destination / "disc.disc1.cue").exists()
    assert (destination / "disc.disc2.cue").exists()
    assert not (first / "cyanrip.log").exists()
    assert not (second / "cyanrip.log").exists()


def test_plugin_logs_errors_without_raising():
    plugin = object.__new__(SkiveAlbumPlugin)
    plugin._log = Mock()
    plugin._import_task_files(None, SimpleNamespace(paths=[]))
    plugin._log.exception.assert_called_once()
