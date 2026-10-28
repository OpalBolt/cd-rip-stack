"""Tests for the scoped ripped search (CONTEXT.md: Ripped search)."""

from types import SimpleNamespace

import pytest

from skive.main import build_parser
from skive.search import (
    find_hits, find_music_hits, folder_states, fmt_seasons, run_search,
    seasons_in, suggestions,
)


def _mkv(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def test_find_hits_reports_states_in_share_order(tmp_path):
    _mkv(tmp_path / "library/movies/Fight Club (1999)/Fight Club (1999).mkv")
    _mkv(tmp_path / "rip/movies/Fight Club (1999)/Fight Club (1999)_t00.mkv")

    hits = find_hits("fight club", tmp_path)

    assert len(hits) == 1
    assert (hits[0].name, hits[0].kind) == ("Fight Club (1999)", "movie")
    assert [s for s, _ in hits[0].states] == ["staging", "library"]
    assert hits[0].states[0][1] == set()


def test_find_hits_tv_lists_seasons_per_state(tmp_path):
    base = "Downton Abbey (2010)"
    _mkv(tmp_path / f"rip/tv/{base}/{base}_S01E01.mkv")
    _mkv(tmp_path / f"rip/tv/{base}/{base}_S02E03-E04.mkv")
    _mkv(tmp_path / f"rip/tv/{base}/{base} - Extra 07.mkv")  # extras never a season
    _mkv(tmp_path / f"library/tv/{base}/Season 01/{base}_S01E01.mkv")
    _mkv(tmp_path / f"library/tv/{base}/Specials/{base}_S00E01.mkv")

    hits = find_hits("DOWNTON", tmp_path)

    assert len(hits) == 1
    states = dict(hits[0].states)
    assert states["staging"] == {1, 2}
    assert states["library"] == {0, 1}


def test_seasons_in_reads_bare_season_folders(tmp_path):
    show = tmp_path / "Show (2020)"
    (show / "Season 03").mkdir(parents=True)
    assert seasons_in(show) == {3}


def test_folder_states_exact_name_ignores_near_names_and_files(tmp_path):
    (tmp_path / "library/movies/Fight Club 1999").mkdir(parents=True)
    (tmp_path / "library/movies/Fight Club (1999)").mkdir(parents=True)
    (tmp_path / "library/movies/stray.mkv").touch()

    rows = folder_states("fight club (1999)", tmp_path, "movies")

    assert [f.name for _s, f in rows] == ["Fight Club (1999)"]


def test_zero_hits_suggest_near_miss(tmp_path):
    _mkv(tmp_path / "library/movies/Fight Club (1999)/Fight Club (1999).mkv")
    assert find_hits("fihgt club", tmp_path) == []
    assert suggestions("fihgt club", tmp_path) == ["Fight Club (1999)"]


def test_fmt_seasons_sorts_and_spells_specials():
    assert fmt_seasons({11, 0, 2}) == "Specials, S02, S11"


def test_video_scope_filters_title_kind(tmp_path):
    _mkv(tmp_path / "library/movies/Movie (2020)/movie.mkv")
    _mkv(tmp_path / "library/tv/Show (2020)/Show_S01E01.mkv")

    assert [hit.kind for hit in find_hits("", tmp_path, ("movies",))] == ["movie"]
    assert [hit.kind for hit in find_hits("", tmp_path, ("tv",))] == ["tv"]


def test_music_hits_include_staging_source_and_library_album(tmp_path):
    (tmp_path / "rip/cd/Artist - First Album").mkdir(parents=True)
    (tmp_path / "library/Artist/2024 Second Album").mkdir(parents=True)

    staging = find_music_hits("first", tmp_path)
    filed = find_music_hits("second", tmp_path)

    assert staging[0].name == "Artist / First Album"
    assert staging[0].kind == "album"
    assert staging[0].states == ["staging (cd)"]
    assert filed[0].name == "Artist / 2024 Second Album"
    assert filed[0].states == ["library"]


def test_search_music_walks_only_music_mount(tmp_path, monkeypatch, capsys):
    (tmp_path / "rip/other/Artist - Album").mkdir(parents=True)
    video = tmp_path / "videos"
    (video / "library/movies/Album (2020)").mkdir(parents=True)
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    monkeypatch.setenv("VIDEOS_MOUNT", str(video))
    monkeypatch.setattr("skive.search.ensure_mounted", lambda *_args: None)

    run_search(SimpleNamespace(scope="music", query="album"))

    output = capsys.readouterr().out
    assert "Artist / Album" in output
    assert "staging (other)" in output
    assert "Album (2020)" not in output


def test_search_mount_failure_is_one_note_and_successful_exit(monkeypatch, capsys):
    monkeypatch.setattr("skive.search.ensure_mounted", lambda *_args: (_ for _ in ()).throw(SystemExit(1)))

    run_search(SimpleNamespace(scope="music", query="album"))

    assert capsys.readouterr().out.strip() == "skive: cannot mount music share; skipping"


def test_search_scope_is_required_and_limited():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["search", "query"])
    assert parser.parse_args(["search", "music", "query"]).scope == "music"
    with pytest.raises(SystemExit):
        parser.parse_args(["search", "podcasts", "query"])
