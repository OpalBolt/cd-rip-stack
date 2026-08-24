"""End-to-end tests for rip_movie with mocked hardware/network."""

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from skive.rip_movie import rip_movie
from skive.tmdb import TMDBPick


@pytest.fixture(autouse=True)
def _stage_root(tmp_path, monkeypatch):
    """Keep staging off the real /mnt/videos."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))


class FakeArgs:
    """Minimal argparse.Namespace for rip_movie."""
    def __init__(
        self,
        titles=None,
        extras=False,
        part=None,
        tmdbid=None,
        verified=False,
    ):
        self.titles = titles
        self.extras = extras
        self.part = part
        self.tmdbid = tmdbid
        self.verified = verified


def test_rip_movie_refuses_recordable():
    """Test burned disc refuses without --verified."""
    args = FakeArgs(verified=False)

    with patch("skive.rip_movie.drive_media", return_value="DVD+R"):
        with patch("skive.rip_movie.ensure_mounted"):
            with patch("skive.rip_movie.purge_junk"):
                with patch("skive.rip_movie.require_online"):
                    try:
                        rip_movie(args)
                        assert False, "Should have exited"
                    except SystemExit as e:
                        assert e.code == 1


def test_rip_movie_allows_recordable_with_verified():
    """Test burned disc rips with --verified."""
    args = FakeArgs(verified=True)

    mock_pick = TMDBPick(
        title="Test Movie",
        year="2020",
        original_language="en",
        type="movie",
        id="12345",
    )

    with patch("skive.rip_movie.drive_media", return_value="DVD+R"):
        with patch("skive.rip_movie.ensure_mounted"):
            with patch("skive.rip_movie.purge_junk"):
                with patch("skive.rip_movie.require_online"):
                    with patch("skive.rip_movie.search_loop", return_value=mock_pick):
                        with patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")):
                            with patch("skive.rip_movie.rip_titles", return_value=0):
                                with patch("skive.rip_movie.rename_staged"):
                                    with patch("skive.rip_movie.subtitle_note", return_value="subs=dan langs=dan"):
                                        with patch("pathlib.Path.unlink"):
                                            try:
                                                rip_movie(args)
                                            except SystemExit as e:
                                                # Should exit with rip_titles' returncode
                                                assert e.code == 0


def test_rip_movie_no_disc():
    """Test no disc detected."""
    args = FakeArgs()

    with patch("skive.rip_movie.drive_media", return_value=""):
        with patch("skive.rip_movie.ensure_mounted"):
            try:
                rip_movie(args)
                assert False, "Should have exited"
            except SystemExit as e:
                assert e.code == 1


def test_rip_movie_tmdbid():
    """Test --tmdbid skips search."""
    args = FakeArgs(tmdbid="movie:12345")

    with patch("skive.rip_movie.drive_media", return_value="DVD-ROM"):
        with patch("skive.rip_movie.ensure_mounted"):
            with patch("skive.rip_movie.purge_junk"):
                with patch("skive.rip_movie.require_online"):
                    mock_pick = TMDBPick(
                        title="TMDB Movie",
                        year="2019",
                        original_language="en",
                        type="movie",
                        id="12345",
                    )
                    with patch("skive.rip_movie.fetch_by_id", return_value=mock_pick):
                        with patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")):
                            with patch("skive.rip_movie.rip_titles", return_value=0):
                                with patch("skive.rip_movie.rename_staged"):
                                    with patch("skive.rip_movie.subtitle_note", return_value="subs=dan"):
                                        with patch("pathlib.Path.unlink"):
                                            try:
                                                rip_movie(args)
                                            except SystemExit as e:
                                                assert e.code == 0


def test_rip_movie_extras():
    """Test --extras uses 'e' suffix."""
    args = FakeArgs(extras=True)

    mock_pick = TMDBPick(
        title="Extras Movie",
        year="2020",
        original_language="en",
        type="movie",
        id="1",
    )

    with patch("skive.rip_movie.drive_media", return_value="DVD-ROM"):
        with patch("skive.rip_movie.ensure_mounted"):
            with patch("skive.rip_movie.purge_junk"):
                with patch("skive.rip_movie.require_online"):
                    with patch("skive.rip_movie.search_loop", return_value=mock_pick):
                        with patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")):
                            with patch("skive.rip_movie.rip_titles", return_value=0):
                                with patch("skive.rip_movie.rename_staged") as mock_rename:
                                    with patch("skive.rip_movie.subtitle_note", return_value="subs=dan"):
                                        with patch("pathlib.Path.unlink"):
                                            try:
                                                rip_movie(args)
                                            except SystemExit:
                                                pass

    mock_rename.assert_called_once()
    # Check that suffix="e" was passed
    call_kwargs = mock_rename.call_args[1]
    assert call_kwargs.get("suffix") == "e"


def test_rip_movie_part():
    """Test --part adds .pN suffix."""
    args = FakeArgs(part=2)

    mock_pick = TMDBPick(
        title="Part Movie",
        year="2020",
        original_language="en",
        type="movie",
        id="1",
    )

    with patch("skive.rip_movie.drive_media", return_value="DVD-ROM"):
        with patch("skive.rip_movie.ensure_mounted"):
            with patch("skive.rip_movie.purge_junk"):
                with patch("skive.rip_movie.require_online"):
                    with patch("skive.rip_movie.search_loop", return_value=mock_pick):
                        with patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")):
                            with patch("skive.rip_movie.rip_titles", return_value=0):
                                with patch("skive.rip_movie.rename_staged") as mock_rename:
                                    with patch("skive.rip_movie.subtitle_note", return_value="subs=dan"):
                                        with patch("pathlib.Path.unlink"):
                                            try:
                                                rip_movie(args)
                                            except SystemExit:
                                                pass

    mock_rename.assert_called_once()
    call_kwargs = mock_rename.call_args[1]
    assert call_kwargs.get("part") == 2


def test_rip_movie_worst_rc_exits():
    """Test worst rc from rip_titles is used as exit code."""
    args = FakeArgs()

    mock_pick = TMDBPick(
        title="RC Movie",
        year="2020",
        original_language="en",
        type="movie",
        id="1",
    )

    with patch("skive.rip_movie.drive_media", return_value="DVD-ROM"):
        with patch("skive.rip_movie.ensure_mounted"):
            with patch("skive.rip_movie.purge_junk"):
                with patch("skive.rip_movie.require_online"):
                    with patch("skive.rip_movie.search_loop", return_value=mock_pick):
                        with patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")):
                            with patch("skive.rip_movie.rip_titles", return_value=2):
                                with patch("skive.rip_movie.rename_staged"):
                                    with patch("skive.rip_movie.subtitle_note", return_value="subs=dan"):
                                        with patch("pathlib.Path.unlink"):
                                            try:
                                                rip_movie(args)
                                            except SystemExit as e:
                                                assert e.code == 2


def test_rip_movie_rip_failure_notes():
    """Test rip failure adds note but continues to rename."""
    args = FakeArgs()

    mock_pick = TMDBPick(
        title="Note Movie",
        year="2020",
        original_language="en",
        type="movie",
        id="1",
    )

    with patch("skive.rip_movie.drive_media", return_value="DVD-ROM"):
        with patch("skive.rip_movie.ensure_mounted"):
            with patch("skive.rip_movie.purge_junk"):
                with patch("skive.rip_movie.require_online"):
                    with patch("skive.rip_movie.search_loop", return_value=mock_pick):
                        with patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")):
                            with patch("skive.rip_movie.rip_titles", return_value=1):
                                with patch("skive.rip_movie.rename_staged"):
                                    with patch("skive.rip_movie.subtitle_note", return_value="subs=dan"):
                                        with patch("pathlib.Path.unlink") as mock_unlink:
                                            with patch("skive.rip_movie.RipLog") as MockLog:
                                                # RipLog.note should be called with rip note
                                                log_instance = MockLog.return_value
                                                try:
                                                    rip_movie(args)
                                                except SystemExit:
                                                    pass
                                            # Check the rip note was made
                                            log_instance.note.assert_any_call("rip", "makemkvcon rc=1 — partial rip, renaming what completed")