"""Tests for the shared movie/TV rip preamble."""

from types import SimpleNamespace
from unittest.mock import Mock

from skive import rip_common
from skive.tmdb import TMDBPick


def _mock_preamble_seams(monkeypatch, media="DVD-ROM"):
    log = Mock()
    monkeypatch.setattr(rip_common, "RipLog", Mock(return_value=log))
    monkeypatch.setattr(rip_common, "ensure_mounted", Mock())
    monkeypatch.setattr(rip_common, "scan_disc", Mock(return_value=(media, [])))
    monkeypatch.setattr(rip_common, "purge_junk", Mock())
    monkeypatch.setattr(rip_common, "require_online", Mock())
    return log


def test_movie_preamble_returns_shared_values(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    monkeypatch.setenv("RIP_MINLENGTH", "90")
    log = _mock_preamble_seams(monkeypatch)
    pick = TMDBPick("Movie/Title:", "2020", "fr", "movie", "1")
    monkeypatch.setattr(rip_common, "search_loop", Mock(return_value=pick))

    result = rip_common.rip_preamble(
        "movie", "skive rip movie", SimpleNamespace(tmdbid=None, verified=False)
    )

    assert result.display == "Movie-Title (2020)"
    assert result.minlength == 90
    assert result.keep == "fra"
    assert result.stage_movie.is_dir() and result.stage_tv.is_dir()
    rip_common.purge_junk.assert_called_once_with([result.stage_movie, result.stage_tv])
    log.start.assert_called_once_with()
    log.note.assert_called_once_with("media", "media=DVD-ROM")


def test_tv_preamble_resolves_tmdb_id_and_preserves_stage_order(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    pick = TMDBPick("Show", "-", "-", "tv", "7")
    monkeypatch.setattr(rip_common, "parse_tmdbid", Mock(return_value=("tv", "7")))
    monkeypatch.setattr(rip_common, "fetch_by_id", Mock(return_value=pick))

    result = rip_common.rip_preamble(
        "tv", "skive rip tv", SimpleNamespace(tmdbid="tv:7", verified=False)
    )

    rip_common.parse_tmdbid.assert_called_once_with("tv:7", "tv")
    rip_common.fetch_by_id.assert_called_once_with("tv", "7")
    rip_common.purge_junk.assert_called_once_with([result.stage_tv, result.stage_movie])
    assert result.display == "Show"
    assert result.keep == ""
