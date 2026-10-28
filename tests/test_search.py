"""Tests for the ripped search (CONTEXT.md: Ripped search, Rip preflight)."""

from skive.search import (
    find_hits, folder_states, fmt_seasons, seasons_in, suggestions,
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
