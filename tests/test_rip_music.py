"""Tests for rip_music.py — no network, no drive, no cyanrip, stub everything."""

import os
import subprocess
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from skive import common
from skive.rip_music import (
    _cyanrip_run,
    _newest_flac_album,
    _metaflac_tag,
    _purge_pass_stubs,
    _toc_durations,
    _write_cd_record,
    require_online,
    rip_music,
)


@pytest.fixture(autouse=True)
def _stage_root(tmp_path, monkeypatch):
    """Keep staging off the real /mnt/music, and the DRV sniff off makemkv."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    monkeypatch.setattr("skive.rip_music.sniff_disc",
                        lambda disc=0: ("CD", "", "/dev/sr0"))


class FakeArgs:
    """Minimal argparse.Namespace for rip_music."""
    def __init__(self, offset=None, cyanrip_flags=None, disc=0):
        self.offset = offset
        self.cyanrip_flags = cyanrip_flags or []
        self.disc = disc


def test_cyanrip_run_cmdline():
    """Test cyanrip command line includes offset and flags."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rip_dir = Path(tmpdir)
        process = Mock(stdout=iter(()))
        process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=process) as mock_popen:
            rc, captured = _cyanrip_run("100", ["-N"], rip_dir)

            cmd = mock_popen.call_args[0][0]
            assert cmd == ["cyanrip", "-s", "100", "-N"]
            assert mock_popen.call_args[1]["cwd"] == rip_dir
            assert mock_popen.call_args[1]["stderr"] == subprocess.STDOUT
            assert (rc, captured) == (0, "")


def test_cyanrip_run_cmdline_drive():
    """The resolved device forwards to cyanrip's -d."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rip_dir = Path(tmpdir)
        process = Mock(stdout=iter(()))
        process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=process) as mock_popen:
            rc, captured = _cyanrip_run("6", [], rip_dir, drive="/dev/sr1")

            assert mock_popen.call_args[0][0] == ["cyanrip", "-s", "6", "-d", "/dev/sr1"]
            assert mock_popen.call_args[1]["cwd"] == rip_dir
            assert mock_popen.call_args[1]["stderr"] == subprocess.STDOUT
            assert (rc, captured) == (0, "")


def test_rip_music_resolves_disc_number_to_device(monkeypatch):
    """-d N uses makemkv's DRV-line device; the srN guess is only the fallback
    when the DRV line prints no path (same rule as rip movie/tv)."""
    monkeypatch.setattr("skive.rip_music.sniff_disc",
                        lambda disc=0: ("CD", "", "/dev/sr1"))
    args = FakeArgs(offset="6", disc=1)

    with patch("skive.rip_music.ensure_mounted"), \
         patch("skive.rip_music.require_online"), \
         patch("skive.rip_music._cyanrip_run", return_value=(0, "")) as mock_run, \
         patch("skive.rip_music._newest_flac_album", return_value=("-", "-")):
        try:
            rip_music(args)
        except SystemExit:
            pass
        assert mock_run.call_args.kwargs["drive"] == "/dev/sr1"

    monkeypatch.setattr("skive.rip_music.sniff_disc",
                        lambda disc=0: ("CD", "", ""))
    with patch("skive.rip_music.ensure_mounted"), \
         patch("skive.rip_music.require_online"), \
         patch("skive.rip_music._cyanrip_run", return_value=(0, "")) as mock_run, \
         patch("skive.rip_music._newest_flac_album", return_value=("-", "-")):
        try:
            rip_music(FakeArgs(offset="6", disc=1))
        except SystemExit:
            pass
        assert mock_run.call_args.kwargs["drive"] == "/dev/sr1"


def test_rip_music_claims_trailing_minus_d(monkeypatch):
    """A `-d N` typed after the offset (REMAINDER passthrough) is claimed as
    the drive number instead of reaching cyanrip, which would read the bare
    number as an image filename (2026-11-13 raw logs)."""
    sniffed = {}

    def fake_sniff(disc=0):
        sniffed["disc"] = disc
        return ("CD", "", "/dev/sr1")

    monkeypatch.setattr("skive.rip_music.sniff_disc", fake_sniff)
    args = FakeArgs(offset="6", cyanrip_flags=["-d", "1"])

    with patch("skive.rip_music.ensure_mounted"), \
         patch("skive.rip_music.require_online"), \
         patch("skive.rip_music._cyanrip_run", return_value=(0, "")) as mock_run, \
         patch("skive.rip_music._newest_flac_album", return_value=("-", "-")):
        try:
            rip_music(args)
        except SystemExit:
            pass
        assert sniffed["disc"] == 1
        assert mock_run.call_args.args[1] == []          # -d 1 no longer passed through
        assert mock_run.call_args.kwargs["drive"] == "/dev/sr1"

    # a non-numeric -d value is cyanrip's own (device path) — untouched
    args = FakeArgs(offset="6", cyanrip_flags=["-d", "/dev/sr0"], disc=1)
    with patch("skive.rip_music.ensure_mounted"), \
         patch("skive.rip_music.require_online"), \
         patch("skive.rip_music._cyanrip_run", return_value=(0, "")) as mock_run, \
         patch("skive.rip_music._newest_flac_album", return_value=("-", "-")):
        try:
            rip_music(args)
        except SystemExit:
            pass
        assert sniffed["disc"] == 1
        assert mock_run.call_args.args[1] == ["-d", "/dev/sr0"]


def test_cyanrip_run_quiet_at_default(capsys, monkeypatch):
    """Level 0: cyanrip output is captured but not streamed on success."""
    monkeypatch.setattr(common, "VERBOSITY", 0)
    with tempfile.TemporaryDirectory() as tmpdir:
        process = Mock(stdout=iter(["Ripping track 1\n", "Rip complete\n"]))
        process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=process):
            rc, captured = _cyanrip_run("100", [], Path(tmpdir))

        assert rc == 0
        assert captured == "Ripping track 1\nRip complete\n"
        assert capsys.readouterr().out == ""


def test_cyanrip_run_progress_streams_at_v1(capsys, monkeypatch):
    """Level 1: percent progress lines stream; other chatter stays hidden."""
    monkeypatch.setattr(common, "VERBOSITY", 1)
    lines = ["Track 1 of 12\n", "Ripping... 45%\n", "Rip complete\n"]
    with tempfile.TemporaryDirectory() as tmpdir:
        process = Mock(stdout=iter(lines))
        process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=process):
            rc, captured = _cyanrip_run("100", [], Path(tmpdir))

        assert rc == 0
        assert capsys.readouterr().err == "cyanrip  Ripping... 45%\n"
        assert captured == "".join(lines)  # capture is complete either way


def test_cyanrip_run_raw_passthrough_at_v2(capsys, monkeypatch):
    """Level 2: everything streams exactly as captured."""
    monkeypatch.setattr(common, "VERBOSITY", 2)
    with tempfile.TemporaryDirectory() as tmpdir:
        process = Mock(stdout=iter(["Ripping track 1\n", "Rip complete\n"]))
        process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=process):
            rc, captured = _cyanrip_run("100", [], Path(tmpdir))

        assert rc == 0
        assert capsys.readouterr().err == "cyanrip  Ripping track 1\ncyanrip  Rip complete\n"


def test_cyanrip_run_failure_replays_captured(capsys, monkeypatch):
    """Level 0 failure: the hidden lines replay verbatim to the console."""
    monkeypatch.setattr(common, "VERBOSITY", 0)
    with tempfile.TemporaryDirectory() as tmpdir:
        process = Mock(stdout=iter(["Ripping track 1\n", "Read error\n"]))
        process.wait.return_value = 1

        with patch("subprocess.Popen", return_value=process):
            rc, captured = _cyanrip_run("100", [], Path(tmpdir))

        assert rc == 1
        assert capsys.readouterr().err == "cyanrip  Ripping track 1\ncyanrip  Read error\n"


def test_require_online_uses_stdlib_head_probe():
    """Test MusicBrainz connectivity uses urllib with a five-second timeout."""
    response = MagicMock()

    with patch("urllib.request.urlopen", return_value=response) as mock_urlopen:
        require_online()

    request = mock_urlopen.call_args.args[0]
    assert request.full_url == "https://musicbrainz.org/"
    assert request.get_method() == "HEAD"
    assert mock_urlopen.call_args.kwargs["timeout"] == 5


def test_require_online_accepts_http_error_response():
    """Test any MusicBrainz HTTP response counts as online."""
    error = urllib.error.HTTPError(
        "https://musicbrainz.org/", 503, "unavailable", {}, None
    )

    with patch("urllib.request.urlopen", side_effect=error):
        require_online()


def test_require_online_exits_on_network_failure(capsys):
    """Test MusicBrainz network failure exits as offline."""
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        with pytest.raises(SystemExit) as exc:
            require_online()

    assert exc.value.code == 1
    assert capsys.readouterr().err == "fail  MusicBrainz is unreachable\n"


def test_online_gate_skipped_with_minus_N():
    """Test online gate is skipped when -N in flags."""
    with tempfile.TemporaryDirectory() as tmpdir:
        args = FakeArgs(offset="100", cyanrip_flags=["-N"])

        with patch("skive.rip_music.require_online") as mock_require:
            with patch("skive.rip_music._cyanrip_run", return_value=(0, "")):
                with patch("skive.rip_music._newest_flac_album", return_value=("-", "-")):
                    try:
                        rip_music(args)
                    except SystemExit:
                        pass
                    mock_require.assert_not_called()


def test_online_gate_without_minus_N():
    """Test online gate probes MusicBrainz without -N."""
    with tempfile.TemporaryDirectory() as tmpdir:
        args = FakeArgs(offset="100")

        with patch("skive.rip_music.ensure_mounted"):
            with patch("skive.rip_music.require_online") as mock_require:
                with patch("skive.rip_music._cyanrip_run", return_value=(0, "")):
                    with patch("skive.rip_music._newest_flac_album", return_value=("-", "-")):
                        with patch("skive.rip_music.RipLog"):
                            try:
                                rip_music(args)
                            except SystemExit:
                                pass
                        mock_require.assert_called_once()


def test_cd_record_merges_newest_log_for_each_disc(tmp_path):
    folder = tmp_path / "Album [FLAC]"
    folder.mkdir()
    log = Mock()
    log.run = "rip-1"
    first_log = folder / "Album CD1.log"
    first_log.write_text("Disc number: 1\nOffset: +10 samples\n")
    (folder / "Album CD1.cue").write_text("cue")
    os.utime(first_log, (100, 100))
    _write_cd_record(tmp_path, folder.name, "10", log)

    log.run = "rip-2"
    second_log = folder / "Album CD2.log"
    second_log.write_text("Disc number: 2\nOffset: +20 samples\n")
    (folder / "Album CD2.cue").write_text("cue")
    os.utime(second_log, (200, 200))
    _write_cd_record(tmp_path, folder.name, "20", log)

    import json
    doc = json.loads((folder / "validation.json").read_text())
    assert set(doc["discs"]) == {"1", "2"}
    assert doc["discs"]["1"]["offset"] == 10
    assert doc["discs"]["2"]["offset"] == 20


def test_cd_record_stalled_key_and_old_record_round_trip(tmp_path):
    """A holed run's disc entry carries ``stalled`` with the skipped track
    numbers; a clean run's entry has no key; a record written before the key
    existed still loads and merges unchanged."""
    import json

    folder = tmp_path / "Album"
    folder.mkdir()
    (folder / "Album.log").write_text("Offset:         +10 samples\n")
    (folder / "Album.cue").write_text("cue")
    log = Mock()
    log.run = "rip-1"

    _write_cd_record(tmp_path, "Album", "10", log, stalled=[5])
    doc = json.loads((folder / "validation.json").read_text())
    assert doc["discs"]["1"]["stalled"] == [5]

    _write_cd_record(tmp_path, "Album", "10", log)
    doc = json.loads((folder / "validation.json").read_text())
    assert "stalled" not in doc["discs"]["1"]

    # A record written before the key existed loads and merges unchanged.
    from skive.validation import (album_document, load_validation,
                                  merge_album_document, write_validation)
    old_disc = {"rip_run": "old", "offset": 10}
    old_doc = album_document("cd", None, [], discs={"2": old_disc})
    write_validation(folder / "validation.json", old_doc)
    loaded = load_validation(folder / "validation.json")
    merged = merge_album_document(loaded, doc)
    assert merged["discs"]["2"] == {"rip_run": "old", "offset": 10}


def test_rip_name_from_tags():
    """Test rip name from newest FLAC's ALBUM/ARTIST tags."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rip_dir = Path(tmpdir)

        # Create FLAC files with different timestamps
        old_flac = rip_dir / "old.flac"
        new_flac = rip_dir / "new.flac"

        old_flac.touch()
        new_flac.touch()

        # Make newest actually newer
        import time
        time.sleep(0.1)
        new_flac.touch()

        # Mock metaflac to return tags
        def mock_metaflac(flac, tag):
            if flac == new_flac and tag == "ALBUM":
                return "TestAlbum"
            if flac == new_flac and tag == "ARTIST":
                return "TestArtist"
            return ""

        with patch("skive.rip_music._metaflac_tag", side_effect=mock_metaflac):
            name, rel_folder = _newest_flac_album(rip_dir)
            assert name == "TestArtist - TestAlbum"
            assert rel_folder == "."


def test_rip_name_no_flac():
    """Test rip name is '-' when no FLAC files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        name, rel_folder = _newest_flac_album(Path(tmpdir))
        assert name == "-"
        assert rel_folder == "-"


def test_rip_name_no_tags():
    """Test rip name is '-' when tags missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rip_dir = Path(tmpdir)
        (rip_dir / "test.flac").touch()

        with patch("skive.rip_music._metaflac_tag", return_value=""):
            name, rel_folder = _newest_flac_album(rip_dir)
            assert name == "-"
            assert rel_folder == "."


def test_rip_name_album_only():
    """Test rip name is just album when no ARTIST tag."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rip_dir = Path(tmpdir)
        (rip_dir / "test.flac").touch()

        def mock_metaflac(flac, tag):
            if tag == "ALBUM":
                return "TestAlbum"
            return ""

        with patch("skive.rip_music._metaflac_tag", side_effect=mock_metaflac):
            name, rel_folder = _newest_flac_album(rip_dir)
            assert name == "TestAlbum"
            assert rel_folder == "."


def test_rip_name_in_album_subfolder():
    """Test rip name and folder when FLAC is inside an album subfolder."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rip_dir = Path(tmpdir)
        album_dir = rip_dir / "Cool Album"
        album_dir.mkdir()
        flac = album_dir / "01.flac"
        flac.touch()

        def mock_metaflac(flac_path, tag):
            if tag == "ALBUM":
                return "Cool Album"
            if tag == "ARTIST":
                return "Cool Artist"
            return ""

        with patch("skive.rip_music._metaflac_tag", side_effect=mock_metaflac):
            name, rel_folder = _newest_flac_album(rip_dir)
            assert name == "Cool Artist - Cool Album"
            assert rel_folder == "Cool Album"


def test_rip_music_logs_under_music_mount_and_notes_folder(tmp_path):
    """Test RipLog writes under MUSIC_MOUNT and notes the relative folder."""
    import json

    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    rip_cd = tmp_path / "rip" / "cd"
    album_dir = rip_cd / "My Album"
    album_dir.mkdir(parents=True)
    flac = album_dir / "track.flac"
    flac.touch()

    def mock_metaflac(flac_path, tag):
        if tag == "ALBUM":
            return "My Album"
        if tag == "ARTIST":
            return "Artist"
        return ""

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
            patch("skive.rip_music._metaflac_tag", side_effect=mock_metaflac):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    log_file = tmp_path / "logs" / "rip.jsonl"
    assert log_file.exists()
    lines = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert lines[0]["status"] == "started"
    # RipLog note for folder
    note_lines = [l for l in lines if l.get("status") == "folder: My Album"]
    assert len(note_lines) == 1
    assert note_lines[0]["phase"] == "rip"
    # Phase rip ok
    phase_lines = [l for l in lines if l.get("phase") == "rip" and l.get("status") == "ok"]
    assert len(phase_lines) == 1


def test_metaflac_tag_parser():
    """Test metaflac tag parser."""
    with tempfile.TemporaryDirectory() as tmpdir:
        flac = Path(tmpdir) / "test.flac"
        flac.touch()

        def mock_run(cmd, **kwargs):
            result = Mock()
            if "--show-tag=ALBUM" in cmd:
                result.stdout = "ALBUM=MyAlbum\n"
            elif "--show-tag=ARTIST" in cmd:
                result.stdout = "ARTIST=MyArtist\n"
            else:
                result.stdout = ""
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=mock_run):
            album = _metaflac_tag(flac, "ALBUM")
            artist = _metaflac_tag(flac, "ARTIST")
            assert album == "MyAlbum"
            assert artist == "MyArtist"


# -- Run-log name lifecycle (album header pair, decision notes) ----------------

# Genuine column-0 header shapes, from the top of
# tests/fixtures/cyanrip/summertime-blues.log.
SUMMERTIME_HEADER = [
    "cyanrip 0.9.3 (523e132)\n",
    "Album:          Summertime Blues\n",
    "Album artist:   Various Artists\n",
    "Track 1 ripped and encoded successfully!\n",
]


def _rip_jsonl(tmp_path):
    import json

    return [json.loads(line)
            for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]


def test_header_pair_names_run_on_success(tmp_path):
    """The streamed Album/Album artist pair names the run mid-rip: every
    rip.jsonl line — started, the rip ok phase line included — carries
    'Various Artists - Summertime Blues', with no FLAC scan needed."""
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    process = Mock(stdout=iter(SUMMERTIME_HEADER))
    process.wait.return_value = 0

    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    lines = _rip_jsonl(tmp_path)
    assert lines[0]["status"] == "started"
    # 'started' printed before cyanrip ran; everything after the pair
    # streamed mid-rip carries the name, the rip ok phase line included.
    assert lines[0]["name"] == "-"
    assert all(line["name"] == "Various Artists - Summertime Blues"
               for line in lines[1:])
    ok = [line for line in lines if line["phase"] == "rip" and line["status"] == "ok"]
    assert ok and ok[0]["name"] == "Various Artists - Summertime Blues"


def test_header_pair_names_failed_run_on_total_line(tmp_path):
    """A failed run keeps the header pair's name: the FAIL total line carries
    it, and staging is never scanned for a failed run."""
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    process = Mock(stdout=iter(SUMMERTIME_HEADER))
    process.wait.return_value = 1

    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 1
    lines = _rip_jsonl(tmp_path)
    total = [line for line in lines if line["phase"] == "total"]
    assert total and total[0]["status"].startswith("FAIL")
    assert total[0]["name"] == "Various Artists - Summertime Blues"


def test_header_pair_names_interrupted_run(tmp_path, monkeypatch):
    """Ctrl+C after the pair streamed: the name set mid-rip persists through
    the interrupted total line."""
    import json

    monkeypatch.setattr("skive.rip_music.kill_child_after_grace", lambda proc: None)

    def gen():
        yield from SUMMERTIME_HEADER
        raise KeyboardInterrupt

    process = Mock(stdout=gen())
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 130
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    interrupted = [line for line in lines if line["status"] == "interrupted"]
    assert interrupted and interrupted[0]["name"] == "Various Artists - Summertime Blues"


def test_album_header_without_artist_success_uses_flac_tags(tmp_path):
    """Album header with no Album artist line: on success the FLAC ARTIST tag
    names the run, exactly as when no header streamed."""
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    album_dir = tmp_path / "rip" / "cd" / "My Album"
    album_dir.mkdir(parents=True)
    (album_dir / "track.flac").touch()
    process = Mock(stdout=iter(["Album:          My Album\n",
                                "Track 1 ripped and encoded successfully!\n"]))
    process.wait.return_value = 0

    def mock_metaflac(flac_path, tag):
        return {"ALBUM": "My Album", "ARTIST": "Artist"}.get(tag, "")

    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process), \
            patch("skive.rip_music._metaflac_tag", side_effect=mock_metaflac):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    ok = [line for line in _rip_jsonl(tmp_path)
          if line["phase"] == "rip" and line["status"] == "ok"]
    assert ok and ok[0]["name"] == "Artist - My Album"


def test_album_header_without_artist_failure_keeps_album_name(tmp_path):
    """The same album-only header on a failing run keeps that album's name —
    not the previous album's name from a staging scan."""
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    stale = tmp_path / "rip" / "cd" / "Old Album"
    stale.mkdir(parents=True)
    (stale / "old.flac").touch()
    process = Mock(stdout=iter(["Album:          My Album\n", "Read error\n"]))
    process.wait.return_value = 1

    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process), \
            patch("skive.rip_music._metaflac_tag", return_value="Old Artist"):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 1
    lines = _rip_jsonl(tmp_path)
    # The album-only header never sets the name mid-rip (success would fall
    # back to the FLAC tags); post-run the failed run keeps the header album.
    assert lines[0] == {**lines[0], "status": "started", "name": "-"}
    assert all(line["name"] == "My Album" for line in lines[1:])
    assert all("Old Album" not in line["status"] for line in lines)


def test_failed_run_without_header_inherits_nothing_from_staging(tmp_path):
    """Regression: a failed run with no header keeps name '-' and folder '-'
    even when another album's tagged FLACs sit in staging — the old
    always-scan bug let the previous album lend its name."""
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    stale = tmp_path / "rip" / "cd" / "Old Album"
    stale.mkdir(parents=True)
    (stale / "old.flac").touch()
    process = Mock(stdout=iter(["Ripping track 1\n", "Read error\n"]))
    process.wait.return_value = 1

    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process), \
            patch("skive.rip_music._metaflac_tag", return_value="Old Artist"):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 1
    lines = _rip_jsonl(tmp_path)
    assert all(line["name"] == "-" for line in lines)
    folder_notes = [line["status"] for line in lines
                    if line["status"].startswith("folder:")]
    assert folder_notes == []  # nothing ripped: no folder note
    rip = [line["status"] for line in lines if line["phase"] == "rip"]
    assert rip == ["FAIL(1)"]  # never a false ok


def test_no_release_failure_notes_submit_url(tmp_path):
    """A no-release failure logs one note carrying the attach URL from that
    run's own captured output (fixture lines, nothing hard-coded)."""
    fixture = Path(__file__).parent / "fixtures" / "cyanrip" / "no-release.txt"
    out_lines = [line + "\n" for line in fixture.read_text().splitlines()]
    url = next(line for line in out_lines
               if line.startswith("https://musicbrainz.org/cdtoc/attach?")).strip()

    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    process = Mock(stdout=iter(out_lines))
    process.wait.return_value = 1

    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 1
    lines = _rip_jsonl(tmp_path)
    notes = [line["status"] for line in lines
             if "no MusicBrainz release" in line["status"]]
    assert notes == [f"no MusicBrainz release: submit this disc — {url}"]
    assert all(line["name"] == "-" for line in lines)


MULTIPLE_RELEASES = [
    "Opening drive...\n",
    "Multiple releases found in database for DiscID aBcD-:\n",
    "    1 (ID: 11111111-aaaa-bbbb-cccc-000000000001): Blue (GB) (1999-03-01)\n",
    "    2 (ID: 11111111-aaaa-bbbb-cccc-000000000002): Blue (remaster) (US) (2 CDs) (2004)\n",
    "Please specify which release to use by adding the -R argument with an index or ID.\n",
]


def _no_release_lines():
    fixture = Path(__file__).parent / "fixtures" / "cyanrip" / "no-release.txt"
    return [line + "\n" for line in fixture.read_text().splitlines()]


def test_not_found_miss_never_prompts_and_logs_rip_fail(tmp_path, capsys):
    """A not-found miss: no prompt, warn lines (plain) and the result line,
    no summary table, no replay of cyanrip's chatter, and the run log's rip
    phase ends FAIL: not on MusicBrainz — never ok, no folder note."""
    args = FakeArgs(offset="6")
    process = Mock(stdout=iter(_no_release_lines()))
    process.wait.return_value = 1

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.require_online"), \
            patch("skive.rip_music.ui.ask") as ask, \
            patch("subprocess.Popen", return_value=process):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 1
    ask.assert_not_called()
    err = capsys.readouterr().err
    assert ("warn  not on MusicBrainz: DiscID bOpGMzWaoGKqV7OC6onCHgXytfE- · 19 tracks"
            in err)
    assert "warn  Open the link, attach this disc to its release, then re-run." in err
    assert "warn  https://musicbrainz.org/cdtoc/attach?toc=1+19+354705" in err
    assert "fail  not on MusicBrainz · " in err
    assert "duration" not in err  # no summary table
    assert "cyanrip  Opening drive" not in err  # no replay
    rip = [line["status"] for line in _rip_jsonl(tmp_path) if line["phase"] == "rip"]
    assert rip[-1] == "FAIL: not on MusicBrainz"
    assert "ok" not in rip
    assert not any(status.startswith("folder:") for status in rip)


def test_not_found_miss_prints_box_and_full_url_on_a_tty(tmp_path, monkeypatch):
    """On a terminal the miss is a yellow rounded box titled `! not on
    MusicBrainz`, then the full URL unwrapped on its own line, then the
    `✗ not on MusicBrainz · <t>` result line."""
    import io
    from rich.console import Console
    from skive import ui

    buf = io.StringIO()
    monkeypatch.setattr(ui, "console", Console(
        file=buf, force_terminal=True, color_system=None, width=80, theme=ui.THEME))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    lines = _no_release_lines()
    url = next(line for line in lines if line.startswith("https://")).strip()
    process = Mock(stdout=iter(lines))
    process.wait.return_value = 1
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch("skive.rip_music.ensure_mounted"), \
                patch("skive.rip_music.require_online"), \
                patch("skive.rip_music.ui.ask") as ask, \
                patch("subprocess.Popen", return_value=process):
            with pytest.raises(SystemExit):
                rip_music(FakeArgs(offset="6"))
    finally:
        ui.clear_view()

    ask.assert_not_called()
    out = buf.getvalue()
    assert "╭─ ! not on MusicBrainz " in out
    assert "│ DiscID bOpGMzWaoGKqV7OC6onCHgXytfE- · 19 tracks · " in out
    assert "│ Open the link, attach this disc to its release, then re-run." in out
    assert f"\n{url}\n" in out  # full and unwrapped
    assert "✗ not on MusicBrainz · " in out
    assert "summary" not in out
    assert out.index("╰") < out.index(f"\n{url}\n") < out.index("✗ not on MusicBrainz")


def test_multiple_releases_miss_lists_candidates_and_rerun_command(tmp_path, capsys):
    """Several releases: no prompt, the candidates, the disc page and the
    exact -R re-run command; the run log's rip phase ends FAIL."""
    process = Mock(stdout=iter(MULTIPLE_RELEASES))
    process.wait.return_value = 1

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.require_online"), \
            patch("skive.rip_music.ui.ask") as ask, \
            patch("subprocess.Popen", return_value=process):
        with pytest.raises(SystemExit) as exc:
            rip_music(FakeArgs(offset="6"))

    assert exc.value.code == 1
    ask.assert_not_called()
    err = capsys.readouterr().err
    assert ("warn    2  Blue · remaster · US · 2 CDs · 2004 · "
            "https://musicbrainz.org/release/11111111-aaaa-bbbb-cccc-000000000002") in err
    assert "https://musicbrainz.org/cdtoc/aBcD-" in err
    assert "skive rip music 6 -- -R <n>" in err
    assert "fail  several MusicBrainz releases · " in err
    rip = [line["status"] for line in _rip_jsonl(tmp_path) if line["phase"] == "rip"]
    assert rip[-1] == "FAIL: several MusicBrainz releases"
    assert "ok" not in rip


def test_miss_rerun_command_quotes_flags_for_the_shell(tmp_path, capsys):
    """The re-run command round-trips through a shell: a flag value with a
    space stays one argument."""
    import shlex
    process = Mock(stdout=iter(MULTIPLE_RELEASES))
    process.wait.return_value = 1

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.require_online"), \
            patch("subprocess.Popen", return_value=process):
        with pytest.raises(SystemExit):
            rip_music(FakeArgs(offset="6", cyanrip_flags=["-D", "My Album"]))

    err = capsys.readouterr().err
    line = next(l for l in err.splitlines() if "skive rip music 6" in l)
    command = line[line.index("skive rip music"):line.index("<n>") + 3]
    assert shlex.split(command) == ["skive", "rip", "music", "6", "--",
                                    "-D", "My Album", "-R", "<n>"]


def test_miss_rerun_command_keeps_drive_and_drops_separator(capsys, monkeypatch):
    """A re-run typed as the habitual `6 -d 2` plus the hint's `-- -R n`:
    the leftover -- never reaches cyanrip (getopt would read -R n as an image
    filename, and the release flag is silently ignored — 2026-11-13 raw
    logs), and the hint itself keeps -d n, without which the re-run would
    open disc 0, the other drive."""
    sniffed = {}

    def fake_sniff(disc=0):
        sniffed["disc"] = disc
        return ("CD", "", "/dev/sr1")

    monkeypatch.setattr("skive.rip_music.sniff_disc", fake_sniff)
    process = Mock(stdout=iter(MULTIPLE_RELEASES))
    process.wait.return_value = 1

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.require_online"), \
            patch("subprocess.Popen", return_value=process) as mock_popen:
        with pytest.raises(SystemExit):
            rip_music(FakeArgs(offset="6", cyanrip_flags=["-d", "2", "--", "-R", "1"]))

    assert sniffed["disc"] == 2
    assert mock_popen.call_args.args[0] == ["cyanrip", "-s", "6",
                                             "-d", "/dev/sr1", "-R", "1"]
    err = capsys.readouterr().err
    assert "skive rip music -d 2 6 -- -R <n>" in err


def test_repo_sweep_no_local_paths():
    """Test no ~/media/audio paths in skive/, config/, windows/ (not lib, not README)."""
    excluded = {".git", ".venv", "uv.lock", "tests/__pycache__", "skive/__pycache__"}
    repo_root = Path(__file__).parent.parent

    for root_dir in [repo_root / "skive", repo_root / "config", repo_root / "windows"]:
        if not root_dir.exists():
            continue
        for py_file in root_dir.rglob("*.py"):
            # Skip cache
            if any(excluded_part in str(py_file) for excluded_part in excluded):
                continue
            content = py_file.read_text()
            assert "~/media" not in content, f"~/media found in {py_file}"
            assert "media/audio" not in content, f"media/audio found in {py_file}"

# -- Interrupt (Ctrl+C) (CONTEXT.md: Interrupt) --------------------------------

def _stdout_then_interrupt(lines):
    """A fake cyanrip stdout that streams lines, then the interrupt lands."""
    def gen():
        yield from lines
        raise KeyboardInterrupt
    return gen()


def test_cyanrip_run_interrupt_kills_child_and_reraises(monkeypatch):
    """Ctrl+C during the cyanrip stream: the child goes through the
    forward/kill helper and the interrupt propagates to rip_music's handler."""
    killed = []
    monkeypatch.setattr("skive.rip_music.kill_child_after_grace",
                        lambda proc: killed.append(proc))
    process = Mock(stdout=_stdout_then_interrupt(["Ripping track 1\n"]))

    with patch("subprocess.Popen", return_value=process):
        with pytest.raises(KeyboardInterrupt):
            _cyanrip_run("100", [], Path("/tmp/rip"))

    assert killed == [process]


def test_rip_music_interrupt_records_interrupted_and_exits_130(tmp_path, monkeypatch, capsys):
    """Ctrl+C mid-rip: the run log's closing line says status=interrupted (the
    finally's finish stays silent), the raw log is kept, one line, exit 130.
    Music staging has no junk labels, so there is no partial purge here."""
    import json

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", side_effect=KeyboardInterrupt):
        with pytest.raises(SystemExit) as e:
            rip_music(args)

    assert e.value.code == 130
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert [line["status"] for line in lines] == ["started", "interrupted"]
    err = capsys.readouterr().err
    assert "fail  interrupted · 0 ok" in err


def test_clean_rip_writes_cd_record_from_cyanrip_log(tmp_path):
    """A clean fake rip leaves the CD proof beside its staged FLACs."""
    import json

    album = tmp_path / "rip" / "cd" / "Album"
    album.mkdir(parents=True)
    (album / "Album.log").write_text(
        "Offset:         +123 samples\n"
        "  Track 1\n"
        "    Accurip v1:  1234 (accurately ripped, confidence 7)\n",
        encoding="utf-8",
    )
    (album / "Album.cue").write_text("FILE Album.flac\n", encoding="utf-8")
    args = FakeArgs(offset="123", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
            patch("skive.rip_music._newest_flac_album", return_value=("Album", "Album")):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)
    assert exc.value.code == 0
    doc = json.loads((album / "validation.json").read_text(encoding="utf-8"))
    assert doc["source"] == "cd"
    assert doc["discs"]["1"]["offset"] == 123
    assert doc["discs"]["1"]["accuraterip"][0]["status"] == "accurate"
    assert "stalled" not in doc["discs"]["1"]  # a clean run carries no key


def test_holed_run_records_stalled_tracks_in_disc_index(tmp_path, monkeypatch):
    """A stall-then-clean fake run (8 of 9 tracks, say) writes the validation
    file with the disc entry's ``stalled`` set to the skipped track numbers."""
    import json

    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    album = tmp_path / "rip" / "cd" / "Album"

    def fake_run(offset, flags, rip_dir, log=None, header=None, drive=None,
                 budgets=None, last_track=None):
        album.mkdir(parents=True, exist_ok=True)
        (album / "Album.log").write_text(
            "Offset:         +123 samples\n"
            "  Track 1\n"
            "    Accurip v1:  1234 (accurately ripped, confidence 7)\n",
            encoding="utf-8",
        )
        (album / "Album.cue").write_text("FILE Album.flac\n", encoding="utf-8")
        header["_tracks"] = {1: "ok", 5: "stalled"}
        header["_stalled"] = [5]
        header["album"], header["album_artist"] = "Album", "Artist"
        return 0, ""

    args = FakeArgs(offset="123", cyanrip_flags=[])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.require_online"), \
            patch("skive.rip_music._toc_durations", return_value=None), \
            patch("skive.rip_music._cyanrip_run", side_effect=fake_run), \
            patch("skive.rip_music._newest_flac_album", return_value=("Album", "Album")):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)
    assert exc.value.code == 1
    doc = json.loads((album / "validation.json").read_text(encoding="utf-8"))
    assert doc["discs"]["1"]["stalled"] == [5]
    assert doc["discs"]["1"]["accuraterip"][0]["status"] == "accurate"


def test_unparsed_cyanrip_log_keeps_record_and_raw_note(tmp_path, capsys):
    import json

    album = tmp_path / "rip" / "cd" / "Album"
    album.mkdir(parents=True)
    (album / "Album.log").write_text("unexpected cyanrip output\nfirst detail\n",
                                     encoding="utf-8")
    (album / "Album.cue").write_text("FILE Album.flac\n", encoding="utf-8")
    args = FakeArgs(offset="123", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
            patch("skive.rip_music._newest_flac_album", return_value=("Album", "Album")):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)
    assert exc.value.code == 0
    doc = json.loads((album / "validation.json").read_text(encoding="utf-8"))
    assert "accuraterip" not in doc["discs"]["1"]
    lines = (tmp_path / "logs" / "rip.jsonl").read_text(encoding="utf-8").splitlines()
    assert any("accuraterip: unparsed" in json.loads(line)["status"] for line in lines)
    run = json.loads(lines[0])["run"]
    raw = tmp_path / "logs" / "raw" / f"{run}.jsonl"
    assert raw.exists()
    assert "unexpected cyanrip output" in raw.read_text(encoding="utf-8")


# -- Born-complete staged record and validation note ----------------------------


def _fixture_report() -> str:
    """The real cyanrip report (spec: genuine output, not guesses)."""
    return (Path(__file__).parent / "fixtures" / "cyanrip"
            / "summertime-blues.log").read_text(encoding="utf-8")


def _rip_statuses(tmp_path):
    import json

    return [json.loads(line)["status"]
            for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]


def test_clean_rip_writes_born_complete_record_from_real_report(tmp_path):
    """A clean rip with the real report shape leaves a born-complete staged
    record: the parsed release, 19 tracks (file/dur_s/format) built from the
    staged FLACs, crc32 on every Accurip entry, and the validation note in
    the run log."""
    import json

    folder_name = "Summertime Blues [FLAC]"
    album = tmp_path / "rip" / "cd" / folder_name
    album.mkdir(parents=True)
    report = _fixture_report()
    (album / "Summertime Blues.log").write_text(report, encoding="utf-8")
    (album / "Summertime Blues.cue").write_text("FILE image.flac\n", encoding="utf-8")
    flac_names = [Path(line.strip()).name for line in report.splitlines()
                  if line.strip().endswith(".flac")]
    for name in flac_names:
        (album / name).touch()

    args = FakeArgs(offset="6", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
            patch("skive.rip_music._newest_flac_album",
                  return_value=("Various Artists - Summertime Blues", folder_name)):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    doc = json.loads((album / "validation.json").read_text(encoding="utf-8"))
    assert doc["release"] == "0ba5b04b-db78-46cd-95e9-9fcf1945fa23"
    assert len(doc["tracks"]) == 19
    assert all(set(track) == {"file", "dur_s", "format"} for track in doc["tracks"])
    assert doc["tracks"][0] == {
        "file": "01 - Damn Right, I've Got the Blues.flac",
        "dur_s": 269,
        "format": "flac",
    }
    accuraterip = doc["discs"]["1"]["accuraterip"]
    assert all(entry["crc32"] for entry in accuraterip)
    assert accuraterip[0]["crc32"] == "6C421A41"
    assert "validation: 19 tracks, 19 accurate (conf 3)" in _rip_statuses(tmp_path)


def test_validation_note_extends_not_found_and_partial(tmp_path):
    """A report with unmatched tracks extends the note: the not_found and
    partial counts appear only when nonzero, after the accurate count."""
    album = tmp_path / "rip" / "cd" / "Album"
    album.mkdir(parents=True)
    (album / "Album.log").write_text(
        "Offset:         +123 samples\n"
        "  Track 1\n"
        "    Accurip v1:  1234 (accurately ripped, confidence 7)\n"
        "  Track 2\n"
        "    Accurip v1:  5678 (not found)\n"
        "  Track 3\n"
        "    Accurip v1:  9abc (partially accurately ripped, confidence 2)\n",
        encoding="utf-8",
    )
    (album / "Album.cue").write_text("FILE Album.flac\n", encoding="utf-8")
    for name in ("01 - One.flac", "02 - Two.flac", "03 - Three.flac"):
        (album / name).touch()

    args = FakeArgs(offset="123", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
            patch("skive.rip_music._newest_flac_album", return_value=("Album", "Album")):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    assert ("validation: 3 tracks, 1 accurate (conf 7), 1 not_found, 1 partial"
            in _rip_statuses(tmp_path))


def test_offline_minus_N_record_lists_files_without_report_facts(tmp_path):
    """Offline -N: the report parses no release and no AccurateRip, yet the
    record is still born from what's on disk: release null, the folder's
    FLACs as tracks with null durations, and no validation note (the
    accuraterip: unparsed note covers that case)."""
    import json

    album = tmp_path / "rip" / "cd" / "Album"
    album.mkdir(parents=True)
    (album / "Album.log").write_text(
        "Offset:         +6 samples\n"
        "Album:          Unknown Album\n",
        encoding="utf-8",
    )
    (album / "Album.cue").write_text("FILE Album.flac\n", encoding="utf-8")
    (album / "01 - One.flac").touch()
    (album / "02 - Two.flac").touch()

    args = FakeArgs(offset="6", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
            patch("skive.rip_music._newest_flac_album", return_value=("Album", "Album")):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    doc = json.loads((album / "validation.json").read_text(encoding="utf-8"))
    assert doc["release"] is None
    assert doc["tracks"] == [
        {"file": "01 - One.flac", "dur_s": None, "format": "flac"},
        {"file": "02 - Two.flac", "dur_s": None, "format": "flac"},
    ]
    assert "accuraterip" not in doc["discs"]["1"]
    statuses = _rip_statuses(tmp_path)
    assert not any(status.startswith("validation:") for status in statuses)
    assert "accuraterip: unparsed" in statuses


def test_interrupted_run_writes_no_record(tmp_path):
    """Interrupt contract: the CD record is written only on rc == 0 — an
    interrupted rip leaves no validation.json beside the staged FLACs."""
    album = tmp_path / "rip" / "cd" / "Album"
    album.mkdir(parents=True)
    (album / "Album.log").write_text("Offset:         +123 samples\n", encoding="utf-8")
    (album / "Album.cue").write_text("FILE Album.flac\n", encoding="utf-8")
    (album / "01 - One.flac").touch()

    args = FakeArgs(offset="123", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", side_effect=KeyboardInterrupt):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 130
    assert not list((tmp_path / "rip" / "cd").rglob("validation.json"))


def test_tracks_pair_positionally_without_number_prefix(tmp_path):
    """A staged file with no leading 'NN - ' number pairs to its parsed
    track by position in the sorted file list."""
    import json

    album = tmp_path / "Album"
    album.mkdir()
    (album / "Album.log").write_text(
        "Offset: +10 samples\n"
        "  Track 1\n"
        "    Duration:    00:01:10\n"
        "    Accurip v1:  AAAA (accurately ripped, confidence 1)\n"
        "  Track 2\n"
        "    Duration:    00:02:20\n"
        "    Accurip v1:  BBBB (accurately ripped, confidence 1)\n",
        encoding="utf-8",
    )
    (album / "side a.flac").touch()
    (album / "side b.flac").touch()
    log = Mock()
    log.run = "rip-1"

    _write_cd_record(tmp_path, "Album", "10", log)

    doc = json.loads((album / "validation.json").read_text(encoding="utf-8"))
    assert [track["dur_s"] for track in doc["tracks"]] == [70, 140]


def test_rerip_record_rebuilds_from_newest_log(tmp_path):
    """-R re-rip: cyanrip appends per-rip logs to the album folder; each run
    rebuilds the disc entry from the newest log by mtime."""
    import json

    folder = tmp_path / "Album"
    folder.mkdir()
    old_log = folder / "Album.log"
    old_log.write_text(
        "Disc number: 1\nOffset: +10 samples\n"
        "  Track 1\n"
        "    EAC CRC32:   AAAAAAAA\n"
        "    Accurip v1:  CCCCCCCC (accurately ripped, confidence 2)\n",
        encoding="utf-8",
    )
    (folder / "Album.cue").write_text("cue\n", encoding="utf-8")
    os.utime(old_log, (100, 100))
    log = Mock()
    log.run = "rip-1"
    _write_cd_record(tmp_path, "Album", "10", log)

    new_log = folder / "Album CD1.log"
    new_log.write_text(
        "Disc number: 1\nOffset: +10 samples\n"
        "  Track 1\n"
        "    EAC CRC32:   BBBBBBBB\n"
        "    Accurip v1:  DDDDDDDD (accurately ripped, confidence 3)\n",
        encoding="utf-8",
    )
    os.utime(new_log, (200, 200))
    log.run = "rip-2"
    _write_cd_record(tmp_path, "Album", "10", log)

    doc = json.loads((folder / "validation.json").read_text(encoding="utf-8"))
    assert doc["discs"]["1"]["accuraterip"][0]["crc32"] == "BBBBBBBB"


# --- Run view wiring (slice 08: opener, headings, footer, summary) ------------


def test_cyanrip_track_heading_and_footer_plain(monkeypatch, capsys):
    """The shared run view keeps music track headings readable when piped."""
    from skive import ui

    monkeypatch.setattr(common, "VERBOSITY", 0)
    view = ui.RunView(tty=False, live=False)
    ui.set_view(view)
    process = Mock(stdout=iter([
        "Disc tracks: 2\n",
        "Track 1 ripped and encoded successfully!\n",
        "title: First song\n",
        "Track 2 ripped and encoded successfully!\n",
        "title: Second song\n",
    ]))
    process.wait.return_value = 0
    try:
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            rc, _ = _cyanrip_run("+6", [], Path(tmpdir))
    finally:
        ui.clear_view()

    assert rc == 0
    err = capsys.readouterr().err
    assert "info  1/2  First song" in err
    assert "info  2/2  Second song" in err
    assert "\\x1b" not in err


def test_cyanrip_run_tool_lines_and_footer_at_v1(monkeypatch, capsys):
    """With a seam view set at -v on a TTY: a progress line feeds the footer's
    progress but stays out of scrollback, and other lines keep their tier."""
    from skive import ui

    monkeypatch.setattr(common, "VERBOSITY", 1)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        process = Mock(stdout=iter(["Track 1 of 12\n",
                                    "Ripping and encoding track 1, progress - 45.00%\n",
                                    "Track 1 ripped and encoded successfully!\n"]))
        process.wait.return_value = 0
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            rc, captured = _cyanrip_run("100", [], Path(tmpdir))
    finally:
        ui.clear_view()

    assert rc == 0
    rendered = view.render()
    err = capsys.readouterr().err
    assert "progress - 45.00%" not in err  # TTY progress never enters scrollback
    assert "cyanrip  Track 1 ripped and encoded successfully!" not in err  # tier unchanged
    assert "100%" in rendered


def test_album_name_lands_in_status_via_header_watch(tmp_path):
    """The header pair renames the status panel mid-rip: 'ripping -' before,
    the real name once Album + Album artist have both streamed."""
    from skive import ui

    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    process = Mock(stdout=iter(SUMMERTIME_HEADER))
    process.wait.return_value = 0

    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch("skive.rip_music.ensure_mounted"), \
                patch("subprocess.Popen", return_value=process):
            with pytest.raises(SystemExit) as exc:
                rip_music(args)
    finally:
        ui.clear_view()

    assert exc.value.code == 0
    rendered = view.render()
    assert "track 1/1" in rendered


def test_album_opener_and_track_footer_use_shared_run_view(tmp_path, capsys):
    """A named album opens the run before track headings, and the footer
    carries the track position, title, offset, bar, and percent."""
    from skive import ui

    args = FakeArgs(offset="+6", cyanrip_flags=["-N"])
    process = Mock(stdout=iter([
        "Disc tracks: 2\n",
        "Album:          Test Album\n",
        "Album artist:   Test Artist\n",
        "Track 1 ripped and encoded successfully!\n",
        "title: First song\n",
        "Track 2 ripped and encoded successfully!\n",
        "title: Second song\n",
    ]))
    process.wait.return_value = 0
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch("skive.rip_music.ensure_mounted"), \
                patch("subprocess.Popen", return_value=process), \
                patch("skive.rip_music._newest_flac_album", return_value=("-", "-")):
            with pytest.raises(SystemExit) as exc:
                rip_music(args)
    finally:
        ui.clear_view()

    assert exc.value.code == 0
    err = capsys.readouterr().err
    # the opener box: title in the border, album and offset inside
    assert err.index("╭─ skive rip music ─") < err.index("│ Test Artist - Test Album ")
    assert err.index("│ offset +6 ") < err.index("╭─ 1/2  First song")
    # no progress line timed these tracks: each frame closes with its result
    # and an untimed rip phase line, never an invented 0s
    first = err[err.index("╭─ 1/2  First song"):err.index("╭─ 2/2  Second song")]
    assert "│ ✓ rip       track 1 " in first and "track 1 \u2026" not in first
    assert "╰─ ✓ done ─" in first
    second = err[err.index("╭─ 2/2  Second song"):]
    assert "│ ✓ rip       track 2 " in second
    assert "╰─ ✓ done ─" in second
    assert "done 0s" not in err
    footer = view.render()
    assert "track 2/2" in footer
    assert "Second song" in footer
    assert "offset +6" in footer
    assert "100%" in footer


def test_hidden_replay_lands_tool_prefixed_under_a_view(monkeypatch, capsys):
    """Quiet failure under a view: the hidden replay set is unchanged and
    replays through the tool prefix."""
    from skive import ui

    monkeypatch.setattr(common, "VERBOSITY", 0)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        process = Mock(stdout=iter(["Ripping track 1\n", "Read error\n"]))
        process.wait.return_value = 1
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            rc, _ = _cyanrip_run("100", [], Path(tmpdir))
    finally:
        ui.clear_view()

    assert rc == 1
    err = capsys.readouterr().err  # tool lines print to scrollback
    assert "cyanrip  Ripping track 1" in err
    assert "cyanrip  Read error" in err


def test_run_summary_rows_and_count_after_close(tmp_path, capsys):
    """The run summary: per-track rows (track, duration, result) plus the
    19-track count, printed after the view closed."""
    from skive import ui

    folder_name = "Summertime Blues [FLAC]"
    album = tmp_path / "rip" / "cd" / folder_name
    album.mkdir(parents=True)
    report = _fixture_report()
    (album / "Summertime Blues.log").write_text(report, encoding="utf-8")
    (album / "Summertime Blues.cue").write_text("FILE image.flac\n", encoding="utf-8")
    for name in [Path(line.strip()).name for line in report.splitlines()
                 if line.strip().endswith(".flac")]:
        (album / name).touch()

    args = FakeArgs(offset="6", cyanrip_flags=["-N"])
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    seen = []
    real = ui.run_summary
    monkey = pytest.MonkeyPatch()
    monkey.setattr(ui, "run_summary",
                   lambda *a, **k: seen.append(view._closed) or real(*a, **k))
    try:
        with patch("skive.rip_music.ensure_mounted"), \
                patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
                patch("skive.rip_music._newest_flac_album",
                      return_value=("Various Artists - Summertime Blues", folder_name)):
            with pytest.raises(SystemExit) as exc:
                rip_music(args)
    finally:
        monkey.undo()
        ui.clear_view()

    assert exc.value.code == 0
    assert seen == [True]  # closed before the summary printed
    err = capsys.readouterr().err
    for col in ("track", "duration", "result"):
        assert col in err
    assert any(l.strip().startswith("1") and "0:04:29" in l and "ok" in l
               for l in err.splitlines())
    assert "ok    19 ok · " in err


def test_interrupt_closes_view_and_fails_once(tmp_path, capsys):
    """Ctrl+C mid-rip under a seam view: the view closes before the fail
    line, one fail line, exit 130, no summary."""
    from skive import ui

    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch("skive.rip_music.ensure_mounted"), \
                patch("skive.rip_music._cyanrip_run", side_effect=KeyboardInterrupt):
            with pytest.raises(SystemExit) as e:
                rip_music(args)
    finally:
        ui.clear_view()

    assert e.value.code == 130
    assert view._closed
    err = capsys.readouterr().err
    assert "fail  interrupted · 0 ok" in err
    assert "track" in err  # interrupted runs still leave a summary


# --- Fix round: real stream shapes, live headings, opener, interrupt rows ----


def _fixture_track_title_lines(count):
    """The first `count` indented title lines of the real cyanrip report."""
    path = Path(__file__).parent / "fixtures" / "cyanrip" / "summertime-blues.log"
    return [line for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
            if line.strip().startswith("title:")][:count]


def test_indented_real_titles_head_every_track(monkeypatch, capsys):
    """The real report indents each track's title; every completed track gets
    its own heading (a pending track is not overwritten by the next one)."""
    from skive import ui

    monkeypatch.setattr(common, "VERBOSITY", 0)
    titles = _fixture_track_title_lines(3)
    stream = ["Disc tracks: 19\n"]
    for number, title_line in enumerate(titles, start=1):
        stream += [f"Track {number} ripped and encoded successfully!\n", title_line]
    view = ui.RunView(tty=False, live=False)
    ui.set_view(view)
    process = Mock(stdout=iter(stream))
    process.wait.return_value = 0
    try:
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            _cyanrip_run("+6", [], Path(tmpdir))
    finally:
        ui.clear_view()

    err = capsys.readouterr().err
    assert "info  1/19  Damn Right, I've Got the Blues" in err
    assert "info  2/19  The Healer" in err
    assert "info  3/19  The Thrill Is Gone" in err
    assert "1/19  track 1" not in err


def test_track_frame_carries_timed_rip_phase_line(monkeypatch, capsys):
    """A track timed by its progress lines gets the rip phase line with the
    window from its first progress line to completion, inside its frame, and
    the done rule carries the same time."""
    from skive import ui

    clock = iter([100.0, 172.0])
    monkeypatch.setattr("skive.rip_music.time.monotonic", lambda: next(clock, 172.0))
    monkeypatch.setattr(common, "VERBOSITY", 0)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    process = Mock(stdout=iter([
        "Disc tracks: 1\n",
        "Ripping and encoding track 1, progress - 10.00%, ETA - 30s\n",
        "Track 1 ripped and encoded successfully!\n",
    ]))
    process.wait.return_value = 0
    try:
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            _cyanrip_run("+6", [], Path(tmpdir))
    finally:
        ui.clear_view()

    lines = capsys.readouterr().err.splitlines()
    phase = next(n for n, line in enumerate(lines) if line.startswith("│ ✓ rip       track 1"))
    assert lines[phase].endswith(" 1m12s │")
    assert lines[phase + 1].startswith("╰─ ✓ done 1m12s ─")


def test_untitled_pending_track_closes_before_next_tracks_frame(monkeypatch, capsys):
    """Track 1 completes with no title line; track 2's progress flushes it
    first, so track 2's frame holds its own success line and done rule."""
    from skive import ui

    monkeypatch.setattr("skive.rip_music.time.monotonic", lambda: 100.0)
    monkeypatch.setattr(common, "VERBOSITY", 0)
    ui.set_view(ui.RunView(tty=True, live=False))
    process = Mock(stdout=iter([
        "Disc tracks: 2\n",
        "Track 1 ripped and encoded successfully!\n",
        "Ripping and encoding track 2, progress - 10.00%, ETA - 30s\n",
        "Track 2 ripped and encoded successfully!\n",
    ]))
    process.wait.return_value = 0
    try:
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            _cyanrip_run("+6", [], Path(tmpdir))
    finally:
        ui.clear_view()

    lines = capsys.readouterr().err.splitlines()
    frames = [line for line in lines if line[:1] in "╭╰"]
    assert frames[0].startswith("╭─ 1/2  track 1")
    assert frames[1].startswith("╰─ ✓ done ─")
    assert frames[2].startswith("╭─ 2/2  track 2")
    assert frames[3].startswith("╰─ ✓ done 0s ─") and len(frames) == 4
    phase = next(n for n, line in enumerate(lines) if line.startswith("│ ✓ rip       track 2"))
    assert lines.index(frames[2]) < phase == lines.index(frames[3]) - 1


def test_track_frame_closes_after_its_trailing_metadata(monkeypatch, capsys):
    """At -vv the metadata cyanrip prints after a track's success message
    stays inside that track's frame; the frame closes as the next one opens."""
    from skive import ui

    monkeypatch.setattr("skive.rip_music.time.monotonic", lambda: 100.0)
    monkeypatch.setattr(common, "VERBOSITY", 2)
    ui.set_view(ui.RunView(tty=True, live=False))
    process = Mock(stdout=iter([
        "Disc tracks: 2\n",
        "Ripping and encoding track 1, progress - 10.00%, ETA - 30s\n",
        "Track 1 ripped and encoded successfully!\n",
        "    title:                         The Healer\n",
        "    artist:                        B.B. King\n",
        "Ripping and encoding track 2, progress - 10.00%, ETA - 30s\n",
    ]))
    process.wait.return_value = 0
    try:
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            _cyanrip_run("+6", [], Path(tmpdir))
    finally:
        ui.clear_view()

    lines = capsys.readouterr().err.splitlines()
    success = next(n for n, line in enumerate(lines) if "Track 1 ripped and encoded" in line)
    artist = next(n for n, line in enumerate(lines) if "B.B. King" in line)
    close = next(n for n, line in enumerate(lines) if line.startswith("╰─ ✓ done"))
    second = next(n for n, line in enumerate(lines) if line.startswith("╭─ 2/2  track 2"))
    assert success < artist < close < second
    assert all(line.startswith("│") for line in lines[success:close])


def test_track_frame_plain_has_no_rip_phase_line(capsys):
    """Plain mode keeps today's track output: the heading, no phase line."""
    process = Mock(stdout=iter([
        "Disc tracks: 1\n",
        "Ripping and encoding track 1, progress - 10.00%, ETA - 30s\n",
        "Track 1 ripped and encoded successfully!\n",
    ]))
    process.wait.return_value = 0
    with tempfile.TemporaryDirectory() as tmpdir, \
            patch("subprocess.Popen", return_value=process):
        _cyanrip_run("+6", [], Path(tmpdir))
    err = capsys.readouterr().err
    assert "info  1/1  track 1" in err
    assert "rip" not in err.replace("ripped", "")


def test_progress_stream_drives_heading_and_footer_at_default_verbosity(monkeypatch, capsys):
    """The track being ripped is headed and footed from cyanrip's progress
    line at default verbosity, and the percent never enters scrollback."""
    from skive import ui

    monkeypatch.setattr(common, "VERBOSITY", 0)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    process = Mock(stdout=iter([
        "Disc tracks: 4\n",
        "Ripping and encoding track 2, progress - 50.00%, ETA - 30s\n",
    ]))
    process.wait.return_value = 1
    try:
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            _cyanrip_run("+6", [], Path(tmpdir))
        footer = view.render()
        err = capsys.readouterr().err
    finally:
        ui.clear_view()

    assert "╭─ 2/4  track 2" in err  # spill: frame header rule (console-frames slice 01)
    assert "╰─ ✗ failed: rip (rc=1) " in err  # the failed run closes the track's frame
    assert "progress - 50.00%" not in err  # not in scrollback, nor in the failure replay
    assert "track 2/4" in footer
    assert "offset +6" in footer
    assert "50%" in footer  # cyanrip's track percent, not a disc estimate


def test_album_without_artist_opens_before_track_work(monkeypatch, capsys):
    """An album-only header emits its opener at the "Tracks:" line, before the
    first heading — and survives an interrupt mid-track."""
    from skive import ui

    monkeypatch.setattr("skive.rip_music.kill_child_after_grace", lambda proc: None)

    def gen():
        yield "Disc tracks: 2\n"
        yield "Album:          Lonely Album\n"
        yield "Tracks:\n"
        yield "Ripping and encoding track 1, progress - 10.00%\n"
        raise KeyboardInterrupt

    view = ui.RunView(tty=False, live=False)
    ui.set_view(view)
    header = {}
    try:
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=Mock(stdout=gen())):
            with pytest.raises(KeyboardInterrupt):
                _cyanrip_run("+6", [], Path(tmpdir), None, header)
    finally:
        ui.clear_view()

    err = capsys.readouterr().err
    opener = "skive rip music · Lonely Album · offset +6"
    assert opener in err
    assert err.index(opener) < err.index("1/2  track 1")
    assert header["_opener_emitted"]


def test_interrupt_summary_keeps_track_outcomes_and_truthful_note(monkeypatch, capsys):
    """Ctrl+C in track 2: the summary lists track 1 ok and track 2
    interrupted, and says nothing was deleted."""
    from skive import ui

    monkeypatch.setattr("skive.rip_music.kill_child_after_grace", lambda proc: None)

    def gen():
        yield "Disc tracks: 3\n"
        yield "Ripping and encoding track 1, progress - 100.00%\n"
        yield "Track 1 ripped and encoded successfully!\n"
        yield "Ripping and encoding track 2, progress - 20.00%\n"
        raise KeyboardInterrupt

    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch("skive.rip_music.ensure_mounted"), \
                patch("subprocess.Popen", return_value=Mock(stdout=gen())):
            with pytest.raises(SystemExit) as e:
                rip_music(FakeArgs(offset="100", cyanrip_flags=["-N"]))
    finally:
        ui.clear_view()

    assert e.value.code == 130
    err = capsys.readouterr().err
    rows = [line.split() for line in err.splitlines()]
    assert ["1", "?", "ok"] in rows
    assert ["2", "?", "interrupted"] in rows
    assert "fail  interrupted · 1 ok" in err
    # track 1's frame closed done; the open track 2 frame closes before the summary
    assert err.index("╰─ ✓ done ") < err.index("╭─ 2/3  track 2")
    assert err.index("╰─ ✗ interrupted ") < err.index("fail  interrupted · 1 ok")
    assert "partial deleted" not in err
    assert "nothing deleted" in err


@pytest.mark.parametrize("line, eta", [
    ("Ripping and encoding track 1, progress - 0.01%, ETA - 303h 2m", "303h 2m"),
    ("Ripping and encoding track 1, progress - 41.50%, ETA - 1h 2m", "1h 2m"),
    ("Ripping and encoding track 1, progress - 41.50%, ETA - 27m", "27m"),
    ("Ripping and encoding track 1, progress - 41.50%, ETA - 45s", "45s"),
    ("Ripping and encoding track 1, progress - 41.50%", None),
])
def test_progress_regex_parses_disc_eta(line, eta):
    from skive.rip_music import _TRACK_PROGRESS

    match = _TRACK_PROGRESS.fullmatch(line)
    assert match and match.group(1) == "1" and match.group(3) == eta


def test_disc_eta_reaches_the_footer(monkeypatch, capsys):
    from skive import ui

    monkeypatch.setattr(common, "VERBOSITY", 0)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    process = Mock(stdout=iter([
        "Disc tracks: 16\n",
        "Ripping and encoding track 3, progress - 63.00%, ETA - 27m\n",
    ]))
    process.wait.return_value = 0
    try:
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            _cyanrip_run("+6", [], Path(tmpdir))
        footer = view.render()
    finally:
        ui.clear_view()

    assert "63%" in footer  # track 3's own percent, not ~16% of the disc
    assert "ETA 27m" in footer


def test_percent_line_that_is_not_progress_shows_on_a_tty_at_v1(monkeypatch, capsys):
    """Only whole progress lines are hidden on a TTY; another '%' line (a
    title with a percent sign) shows as a tool line at -v."""
    from skive import ui

    monkeypatch.setattr(common, "VERBOSITY", 1)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    process = Mock(stdout=iter(["    title:     100% Pure Love\n"]))
    process.wait.return_value = 0
    try:
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            _cyanrip_run("+6", [], Path(tmpdir))
    finally:
        ui.clear_view()

    assert "cyanrip      title:     100% Pure Love" in capsys.readouterr().err


def test_raw_log_throttles_progress_and_drops_unrecognised(monkeypatch):
    """13,000 progress lines for one track reach the raw log about once per
    whole percent, the track's last line kept; libmusicbrainz Unrecognised
    lines never reach it."""
    monkeypatch.setattr(common, "VERBOSITY", 0)
    progress = [f"Ripping and encoding track 1, progress - {i * 99.99 / 12999:.2f}%, ETA - 1m\n"
                for i in range(13000)]
    unrecognised = ["Unrecognised disc element: 'offset-list'\n",
                    "Unrecognised artist attribute: 'type-id'\n"]
    process = Mock(stdout=iter(unrecognised + progress))
    process.wait.return_value = 0
    log = Mock()
    with tempfile.TemporaryDirectory() as tmpdir, \
            patch("subprocess.Popen", return_value=process):
        _cyanrip_run("+6", [], Path(tmpdir), log)

    raw = [call.args[1] for call in log.raw.call_args_list]
    assert len(raw) <= 102
    assert raw[-1] == progress[-1].rstrip()  # 99.99%: same whole percent, still kept
    assert not any(line.startswith("Unrecognised") for line in raw)


# -- Stall guard (ADR 0018) ----------------------------------------------------

def test_toc_durations_parses_prepass_cue(tmp_path):
    """The cyanrip -J pre-pass runs with the same offset/drive/flags and its
    CUE yields per-track durations plus the last track's start."""
    cue = ("FILE \"X.flac\" WAVE\n"
           "  TRACK 01 AUDIO\n"
           "    INDEX 01 00:00:00\n"
           "  TRACK 02 AUDIO\n"
           "    INDEX 01 03:41:12\n")
    result = Mock(returncode=0, stdout=cue)
    with patch("skive.rip_music.subprocess.run", return_value=result) as mock_run:
        toc = _toc_durations("6", ["-R", "1"], "/dev/sr0")
    assert mock_run.call_args.args[0] == ["cyanrip", "-J", "-s", "6",
                                          "-d", "/dev/sr0", "-R", "1"]
    durations, last = toc
    assert durations == {1: 221 + 12 / 75}
    assert last == (2, 221 + 12 / 75)


def test_toc_durations_none_on_any_failure(tmp_path):
    """Nonzero rc, unparseable output or a spawn error all leave the guard
    unarmed (None)."""
    with patch("skive.rip_music.subprocess.run",
               return_value=Mock(returncode=1, stdout="")):
        assert _toc_durations("6", [], None) is None
    with patch("skive.rip_music.subprocess.run",
               return_value=Mock(returncode=0, stdout="no cue here")):
        assert _toc_durations("6", [], None) is None
    with patch("skive.rip_music.subprocess.run", side_effect=FileNotFoundError):
        assert _toc_durations("6", [], None) is None


def test_cyanrip_run_budget_trips_and_kills(monkeypatch, capsys):
    """A track past its tiny injected budget trips once: warn line, stalled
    outcome, the kill plumbing fires, and the pass fails even though the
    child exits 0."""
    import time as time_mod

    killed = []
    monkeypatch.setattr("skive.rip_music.kill_child_after_grace",
                        lambda proc: killed.append(proc))
    monkeypatch.setattr(common, "VERBOSITY", 0)

    def gen():
        yield "Ripping and encoding track 1, progress - 10%\n"
        time_mod.sleep(0.06)
        yield "Ripping and encoding track 1, progress - 20%\n"

    process = Mock(stdout=gen())
    process.wait.return_value = 0
    header: dict = {}
    with patch("subprocess.Popen", return_value=process):
        rc, _ = _cyanrip_run("6", [], Path("/tmp/rip"), header=header,
                             budgets={1: 0.05})

    assert rc == 1  # the child exited 0; the trip fails the pass
    assert header["_tracks"] == {1: "stalled"}
    assert header["_stalled"] == [1]
    assert killed == [process]
    assert "track 1 stalled" in capsys.readouterr().err


def test_cyanrip_run_total_time_arms_last_track_budget(monkeypatch, capsys):
    """The last track's budget arms from the Total time header line (total −
    last start, max(600, 3×)) moments before track 1 begins; the guard stays
    silent while within budget."""
    killed = []
    monkeypatch.setattr("skive.rip_music.kill_child_after_grace",
                        lambda proc: killed.append(proc))
    monkeypatch.setattr(common, "VERBOSITY", 0)
    process = Mock(stdout=iter([
        "Total time:     00:10:00.000\n",
        "Ripping and encoding track 2, progress - 10%\n",
    ]))
    process.wait.return_value = 0
    header: dict = {}
    budgets = {1: 600.0}
    with patch("subprocess.Popen", return_value=process):
        rc, _ = _cyanrip_run("6", [], Path("/tmp/rip"), header=header,
                             budgets=budgets, last_track=(2, 240.0))

    assert rc == 0
    assert budgets[2] == 1080.0  # max(600, 3 × (600 − 240))
    assert header["_tracks"] == {2: "interrupted"}
    assert killed == []


def test_cyanrip_run_without_budgets_never_trips(monkeypatch, capsys):
    """No budgets (pre-pass failed / -Z): the guard is inert."""
    monkeypatch.setattr(common, "VERBOSITY", 0)
    process = Mock(stdout=iter(["Ripping track 1, progress - 99%\n"]))
    process.wait.return_value = 0
    with patch("subprocess.Popen", return_value=process):
        rc, _ = _cyanrip_run("6", [], Path("/tmp/rip"))
    assert rc == 0


def test_rip_music_stalled_rows_in_summary(tmp_path, monkeypatch, capsys):
    """A tripped pass fails the run; the summary shows one row per seen
    track — ok for finished, stalled for the tripped one."""
    def fake_run(offset, flags, rip_dir, log=None, header=None, drive=None,
                 budgets=None, last_track=None):
        header["_tracks"] = {1: "ok", 2: "stalled"}
        header["_stalled"] = [2]
        header["album"], header["album_artist"] = "Album", "Artist"
        return 1, ""

    with patch("skive.rip_music.ensure_mounted"), \
         patch("skive.rip_music.require_online"), \
         patch("skive.rip_music._toc_durations", return_value=None), \
         patch("skive.rip_music._cyanrip_run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            rip_music(FakeArgs(offset="6", cyanrip_flags=[]))

    assert exc.value.code == 1
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert "stalled" in out


def test_rip_music_disarms_guard_with_z_flag(tmp_path, monkeypatch):
    """-Z anywhere in the pass-through flags skips the TOC pre-pass."""
    toc = Mock()
    with patch("skive.rip_music.ensure_mounted"), \
         patch("skive.rip_music.require_online"), \
         patch("skive.rip_music._toc_durations", toc) as mock_toc, \
         patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
         patch("skive.rip_music._newest_flac_album", return_value=("-", "-")):
        with pytest.raises(SystemExit) as exc:
            rip_music(FakeArgs(offset="6", cyanrip_flags=["-Z"]))
    assert exc.value.code == 0
    assert mock_toc.call_count == 0

def test_rip_music_stall_continues_with_l_and_holed_run_fails(tmp_path, monkeypatch, capsys):
    """After a trip the pass re-runs with -l minus the stalled track; a clean
    continuation completes with the hole — but the holed run is a loud
    failure: nonzero exit, ok/stalled summary rows, one re-rip note per hole,
    and the stalled outcome is never overwritten."""
    import json

    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    calls = []
    outcomes_seen = []

    def fake_run(offset, flags, rip_dir, log=None, header=None, drive=None,
                 budgets=None, last_track=None):
        calls.append(list(flags))
        header.setdefault("_tracks", {})[1] = "ok"
        if len(calls) == 1:
            header["_disc_tracks"] = 3
            header["_tracks"][2] = "stalled"
            header["_stalled"] = [2]
            header["album"], header["album_artist"] = "Album", "Artist"
            return 1, ""
        header["_tracks"][3] = "ok"  # the excluded track 2 is never touched
        outcomes_seen.append(dict(header["_tracks"]))
        return 0, ""

    record = [{"file": "01 - A.flac", "dur_s": 100, "format": "flac"},
              {"file": "03 - C.flac", "dur_s": 200, "format": "flac"}]
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.require_online"), \
            patch("skive.rip_music._toc_durations", return_value=None), \
            patch("skive.rip_music._cyanrip_run", side_effect=fake_run), \
            patch("skive.rip_music._newest_flac_album",
                  return_value=("Album", "Album")), \
            patch("skive.rip_music._write_cd_record", return_value=record):
        with pytest.raises(SystemExit) as exc:
            rip_music(FakeArgs(offset="6", cyanrip_flags=[]))

    assert exc.value.code == 1  # a holed run never reads as success
    assert calls[1][-2:] == ["-l", "1,3"]  # continuation excludes track 2
    assert outcomes_seen[-1] == {1: "ok", 2: "stalled", 3: "ok"}  # outcome kept
    captured = capsys.readouterr()
    assert "stalled" in captured.out + captured.err
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    notes = [line["status"] for line in lines if "hand re-rip" in line["status"]]
    assert len(notes) == 1
    assert "skive rip music 6 -- -l 2" in notes[0]
    assert "track 2 stalled" in notes[0]


def test_rip_music_three_stalls_hit_cap(tmp_path, monkeypatch, capsys):
    """A third stall stops the run: one wipe-or-replace note naming all three
    stalled tracks, exit nonzero, no fourth pass."""
    import json

    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    calls = []

    def fake_run(offset, flags, rip_dir, log=None, header=None, drive=None,
                 budgets=None, last_track=None):
        calls.append(list(flags))
        stalled = header.setdefault("_stalled", [])
        number = len(stalled) + 1
        stalled.append(number)
        header["_disc_tracks"] = 3
        header["_tracks"] = {n: "stalled" for n in stalled}
        return 1, ""

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.require_online"), \
            patch("skive.rip_music._toc_durations", return_value=None), \
            patch("skive.rip_music._cyanrip_run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            rip_music(FakeArgs(offset="6", cyanrip_flags=[]))

    assert exc.value.code == 1
    assert len(calls) == 3
    assert calls[1][-2:] == ["-l", "2,3"]
    assert calls[2][-2:] == ["-l", "3"]
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    wipe = [line["status"] for line in lines if "wipe or replace" in line["status"]]
    assert len(wipe) == 1
    for n in (1, 2, 3):
        assert str(n) in wipe[0]


def test_rip_music_continuation_own_failure_fails_run(tmp_path, monkeypatch, capsys):
    """A continuation pass failing on its own (rc!=0, no new stall) breaks to
    the existing failure path — no further passes."""
    calls = []

    def fake_run(offset, flags, rip_dir, log=None, header=None, drive=None,
                 budgets=None, last_track=None):
        calls.append(list(flags))
        if len(calls) == 1:
            header["_disc_tracks"] = 2
            header["_tracks"] = {1: "stalled"}
            header["_stalled"] = [1]
            return 1, ""
        return 1, ""  # own failure (e.g. MusicBrainz miss)

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.require_online"), \
            patch("skive.rip_music._toc_durations", return_value=None), \
            patch("skive.rip_music._cyanrip_run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            rip_music(FakeArgs(offset="6", cyanrip_flags=[]))

    assert exc.value.code == 1
    assert len(calls) == 2
    captured = capsys.readouterr()
    assert "fail" in (captured.out + captured.err).lower()


def test_purge_pass_stubs_window(tmp_path):
    """_purge_pass_stubs: window-limited stub purge (helper direct call)."""
    """Only 0-byte flac/log/cue stubs written inside the killed pass's window
    are purged; older 0-byte and nonzero files survive."""
    import os
    import time

    old = tmp_path / "old.flac"
    old.touch()
    os.utime(old, (time.time() - 100, time.time() - 100))
    full = tmp_path / "sub" / "full.flac"
    full.parent.mkdir()
    full.write_bytes(b"x")
    fresh = [tmp_path / "fresh.flac", tmp_path / "sub" / "fresh.log",
             tmp_path / "fresh.cue"]
    for path in fresh:
        path.touch()

    _purge_pass_stubs(tmp_path, time.time() - 5)

    assert old.exists() and full.exists()
    assert not any(path.exists() for path in fresh)


def test_cyanrip_run_captures_disc_tracks(monkeypatch):
    """The stream watch records the Disc tracks: count into the header dict."""
    monkeypatch.setattr(common, "VERBOSITY", 0)
    header: dict = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        process = Mock(stdout=iter(["Disc tracks: 4\n", "Rip complete\n"]))
        process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=process):
            _cyanrip_run("100", [], Path(tmpdir), header=header)

    assert header["_disc_tracks"] == 4


def test_rip_music_continuation_intersects_operator_l(tmp_path, monkeypatch):
    """The continuation pass's -l is the universe (Disc tracks:) intersected
    with the operator's own -l, minus the stalled track."""
    calls = []

    def fake_run(offset, flags, rip_dir, log=None, header=None, drive=None,
                 budgets=None, last_track=None):
        calls.append(list(flags))
        if len(calls) == 1:
            header["_disc_tracks"] = 4
            header["_tracks"] = {3: "stalled"}
            header["_stalled"] = [3]
            return 1, ""
        header["_tracks"].update({1: "ok", 4: "ok"})
        return 0, ""

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.require_online"), \
            patch("skive.rip_music._toc_durations", return_value=None), \
            patch("skive.rip_music._cyanrip_run", side_effect=fake_run), \
            patch("skive.rip_music._newest_flac_album",
                  return_value=("Album", "Album")), \
            patch("skive.rip_music._write_cd_record", return_value=[]):
        with pytest.raises(SystemExit) as exc:
            rip_music(FakeArgs(offset="6", cyanrip_flags=["-l", "2,3,4"]))

    assert exc.value.code == 1  # holed run still fails loudly
    assert calls[1][-2:] == ["-l", "2,4"]


def test_rip_music_four_track_disc_stalls_three_then_cap(tmp_path, monkeypatch):
    """On a 4-track disc, tracks 1/2/3 stalling one per pass hits the cap:
    exactly three cyanrip passes, no fourth, and the run fails with the
    wipe-or-replace note naming all three."""
    import json

    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    calls = []

    def fake_run(offset, flags, rip_dir, log=None, header=None, drive=None,
                 budgets=None, last_track=None):
        calls.append(list(flags))
        stalled = header.setdefault("_stalled", [])
        number = len(stalled) + 1
        stalled.append(number)
        header["_disc_tracks"] = 4
        header["_tracks"] = {n: "stalled" for n in stalled}
        return 1, ""

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.require_online"), \
            patch("skive.rip_music._toc_durations", return_value=None), \
            patch("skive.rip_music._cyanrip_run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            rip_music(FakeArgs(offset="6", cyanrip_flags=[]))

    assert exc.value.code == 1
    assert len(calls) == 3  # cap at three stalls — no fourth pass
    assert calls[1][-2:] == ["-l", "2,3,4"]
    assert calls[2][-2:] == ["-l", "3,4"]
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    wipe = [line["status"] for line in lines if "wipe or replace" in line["status"]]
    assert len(wipe) == 1
    for n in (1, 2, 3):
        assert str(n) in wipe[0]
