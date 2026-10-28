"""Tests for rip_music.py — no network, no drive, no cyanrip, stub everything."""

import os
import subprocess
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from skive import common
from skive.rip_music import rip_music, _cyanrip_run, _newest_flac_album, _metaflac_tag, require_online
from rich.prompt import Prompt


@pytest.fixture(autouse=True)
def _stage_root(tmp_path, monkeypatch):
    """Keep staging off the real /mnt/music."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))


class FakeArgs:
    """Minimal argparse.Namespace for rip_music."""
    def __init__(self, offset=None, cyanrip_flags=None):
        self.offset = offset
        self.cyanrip_flags = cyanrip_flags or []


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
            assert rip_dir == mock_popen.call_args[1]["cwd"]
            assert mock_popen.call_args[1]["stderr"] == subprocess.STDOUT
            assert (rc, captured) == (0, "")


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
        assert capsys.readouterr().out == "Ripping... 45%\n"
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
        assert capsys.readouterr().out == captured


def test_cyanrip_run_failure_replays_captured(capsys, monkeypatch):
    """Level 0 failure: the hidden lines replay verbatim to the console."""
    monkeypatch.setattr(common, "VERBOSITY", 0)
    with tempfile.TemporaryDirectory() as tmpdir:
        process = Mock(stdout=iter(["Ripping track 1\n", "Read error\n"]))
        process.wait.return_value = 1

        with patch("subprocess.Popen", return_value=process):
            rc, captured = _cyanrip_run("100", [], Path(tmpdir))

        assert rc == 1
        assert capsys.readouterr().out == captured


def test_multiple_releases_prompts(tmp_path):
    """Test 'Multiple releases found' prompts and re-runs with -R."""
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.RipLog") as MockLog, \
            patch("skive.rip_music._newest_flac_album", return_value=("-", "-")), \
            patch("skive.rip_music.Prompt.ask", return_value="2"), \
            patch("skive.rip_music._cyanrip_run", side_effect=[
                (1, "Multiple releases found\n"),
                (0, "Rip complete\n"),
            ]) as mock_run:
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    rip_dir = tmp_path / "rip" / "cd"
    log = MockLog.return_value  # the raw log rides along to _cyanrip_run
    assert mock_run.call_args_list == [
        (("100", ["-N"], rip_dir, log),),
        (("100", ["-R", "2", "-N"], rip_dir, log),),
    ]


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
    assert capsys.readouterr().err == "skive: MusicBrainz is unreachable\n"


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
    assert "skive: interrupted" in err
    assert err.count("skive: interrupted") == 1


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
