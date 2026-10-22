"""Tests for rip_music.py — no network, no drive, no cyanrip, stub everything."""

import os
import subprocess
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

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


def test_cyanrip_run_captures_and_streams_output(capsys):
    """Test cyanrip output is captured and streamed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        process = Mock(stdout=iter(["Ripping track 1\n", "Rip complete\n"]))
        process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=process):
            rc, captured = _cyanrip_run("100", [], Path(tmpdir))

        assert rc == 0
        assert captured == "Ripping track 1\nRip complete\n"
        assert capsys.readouterr().out == captured


def test_multiple_releases_prompts(tmp_path):
    """Test 'Multiple releases found' prompts and re-runs with -R."""
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.RipLog") as MockLog, \
            patch("skive.rip_music._newest_flac_album", return_value="-"), \
            patch("skive.rip_music.Prompt.ask", return_value="2"), \
            patch("skive.rip_music._cyanrip_run", side_effect=[
                (1, "Multiple releases found\n"),
                (0, "Rip complete\n"),
            ]) as mock_run:
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    rip_dir = tmp_path / "rip"
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
                with patch("skive.rip_music._newest_flac_album", return_value="-"):
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
                    with patch("skive.rip_music._newest_flac_album", return_value="-"):
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
            name = _newest_flac_album(rip_dir)
            assert name == "TestArtist - TestAlbum"


def test_rip_name_no_flac():
    """Test rip name is '-' when no FLAC files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        name = _newest_flac_album(Path(tmpdir))
        assert name == "-"


def test_rip_name_no_tags():
    """Test rip name is '-' when tags missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rip_dir = Path(tmpdir)
        (rip_dir / "test.flac").touch()

        with patch("skive.rip_music._metaflac_tag", return_value=""):
            name = _newest_flac_album(rip_dir)
            assert name == "-"


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
            name = _newest_flac_album(rip_dir)
            assert name == "TestAlbum"


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