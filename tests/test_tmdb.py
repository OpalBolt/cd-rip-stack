"""Tests for TMDB functions."""

import json
import os
import urllib.error
from unittest.mock import Mock, patch, MagicMock

import pytest

from skive.tmdb import (
    _http_get_json, fetch_by_id, fetch_season, parse_tmdbid, require_online,
    search_loop,
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


def test_http_get_json_failure_scrubs_api_key():
    """Test _http_get_json scrubs the API key from network errors."""
    error = urllib.error.URLError("https://example.com?api_key=secret")
    with patch.dict(os.environ, {"TMDB_API_KEY": "secret"}, clear=False):
        with patch("urllib.request.urlopen", side_effect=error):
            with pytest.raises(RuntimeError) as exc:
                _http_get_json("https://example.com?api_key=secret")

    assert "TMDB request failed" in str(exc.value)
    assert "secret" not in str(exc.value)
    assert "api_key=***" in str(exc.value)


def test_http_get_json_does_not_wrap_unexpected_errors():
    """Test _http_get_json only wraps expected request and decoding errors."""
    with patch("urllib.request.urlopen", side_effect=RuntimeError("unexpected")):
        with pytest.raises(RuntimeError, match="^unexpected$"):
            _http_get_json("http://example.com")


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


def test_fetch_by_id_movie_runtime_secs():
    """A movie by-id pick converts the details payload's runtime (minutes)
    to seconds — the payload is already in hand, no second fetch."""
    mock_data = {"title": "Mamma Mia!", "release_date": "2008-06-30",
                 "original_language": "en", "id": 471, "runtime": 108}
    with patch("skive.tmdb._http_get_json", return_value=mock_data) as mock_http:
        with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
            pick = fetch_by_id("movie", "471")
    assert pick.runtime_secs == 6480
    assert mock_http.call_count == 1


def test_fetch_by_id_movie_missing_runtime_is_none():
    """A movie details payload without runtime degrades to unknown (None),
    never a fake 0 (the tv_match.audit_cell no-false-yellow rule)."""
    mock_data = {"title": "Mystery Film", "release_date": "2010-01-01",
                 "original_language": "de", "id": 99}
    with patch("skive.tmdb._http_get_json", return_value=mock_data):
        with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
            pick = fetch_by_id("movie", "99")
    assert pick.runtime_secs is None


def test_fetch_by_id_tv_has_no_runtime():
    """TV picks stay runtime-less: episode runtimes arrive at season level."""
    mock_data = {"name": "Some Show", "first_air_date": "2020-01-01",
                 "original_language": "en", "id": 7}
    with patch("skive.tmdb._http_get_json", return_value=mock_data):
        with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
            pick = fetch_by_id("tv", "7")
    assert pick.type == "tv"
    assert pick.runtime_secs is None


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
            # details payload (same mock) carries no runtime -> unknown
            assert pick.runtime_secs is None


@patch_console
@patch_prompt
def test_search_loop_movie_resolves_runtime_from_details():
    """Search results lack runtime; a movie pick fetches the details endpoint
    after selection and carries the runtime in seconds."""
    search = {"results": [{"title": "Mamma Mia!", "release_date": "2008-06-30",
                           "original_language": "en", "id": 471}]}
    details = {"runtime": 108, "id": 471}
    with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
        with patch("skive.tmdb._http_get_json", side_effect=[search, details]) as mock_http:
            mock_prompt.ask.side_effect = ["Mamma Mia!", ""]
            pick = search_loop("movie")
    assert pick.title == "Mamma Mia!"
    assert pick.runtime_secs == 108 * 60
    assert "movie/471?" in mock_http.call_args_list[1].args[0]


@patch_console
@patch_prompt
def test_search_loop_movie_runtime_fetch_failure_still_picks():
    """A failed details fetch never kills the pick — runtime degrades to None
    (the pick already succeeded; runtime is an enhancement)."""
    search = {"results": [{"title": "Mamma Mia!", "release_date": "2008-06-30",
                           "original_language": "en", "id": 471}]}
    with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
        with patch("skive.tmdb._http_get_json",
                   side_effect=[search, RuntimeError("TMDB request failed: HTTP 500")]):
            mock_prompt.ask.side_effect = ["Mamma Mia!", ""]
            pick = search_loop("movie")
    assert pick.title == "Mamma Mia!"
    assert pick.runtime_secs is None


@patch_console
@patch_prompt
def test_search_loop_movie_missing_runtime_is_none():
    """A movie whose details carry no runtime degrades to unknown (None),
    never a fake 0 that downstream would read as a real duration."""
    search = {"results": [{"title": "Mystery Film", "release_date": "2010-01-01",
                           "original_language": "de", "id": 99}]}
    details = {"runtime": None, "id": 99}
    with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
        with patch("skive.tmdb._http_get_json", side_effect=[search, details]):
            mock_prompt.ask.side_effect = ["Mystery Film", ""]
            pick = search_loop("movie")
    assert pick.runtime_secs is None


@patch_console
@patch_prompt
def test_search_loop_tv_makes_no_runtime_fetch():
    """TV picks are untouched: no runtime resolution, no follow-up fetch
    (episode runtimes already arrive at season level via fetch_season)."""
    search = {"results": [{"name": "Some Show", "first_air_date": "2020-01-01",
                           "original_language": "en", "id": 7}]}
    with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
        with patch("skive.tmdb._http_get_json", side_effect=[search]) as mock_http:
            mock_prompt.ask.side_effect = ["Some Show", ""]
            pick = search_loop("tv")
    assert pick.type == "tv"
    assert pick.runtime_secs is None
    assert mock_http.call_count == 1


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

@patch_console
@patch_prompt
def test_search_loop_prefill_skips_query_prompt():
    """Prefill runs the search directly; only the pick prompt is asked."""
    mock_data = {"results": [{"title": "Rogue One", "release_date": "2016-12-14",
                              "original_language": "en", "id": 330459}]}
    with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
        with patch("skive.tmdb._http_get_json", return_value=mock_data) as mock_http:
            mock_prompt.ask.reset_mock()
            mock_prompt.ask.side_effect = [""]  # Enter = pick 1
            pick = search_loop("movie", prefill="ROGUE ONE")
            assert pick.title == "Rogue One"
            assert mock_prompt.ask.call_count == 1
            assert "query=ROGUE%20ONE" in mock_http.call_args_list[0].args[0]


@patch_console
@patch_prompt
def test_search_loop_s_drops_to_typed_query():
    """'s' at the table returns to a typed search."""
    mock_data = {"results": [{"title": "Alien", "release_date": "1979-05-25",
                              "original_language": "en", "id": 348}]}
    with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
        with patch("skive.tmdb._http_get_json", return_value=mock_data) as mock_http:
            mock_prompt.ask.side_effect = ["s", "typed query", ""]
            pick = search_loop("movie", prefill="ALIEN")
            assert pick.title == "Alien"
            assert "query=typed%20query" in mock_http.call_args_list[1].args[0]
            # two searches + the pick's runtime details fetch
            assert mock_http.call_count == 3


@patch_console
@patch_prompt
def test_search_loop_prefill_zero_results_prompts_typed():
    """A prefilled query with no matches falls back to the typed prompt."""
    empty = {"results": []}
    hit = {"results": [{"title": "Alien", "release_date": "1979-05-25",
                        "original_language": "en", "id": 348}]}
    # third payload: the picked movie's runtime details fetch
    with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
        with patch("skive.tmdb._http_get_json", side_effect=[empty, hit, {"id": 348}]):
            mock_prompt.ask.side_effect = ["typed", ""]
            pick = search_loop("movie", prefill="GARBAGE_LABEL")
            assert pick.title == "Alien"


def test_fetch_season_returns_seconds():
    """TMDB reports minutes; the matcher works in seconds (mixed units once
    produced an all-extras table on Generation Kill)."""
    mock_data = {"episodes": [
        {"episode_number": 1, "name": "Get Some", "runtime": 66},
        {"episode_number": 2, "name": "The Cradle of Civilization", "runtime": None},
    ]}
    with patch.dict(os.environ, {"TMDB_API_KEY": "testkey"}, clear=False):
        with patch("skive.tmdb._http_get_json", return_value=mock_data):
            eps = fetch_season("1432", 1)
    assert eps == [(1, "Get Some", 66 * 60), (2, "The Cradle of Civilization", 0)]
