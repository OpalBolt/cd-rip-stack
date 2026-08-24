"""Tests for TMDB functions."""

import json
import os
from unittest.mock import Mock, patch, MagicMock

from skive.tmdb import (
    _http_get_json, fetch_by_id, parse_tmdbid, require_online, search_loop,
)

# Create mocks that won't fail with rich
mock_console = MagicMock()
mock_prompt = MagicMock()

patch_console = patch("skive.tmdb.console", mock_console)
patch_prompt = patch("skive.tmdb.Prompt", mock_prompt)


def test_http_get_json_success():
    """Test _http_get_json with a mock server."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = b'{"key": "value"}'
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = _http_get_json("http://example.com")
        assert result == {"key": "value"}


def test_http_get_json_failure():
    """Test _http_get_json on HTTP error."""
    with patch("urllib.request.urlopen", side_effect=RuntimeError("network error")):
        try:
            _http_get_json("http://example.com")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "TMDB request failed" in str(e)


def test_parse_tmdbid_bare():
    """Test parsing bare movie id."""
    kind, id_ = parse_tmdbid("123", "movie")
    assert kind == "movie"
    assert id_ == "123"


def test_parse_tmdbid_movie_prefix():
    """Test parsing movie:123."""
    kind, id_ = parse_tmdbid("movie:456", "movie")
    assert kind == "movie"
    assert id_ == "456"


def test_parse_tmdbid_url():
    """Test parsing pasted URL."""
    kind, id_ = parse_tmdbid("https://themoviedb.org/movie/789", "movie")
    assert kind == "movie"
    assert id_ == "789"


def test_parse_tmdbid_tv_bare_rejected():
    """Test TV rejects bare id."""
    try:
        parse_tmdbid("123", "tv")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "tv:N or a /tv/N URL" in str(e)


def test_parse_tmdbid_tv_url():
    """Test TV URL parsing."""
    kind, id_ = parse_tmdbid("https://themoviedb.org/tv/42", "tv")
    assert kind == "tv"
    assert id_ == "42"


def test_parse_tmdbid_wrong_type():
    """Test wrong type URL rejected."""
    try:
        parse_tmdbid("https://themoviedb.org/movie/123", "tv")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "that is a movie id" in str(e)


@patch("os.environ", {})
def test_require_online_no_key():
    """Test require_online exits when no API key."""
    with patch("sys.exit", side_effect=SystemExit):
        try:
            require_online()
            assert False, "Should have exited"
        except SystemExit:
            pass


def test_fetch_by_id_success():
    """Test fetch_by_id with mock response."""
    mock_data = {
        "title": "Test Movie",
        "release_date": "2020-01-01",
        "original_language": "en",
        "id": 12345,
    }

    with patch("skive.tmdb._http_get_json", return_value=mock_data):
        with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
            pick = fetch_by_id("movie", "12345")
            assert pick.title == "Test Movie"
            assert pick.year == "2020"
            assert pick.original_language == "en"
            assert pick.type == "movie"
            assert pick.id == "12345"


@patch_console
@patch_prompt
def test_search_loop_reprompts_empty():
    """Test search_loop re-prompts on zero results."""
    with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
        with patch("skive.tmdb._http_get_json", return_value={"results": []}):
            # side_effect: empty -> re-prompt, then q to quit
            mock_prompt.ask.side_effect = ["test", "q"]
            try:
                search_loop("movie")
                assert False, "Should have exited"
            except SystemExit:
                pass


@patch_console
@patch_prompt
def test_search_loop_pick():
    """Test search_loop picks result 1 on Enter."""
    mock_data = {
        "results": [
            {
                "title": "First Result",
                "release_date": "2019-01-01",
                "original_language": "en",
                "id": 1,
            }
        ]
    }

    with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
        with patch("skive.tmdb._http_get_json", return_value=mock_data):
            mock_prompt.ask.side_effect = ["First Result", ""]
            pick = search_loop("movie")
            assert pick.title == "First Result"
            assert pick.year == "2019"
            assert pick.original_language == "en"


@patch_prompt
def test_search_loop_q_quits():
    """Test search_loop exits on 'q'."""
    with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
        mock_prompt.ask.side_effect = ["q"]
        try:
            search_loop("movie")
            assert False, "Should have exited"
        except SystemExit as e:
            assert e.code == 1