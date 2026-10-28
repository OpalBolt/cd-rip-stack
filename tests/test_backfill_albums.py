"""Backfill album validation records with fake mutagen readers and temp files."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location(
    "backfill_albums", _REPO / "scripts" / "backfill_albums.py")
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)


def _reader(tags_by_name):
    def read(path, easy=True):
        return SimpleNamespace(
            tags=tags_by_name[path.name],
            info=SimpleNamespace(length={"01.flac": 12.4, "03.flac": 15.6}[path.name]),
            format="FLAC",
        )
    return read


def _track(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not really audio")


def test_backfill_dry_run_apply_gap_and_existing_record(tmp_path, capsys):
    root = tmp_path / "music"
    album = root / "library" / "Artist" / "2020 Album"
    _track(album / "01.flac")
    _track(album / "03.flac")
    existing = root / "library" / "Artist" / "Existing"
    _track(existing / "01.flac")
    old = {"kind": "album", "source": "cd", "release": "keep-me"}
    (existing / "validation.json").write_text(json.dumps(old), encoding="utf-8")
    tags = {
        "01.flac": {"musicbrainz_albumid": ["release-id"],
                     "tracknumber": ["1/12"], "discnumber": ["1/1"]},
        "03.flac": {"tracknumber": ["3/12"], "discnumber": ["1/1"]},
    }
    reader = _reader(tags)
    original = bf.reader
    bf.reader = reader
    try:
        assert bf.main(["--root", str(root)]) == 0
    finally:
        bf.reader = original
    assert not (album / "validation.json").exists()
    assert json.loads((existing / "validation.json").read_text()) == old
    assert "write  library/Artist/2020 Album/validation.json: 2 of 12 tracks" in capsys.readouterr().out

    # The injected reader is the only non-filesystem dependency.
    original = bf.reader
    bf.reader = reader
    try:
        assert bf.main(["--apply", "--root", str(root)]) == 0
    finally:
        bf.reader = original

    doc = json.loads((album / "validation.json").read_text(encoding="utf-8"))
    assert doc["source"] == "unknown"
    assert doc["reconstructed"] is True
    assert doc["release"] == "release-id"
    assert doc["tracks_found"] == 2
    assert doc["tracktotal"] == 12
    assert doc["disctotal"] == 1
    assert doc["tracks"] == [
        {"file": "01.flac", "dur_s": 12, "format": "FLAC"},
        {"file": "03.flac", "dur_s": 16, "format": "FLAC"},
    ]
    assert json.loads((existing / "validation.json").read_text()) == old


def test_backfill_uses_bandcamp_url_when_no_musicbrainz_id(tmp_path):
    album = tmp_path / "library" / "Artist" / "Album"
    _track(album / "01.flac")
    fake = SimpleNamespace(
        tags={"tracknumber": ["1/1"], "discnumber": ["1/1"],
              "comment": ["https://artist.bandcamp.com/album/album"]},
        info=SimpleNamespace(length=10), format="FLAC")
    doc = bf.build_album_document(album, reader=lambda path, easy=True: fake)
    assert doc["release"] == "https://artist.bandcamp.com/album/album"


def test_backfill_accepts_mb_release_id_and_counts_wma(tmp_path):
    album = tmp_path / "library" / "Artist" / "Album"
    _track(album / "01.wma")
    fake = SimpleNamespace(
        tags={"mb_release_id": ["release-id"], "tracknumber": ["1/1"]},
        info=SimpleNamespace(length=10), format="WMA")
    doc = bf.build_album_document(album, reader=lambda path, easy=True: fake)
    assert doc["release"] == "release-id"
    assert doc["tracks_found"] == 1
    assert doc["tracks"] == [{"file": "01.wma", "dur_s": 10, "format": "WMA"}]
