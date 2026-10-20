"""Tests for the shared movie/TV rip preamble."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from skive import rip_common
from skive.makemkv import TitleInfo
from skive.tmdb import TMDBPick


def _mock_preamble_seams(monkeypatch, media="DVD-ROM"):
    log = Mock()
    monkeypatch.setattr(rip_common, "RipLog", Mock(return_value=log))
    monkeypatch.setattr(rip_common, "ensure_mounted", Mock())
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=(media, "")))
    # Default: the fast scan fails, so tests land on the full-scan fallback
    # exactly like the pre-fast-scan flow. Fast-path tests override this.
    monkeypatch.setattr(rip_common, "fast_scan_dvd",
                        Mock(side_effect=RuntimeError("tests force the full-scan fallback")))
    monkeypatch.setattr(rip_common, "fast_scan_bluray",
                        Mock(side_effect=RuntimeError("tests force the full-scan fallback")))
    monkeypatch.setattr(rip_common, "scan_disc", Mock(return_value=(media, "", [])))
    monkeypatch.setattr(rip_common, "purge_junk", Mock())
    monkeypatch.setattr(rip_common, "require_online", Mock())
    return log


def _tv_args(**kw):
    """TV args shaped for the fast path: no -T, no --extras."""
    fields = dict(tmdbid=None, verified=False, titles=None, extras=False)
    fields.update(kw)
    return SimpleNamespace(**fields)


def _mock_search(monkeypatch, pick=None):
    """Keep the TMDB search loop out of preamble tests."""
    monkeypatch.setattr(rip_common, "search_loop",
                        Mock(return_value=pick or TMDBPick("Show", "-", "-", "tv", "7")))


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
    rip_common.search_loop.assert_called_once_with("movie", prefill="")


def test_preamble_passes_volume_label_as_prefill(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    rip_common.scan_disc.return_value = ("DVD-ROM", "ROGUE ONE", [])
    search = Mock(return_value=TMDBPick("Rogue One", "2016", "en", "movie", "330459"))
    monkeypatch.setattr(rip_common, "search_loop", search)

    rip_common.rip_preamble(
        "movie", "skive rip movie", SimpleNamespace(tmdbid=None, verified=False)
    )

    search.assert_called_once_with("movie", prefill="ROGUE ONE")


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


# --- DVD fast-scan gate (ADR 0002, DVD half) ---

GK_DURATIONS = [3932, 3897, 3653, 11482]


def test_tv_dvd_fast_scan_synthesizes_titles(tmp_path, monkeypatch):
    """TV + DVD + no flags: lsdvd durations become 0-based TitleInfos, size 0."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=("DVD-ROM", "GENERATION KILL")))
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=GK_DURATIONS))
    monkeypatch.setattr(rip_common, "search_loop",
                        Mock(return_value=TMDBPick("Show", "-", "-", "tv", "7")))

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    rip_common.fast_scan_dvd.assert_called_once_with(0)
    rip_common.scan_disc.assert_not_called()
    assert result.fast_scan is True
    assert result.scan_durations == GK_DURATIONS
    assert [(t.number, t.seconds) for t in result.titles_full] == list(enumerate(GK_DURATIONS))
    assert all(t.size == 0 and not t.audio and not t.subtitles and not t.scrambled
               for t in result.titles_full)


def test_tv_dvd_fast_scan_failure_falls_back_to_full_scan(tmp_path, monkeypatch, capsys):
    """A RuntimeError from the fast scan warns once and runs the full scan."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    log = _mock_preamble_seams(monkeypatch)
    full_titles = [TitleInfo(0, 3932, 100, "dan", "", False)]
    rip_common.scan_disc.return_value = ("DVD-ROM", "GENERATION KILL", full_titles)
    _mock_search(monkeypatch)

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    assert result.fast_scan is False
    assert result.scan_durations is None
    assert result.titles_full == full_titles
    rip_common.scan_disc.assert_called_once()
    assert "fast scan failed" in capsys.readouterr().err
    assert any("full-scan fallback" in str(c.args) for c in log.note.call_args_list)


def test_tv_dvd_fast_scan_ambiguous_durations_escalate_to_full_scan(tmp_path, monkeypatch, capsys):
    """Duplicate-duration fast scans (ADR 0002) discard the fast result and
    escalate to the full MakeMKV scan — same path as the RuntimeError fallback."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    log = _mock_preamble_seams(monkeypatch)
    ambiguous_durations = GK_DURATIONS + [3933]  # 3932 duplicate within ±1s
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=ambiguous_durations))
    full_titles = [TitleInfo(0, 3932, 100, "dan", "", False), TitleInfo(1, 3933, 90, "dan", "", False)]
    rip_common.scan_disc.return_value = ("DVD-ROM", "GENERATION KILL", full_titles)
    _mock_search(monkeypatch)

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    rip_common.fast_scan_dvd.assert_called_once_with(0)
    rip_common.scan_disc.assert_called_once()
    assert result.fast_scan is False
    assert result.scan_durations is None
    assert result.titles_full == full_titles
    assert "escalating to the full MakeMKV scan" in capsys.readouterr().err
    assert any("full-scan escalation" in str(c.args) for c in log.note.call_args_list)


# --- Blu-ray fast-scan gate (ADR 0002, BD half) ---


def test_tv_bd_fast_scan_synthesizes_titles(tmp_path, monkeypatch):
    """TV + pressed BD + no flags: ffprobe bluray durations become 0-based
    TitleInfos via fast_scan_bluray — never the DVD scanner."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=("BD-ROM", "SOME SHOW")))
    monkeypatch.setattr(rip_common, "fast_scan_bluray", Mock(return_value=GK_DURATIONS))
    _mock_search(monkeypatch)

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    rip_common.fast_scan_bluray.assert_called_once_with(0)
    rip_common.fast_scan_dvd.assert_not_called()
    rip_common.scan_disc.assert_not_called()
    assert result.fast_scan is True
    assert result.scan_durations == GK_DURATIONS
    assert [(t.number, t.seconds) for t in result.titles_full] == list(enumerate(GK_DURATIONS))


def test_tv_bd_fast_scan_ambiguous_durations_escalate_to_full_scan(tmp_path, monkeypatch, capsys):
    """Duplicate-duration BD fast scans (ADR 0002) escalate to the full scan
    via the same shared path as the DVD fast scan."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    log = _mock_preamble_seams(monkeypatch)
    ambiguous_durations = GK_DURATIONS + [3933]  # 3932 duplicate within ±1s
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=("BD-ROM", "SOME SHOW")))
    monkeypatch.setattr(rip_common, "fast_scan_bluray", Mock(return_value=ambiguous_durations))
    full_titles = [TitleInfo(0, 3932, 100, "dan", "", False), TitleInfo(1, 3933, 90, "dan", "", False)]
    rip_common.scan_disc.return_value = ("BD-ROM", "SOME SHOW", full_titles)
    _mock_search(monkeypatch)

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    rip_common.fast_scan_bluray.assert_called_once_with(0)
    rip_common.scan_disc.assert_called_once()
    assert result.fast_scan is False
    assert result.scan_durations is None
    assert result.titles_full == full_titles
    assert "escalating to the full MakeMKV scan" in capsys.readouterr().err
    assert any("full-scan escalation" in str(c.args) for c in log.note.call_args_list)


def test_tv_bd_fast_scan_failure_falls_back_to_full_scan(tmp_path, monkeypatch, capsys):
    """A RuntimeError from the BD fast scan warns once and runs the full scan."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=("BD-ROM", "SOME SHOW")))
    full_titles = [TitleInfo(0, 3932, 100, "dan", "", False)]
    rip_common.scan_disc.return_value = ("BD-ROM", "SOME SHOW", full_titles)
    _mock_search(monkeypatch)

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    rip_common.fast_scan_bluray.assert_called_once_with(0)
    assert result.fast_scan is False
    assert result.scan_durations is None
    assert result.titles_full == full_titles
    rip_common.scan_disc.assert_called_once()
    assert "fast scan failed" in capsys.readouterr().err


def test_preamble_movies_never_fast_scan(tmp_path, monkeypatch):
    """Movies stay on the full scan even on a pressed DVD."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    _mock_search(monkeypatch)
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=GK_DURATIONS))

    result = rip_common.rip_preamble(
        "movie", "skive rip movie", SimpleNamespace(tmdbid=None, verified=False))

    rip_common.fast_scan_dvd.assert_not_called()
    rip_common.scan_disc.assert_called_once()
    assert result.fast_scan is False


def test_preamble_movie_bd_never_fast_scan(tmp_path, monkeypatch):
    """Movies stay on the full scan even on a pressed Blu-ray."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch, media="BD-ROM")
    _mock_search(monkeypatch)

    result = rip_common.rip_preamble(
        "movie", "skive rip movie", SimpleNamespace(tmdbid=None, verified=False))

    rip_common.fast_scan_bluray.assert_not_called()
    rip_common.scan_disc.assert_called_once()
    assert result.fast_scan is False


def test_preamble_titles_flag_never_fast_scan(tmp_path, monkeypatch):
    """-T (titles spec) stays on the full scan."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    _mock_search(monkeypatch)
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=GK_DURATIONS))

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args(titles="0"))

    rip_common.fast_scan_dvd.assert_not_called()
    assert result.fast_scan is False


def test_preamble_extras_flag_never_fast_scan(tmp_path, monkeypatch):
    """--extras stays on the full scan."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    _mock_search(monkeypatch)
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=GK_DURATIONS))

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args(extras=True))

    rip_common.fast_scan_dvd.assert_not_called()
    assert result.fast_scan is False


def test_preamble_no_disc_sniff_exits(tmp_path, monkeypatch, capsys):
    """An empty sniff keeps the existing no-disc exit, before any scan."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=("", "")))

    with pytest.raises(SystemExit) as e:
        rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    assert e.value.code == 1
    assert "no disc detected" in capsys.readouterr().err
    rip_common.scan_disc.assert_not_called()
    rip_common.fast_scan_dvd.assert_not_called()
