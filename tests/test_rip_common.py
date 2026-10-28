"""Tests for the shared movie/TV rip preamble."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from skive import rip_common
from skive.common import MENU, RipLog
from skive.scan import TitleInfo
from skive.tmdb import TMDBPick
from skive.validation import VALIDATION_FILENAME, movie_document, write_validation


# -- Interrupt exit (CONTEXT.md: Interrupt): partial purge + log + 130 --------

def _tmp_log(tmp_path):
    log = RipLog("skive rip movie", "makemkvcon", "Test", root=str(tmp_path))
    log.start()
    return log


def _run_log_lines(tmp_path) -> list[dict]:
    return [json.loads(line)
            for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]


def test_interrupted_exit_purges_junk_keeps_renamed_exits_130(tmp_path, capsys):
    """The interrupt exit deletes the junk-labeled in-flight partials, keeps
    completed renamed titles, records status=interrupted, prints ONE line, and
    exits 130."""
    stage = tmp_path / "d1"
    stage.mkdir()
    (stage / "B1_t0.mkv").touch()                # in-flight partial (junk label)
    (stage / "Show (2020)_S01E01.mkv").touch()   # completed, renamed
    log = _tmp_log(tmp_path)

    with pytest.raises(SystemExit) as e:
        rip_common.interrupted_exit(log, [stage])

    assert e.value.code == 130
    assert not (stage / "B1_t0.mkv").exists()
    assert (stage / "Show (2020)_S01E01.mkv").exists()
    lines = _run_log_lines(tmp_path)
    assert lines[-1]["phase"] == "total" and lines[-1]["status"] == "interrupted"
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "skive: interrupted — partial deleted, kept 1 titles" in err


def test_interrupted_exit_leaves_raw_log_for_evidence(tmp_path):
    """The interrupt keeps this run's raw log (deleted only on rc==0)."""
    stage = tmp_path / "d1"
    stage.mkdir()
    (stage / "B1_t0.mkv").touch()
    log = _tmp_log(tmp_path)
    log.raw("makemkvcon", "MSG:1007 Working")

    with pytest.raises(SystemExit):
        rip_common.interrupted_exit(log, [stage])

    assert (tmp_path / "logs" / "raw" / f"{log.run}.jsonl").exists()


def test_preamble_interrupt_logs_interrupted_and_reraises(tmp_path, monkeypatch):
    """An interrupt inside the preamble (disc scan, TMDB prompt) records the
    closing interrupted line — the run log is never left dangling — and passes
    the interrupt up for the one-line exit."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    monkeypatch.setattr(rip_common, "ensure_mounted", Mock())
    monkeypatch.setattr(rip_common, "sniff_disc",
                        Mock(return_value=("DVD-ROM", "", "")))
    monkeypatch.setattr(rip_common, "scan_disc",
                        Mock(side_effect=KeyboardInterrupt))

    with pytest.raises(KeyboardInterrupt):
        rip_common.rip_preamble("movie", "skive rip movie",
                                SimpleNamespace(tmdbid=None, verified=False))

    lines = _run_log_lines(tmp_path)
    assert lines[-1]["status"] == "interrupted"


def _mock_preamble_seams(monkeypatch, media="DVD-ROM", real_log=False):
    """Mock the preamble's IO seams; pass real_log=True to keep the real
    RipLog (writing under VIDEOS_MOUNT) for asserting on the JSONL itself."""
    log = None if real_log else Mock()
    if not real_log:
        monkeypatch.setattr(rip_common, "RipLog", Mock(return_value=log))
    monkeypatch.setattr(rip_common, "ensure_mounted", Mock())
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=(media, "", "")))
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
    # Title staging: rip/movies/<Name (Year)>/d{disc} (CONTEXT.md: Staging)
    assert result.title_dir == tmp_path / "rip" / "movies" / "Movie-Title (2020)"
    assert result.stage == result.title_dir / "d0"
    assert result.title_dir.is_dir() and result.stage.is_dir()
    rip_common.purge_junk.assert_called_once_with([result.title_dir, result.stage])
    log.start.assert_called_once_with()
    # The forced fast-scan fallback (see _mock_preamble_seams) adds a scan
    # note; the media note must still be among them.
    assert ("media", "media=DVD-ROM") in [tuple(c.args) for c in log.note.call_args_list]
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


def test_tv_preamble_resolves_tmdb_id_and_creates_title_staging(tmp_path, monkeypatch):
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
    assert result.title_dir == tmp_path / "rip" / "tv" / "Show"
    assert result.stage == result.title_dir / "d0"
    assert result.stage.is_dir()
    rip_common.purge_junk.assert_called_once_with([result.title_dir, result.stage])
    assert result.display == "Show"
    assert result.keep == ""


def test_preamble_mkdirs_title_staging_only_after_tmdb_pick(tmp_path, monkeypatch):
    """Preamble order (spec: Share layout): mount → sniff → gates → TMDB pick →
    mkdir the title staging. Nothing exists under rip/ until the pick has named
    the title folder."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    seen = {}

    def noting_pick(kind, prefill=""):
        seen["rip_exists_at_pick"] = (tmp_path / "rip").exists()
        return TMDBPick("Show", "2000", "en", "tv", "7")

    monkeypatch.setattr(rip_common, "search_loop", noting_pick)

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    assert seen["rip_exists_at_pick"] is False
    assert (tmp_path / "rip").is_dir()  # created afterwards, by the staging mkdir
    assert result.title_dir == tmp_path / "rip" / "tv" / "Show (2000)"
    assert result.stage == result.title_dir / "d0"


def test_preamble_junk_purge_scoped_to_title_folder(tmp_path, monkeypatch):
    """The preamble's junk purge sweeps only this title's tree: title folder +
    this run's d{disc}. A sibling title's junk and a parallel drive's d{disc}
    junk survive (show-scoped purge, spec: Share layout)."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    monkeypatch.setattr(rip_common, "ensure_mounted", Mock())
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=("DVD-ROM", "", "")))
    monkeypatch.setattr(rip_common, "fast_scan_dvd",
                        Mock(side_effect=RuntimeError("tests force the full-scan fallback")))
    monkeypatch.setattr(rip_common, "fast_scan_bluray",
                        Mock(side_effect=RuntimeError("tests force the full-scan fallback")))
    monkeypatch.setattr(rip_common, "scan_disc", Mock(return_value=("DVD-ROM", "", [])))
    monkeypatch.setattr(rip_common, "require_online", Mock())
    monkeypatch.setattr(rip_common, "search_loop",
                        Mock(return_value=TMDBPick("Show", "2000", "en", "tv", "7")))

    sibling = tmp_path / "rip" / "tv" / "Other Show (1999)"
    sibling.mkdir(parents=True)
    (sibling / "B1_t00.mkv").touch()  # another title's stale junk
    parallel = tmp_path / "rip" / "tv" / "Show (2000)" / "d1"
    parallel.mkdir(parents=True)
    (parallel / "B9_t03.mkv").touch()  # a parallel drive's in-flight junk

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    # This run's own d0 would-be junk is swept (nothing to assert — it starts
    # empty); everything outside the title folder + own d{disc} survives.
    assert (sibling / "B1_t00.mkv").exists()
    assert (parallel / "B9_t03.mkv").exists()
    assert result.stage == tmp_path / "rip" / "tv" / "Show (2000)" / "d0"


# --- DVD fast-scan gate (ADR 0002, DVD half) ---

GK_DURATIONS = [3932, 3897, 3653, 11482]


def test_tv_dvd_fast_scan_synthesizes_titles(tmp_path, monkeypatch):
    """TV + DVD + no flags: lsdvd durations become 0-based TitleInfos, size 0."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=("DVD-ROM", "GENERATION KILL", "")))
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=GK_DURATIONS))
    monkeypatch.setattr(rip_common, "search_loop",
                        Mock(return_value=TMDBPick("Show", "-", "-", "tv", "7")))

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    rip_common.fast_scan_dvd.assert_called_once_with(0, "/dev/sr0")
    rip_common.scan_disc.assert_not_called()
    assert result.fast_scan is True
    assert result.scan_durations == GK_DURATIONS
    assert result.device == "/dev/sr0"  # fallback: sniff printed no path
    assert [(t.number, t.seconds) for t in result.titles_full] == list(enumerate(GK_DURATIONS))
    assert all(t.size == 0 and not t.audio and not t.subtitles and not t.scrambled
               for t in result.titles_full)


def test_preamble_device_from_drv_line_reaches_scanner(tmp_path, monkeypatch):
    """The real device from sniff_disc flows to the scanner — NOT the
    /dev/sr{disc} guess — and onto the Preamble for the rip callers."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    monkeypatch.setattr(rip_common, "sniff_disc",
                        Mock(return_value=("DVD-ROM", "GENERATION KILL", "/dev/sr1")))
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=GK_DURATIONS))
    _mock_search(monkeypatch)

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    rip_common.fast_scan_dvd.assert_called_once_with(0, "/dev/sr1")
    assert result.device == "/dev/sr1"


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

    rip_common.fast_scan_dvd.assert_called_once_with(0, "/dev/sr0")
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
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=("BD-ROM", "SOME SHOW", "")))
    monkeypatch.setattr(rip_common, "fast_scan_bluray", Mock(return_value=GK_DURATIONS))
    _mock_search(monkeypatch)

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    rip_common.fast_scan_bluray.assert_called_once_with(0, "/dev/sr0")
    rip_common.fast_scan_dvd.assert_not_called()
    rip_common.scan_disc.assert_not_called()
    assert result.fast_scan is True
    assert result.scan_durations == GK_DURATIONS
    assert result.device == "/dev/sr0"
    assert [(t.number, t.seconds) for t in result.titles_full] == list(enumerate(GK_DURATIONS))


def test_tv_bd_fast_scan_ambiguous_durations_escalate_to_full_scan(tmp_path, monkeypatch, capsys):
    """Duplicate-duration BD fast scans (ADR 0002) escalate to the full scan
    via the same shared path as the DVD fast scan."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    log = _mock_preamble_seams(monkeypatch)
    ambiguous_durations = GK_DURATIONS + [3933]  # 3932 duplicate within ±1s
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=("BD-ROM", "SOME SHOW", "")))
    monkeypatch.setattr(rip_common, "fast_scan_bluray", Mock(return_value=ambiguous_durations))
    full_titles = [TitleInfo(0, 3932, 100, "dan", "", False), TitleInfo(1, 3933, 90, "dan", "", False)]
    rip_common.scan_disc.return_value = ("BD-ROM", "SOME SHOW", full_titles)
    _mock_search(monkeypatch)

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    rip_common.fast_scan_bluray.assert_called_once_with(0, "/dev/sr0")
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
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=("BD-ROM", "SOME SHOW", "")))
    full_titles = [TitleInfo(0, 3932, 100, "dan", "", False)]
    rip_common.scan_disc.return_value = ("BD-ROM", "SOME SHOW", full_titles)
    _mock_search(monkeypatch)

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    rip_common.fast_scan_bluray.assert_called_once_with(0, "/dev/sr0")
    assert result.fast_scan is False
    assert result.scan_durations is None
    assert result.titles_full == full_titles
    rip_common.scan_disc.assert_called_once()
    assert "fast scan failed" in capsys.readouterr().err


# --- Movie fast-scan gate (ADR 0002, issue 07) ---


def _movie_args(**kw):
    """Movie args shaped for the fast path: no -T, no --extras."""
    fields = dict(tmdbid=None, verified=False, titles=None, extras=False)
    fields.update(kw)
    return SimpleNamespace(**fields)


def test_movie_dvd_fast_scan_synthesizes_titles(tmp_path, monkeypatch):
    """Movie + DVD + no flags: plain movie rips fast-scan like TV (issue 07)."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=GK_DURATIONS))
    _mock_search(monkeypatch)

    result = rip_common.rip_preamble("movie", "skive rip movie", _movie_args())

    rip_common.fast_scan_dvd.assert_called_once_with(0, "/dev/sr0")
    rip_common.scan_disc.assert_not_called()
    assert result.fast_scan is True
    assert result.scan_durations == GK_DURATIONS
    assert [(t.number, t.seconds) for t in result.titles_full] == list(enumerate(GK_DURATIONS))
    assert all(t.size == 0 and not t.audio for t in result.titles_full)


def test_movie_bd_fast_scan_uses_bluray_scanner(tmp_path, monkeypatch):
    """Movie + pressed BD + no flags: the ffprobe listing fast-scans."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch, media="BD-ROM")
    monkeypatch.setattr(rip_common, "fast_scan_bluray", Mock(return_value=GK_DURATIONS))
    _mock_search(monkeypatch)

    result = rip_common.rip_preamble("movie", "skive rip movie", _movie_args())

    rip_common.fast_scan_bluray.assert_called_once_with(0, "/dev/sr0")
    rip_common.fast_scan_dvd.assert_not_called()
    rip_common.scan_disc.assert_not_called()
    assert result.fast_scan is True
    assert result.scan_durations == GK_DURATIONS


def test_movie_titles_menu_sentinel_never_fast_scan(tmp_path, monkeypatch):
    """Bare -T (the MENU sentinel) on a movie forces the full scan: the title
    menu needs TitleInfo sizes/languages (user story 15)."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    _mock_search(monkeypatch)
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=GK_DURATIONS))

    result = rip_common.rip_preamble("movie", "skive rip movie", _movie_args(titles=MENU))

    rip_common.fast_scan_dvd.assert_not_called()
    rip_common.scan_disc.assert_called_once()
    assert result.fast_scan is False
    assert result.scan_durations is None


def test_movie_titles_spec_never_fast_scan(tmp_path, monkeypatch):
    """-T 0 (a spec string) on a movie forces the full scan."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    _mock_search(monkeypatch)
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=GK_DURATIONS))

    result = rip_common.rip_preamble("movie", "skive rip movie", _movie_args(titles="0"))

    rip_common.fast_scan_dvd.assert_not_called()
    rip_common.scan_disc.assert_called_once()
    assert result.fast_scan is False


def test_movie_extras_flag_never_fast_scan(tmp_path, monkeypatch):
    """--extras on a movie stays on the full scan (per-title extras flow)."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    _mock_search(monkeypatch)
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=GK_DURATIONS))

    result = rip_common.rip_preamble("movie", "skive rip movie", _movie_args(extras=True))

    rip_common.fast_scan_dvd.assert_not_called()
    rip_common.scan_disc.assert_called_once()
    assert result.fast_scan is False


def test_movie_fast_scan_ambiguous_durations_escalate_to_full_scan(tmp_path, monkeypatch, capsys):
    """±1s duplicate durations on a movie fast scan escalate to the full scan
    — the shared escalation doubles as the BD decoy filter on the fast path."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    log = _mock_preamble_seams(monkeypatch)
    ambiguous_durations = GK_DURATIONS + [3933]  # 3932 duplicate within ±1s
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=ambiguous_durations))
    full_titles = [TitleInfo(0, 3932, 100, "dan", "", False), TitleInfo(1, 3933, 90, "dan", "", False)]
    rip_common.scan_disc.return_value = ("DVD-ROM", "A MOVIE", full_titles)
    _mock_search(monkeypatch)

    result = rip_common.rip_preamble("movie", "skive rip movie", _movie_args())

    rip_common.scan_disc.assert_called_once()
    assert result.fast_scan is False
    assert result.scan_durations is None
    assert result.titles_full == full_titles
    assert "escalating to the full MakeMKV scan" in capsys.readouterr().err
    assert any("full-scan escalation" in str(c.args) for c in log.note.call_args_list)


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
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=("", "", "")))

    with pytest.raises(SystemExit) as e:
        rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    assert e.value.code == 1
    assert "no disc detected" in capsys.readouterr().err
    rip_common.scan_disc.assert_not_called()
    rip_common.fast_scan_dvd.assert_not_called()


# --- Movie identity gates (map.md decision 2, issue 03) ---------------------

MAMMA_PICK = TMDBPick("Mamma Mia!", "2008", "en", "movie", "157")


def _validation(folder, tmdb_id, title):
    """A title folder carrying a written validation.json (the one writer)."""
    folder.mkdir(parents=True, exist_ok=True)
    doc = movie_document(tmdb_id, title, 6480, "MAMMA_PAL", "deadbeef", "t21", [])
    write_validation(folder / VALIDATION_FILENAME, doc)
    return folder


def _movie_run(monkeypatch, answers=()):
    """Movie preamble seams + search pick; `answers` feeds builtins.input in
    order — any unexpected prompt dies on StopIteration. Returns (log,
    prompts): the mock RipLog and every prompt string the gates showed."""
    log = _mock_preamble_seams(monkeypatch)
    monkeypatch.setattr(rip_common, "search_loop", Mock(return_value=MAMMA_PICK))
    prompts: list[str] = []
    chain = iter(answers)

    def fake_input(prompt):
        prompts.append(prompt)
        return next(chain)

    monkeypatch.setattr("builtins.input", fake_input)
    return log, prompts


def _gate_notes(log):
    """The (phase, text) notes a mock RipLog recorded under phase "gate"."""
    return [(c.args[0], c.args[1]) for c in log.note.call_args_list
            if c.args[0] == "gate"]


# -- pure helpers (no prompts) --

def test_recorded_identity_reads_id_and_title(tmp_path):
    folder = _validation(tmp_path / "rip" / "movies" / "Mamma Mia! (2008)",
                         "157", "Mamma Mia! (2008)")
    assert rip_common._recorded_identity(folder) == ("157", "Mamma Mia! (2008)")


def test_recorded_identity_unusable_documents_read_none(tmp_path):
    """No file, corrupt JSON, a non-object, no tmdb block, no id: all None —
    the gates never block or crash on an unreadable document."""
    folder = tmp_path / "legacy"
    assert rip_common._recorded_identity(folder) is None  # no file at all
    folder.mkdir()
    for text in ("{not json", '["a list"]', '{"run": "x"}',
                 '{"tmdb": "157"}', '{"tmdb": {}}'):
        (folder / VALIDATION_FILENAME).write_text(text)
        assert rip_common._recorded_identity(folder) is None, text


def test_recorded_identity_int_id_and_missing_title_fall_back(tmp_path):
    """A hand-edited int id still compares; a missing title falls back to the
    folder name so the wrong-name prompt can always name both films."""
    folder = tmp_path / "Odd (1990)"
    folder.mkdir()
    (folder / VALIDATION_FILENAME).write_text('{"tmdb": {"id": 5}}')
    assert rip_common._recorded_identity(folder) == ("5", "Odd (1990)")


def test_duplicate_id_folders_scans_all_three_movie_roots_sorted(tmp_path):
    _validation(tmp_path / "lossless" / "movies" / "B (1999)", "157", "B (1999)")
    _validation(tmp_path / "rip" / "movies" / "A (2001)", "157", "A (2001)")
    _validation(tmp_path / "library" / "movies" / "C (2010)", "157", "C (2010)")
    _validation(tmp_path / "rip" / "movies" / "Other (2005)", "999", "Other (2005)")
    _validation(tmp_path / "rip" / "tv" / "Show (2020)", "157", "Show (2020)")

    matches = rip_common._duplicate_id_folders(tmp_path, "157")

    assert [m.name for m in matches] == ["A (2001)", "B (1999)", "C (2010)"]


def test_duplicate_id_folders_never_matches_without_a_document(tmp_path):
    """Legacy folders (no file), corrupt files and non-folders never match —
    the gate can't block on what has no readable identity."""
    legacy = tmp_path / "rip" / "movies" / "Legacy (1980)"
    legacy.mkdir(parents=True)
    corrupt = tmp_path / "lossless" / "movies" / "Corrupt (1981)"
    corrupt.mkdir(parents=True)
    (corrupt / VALIDATION_FILENAME).write_text("{oops")
    stray = tmp_path / "library" / "movies" / "stray.mkv"
    stray.parent.mkdir(parents=True)
    stray.touch()

    assert rip_common._duplicate_id_folders(tmp_path, "157") == []


# -- gate (a): already ripped --

def test_duplicate_gate_bare_enter_refuses_before_staging(tmp_path, monkeypatch, capsys):
    """The picked id already under rip/movies prompts; bare Enter (default No)
    refuses: exit 1, no staging folder created, purge never runs, the refusal
    logged under phase "gate" and the matching folder listed on stderr."""
    existing = _validation(tmp_path / "rip" / "movies" / "Mamma Mia! (2008)",
                           "157", "Mamma Mia! (2008)")
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    log, prompts = _movie_run(monkeypatch, answers=[""])

    with pytest.raises(SystemExit) as e:
        rip_common.rip_preamble("movie", "skive rip movie", _movie_args())

    assert e.value.code == 1
    assert not (existing / "d0").exists()
    rip_common.purge_junk.assert_not_called()
    gate = _gate_notes(log)
    assert len(gate) == 1 and "already ripped" in gate[0][1]
    captured = capsys.readouterr()
    assert prompts == ["already ripped — continue? (y/N): "]  # default No
    assert str(existing) in captured.err  # the human sees where


def test_duplicate_gate_explicit_yes_proceeds(tmp_path, monkeypatch):
    """An explicit y confirms the already-ripped warning and the rip goes on."""
    existing = _validation(tmp_path / "rip" / "movies" / "Mamma Mia! (2008)",
                           "157", "Mamma Mia! (2008)")
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _movie_run(monkeypatch, answers=["y"])

    result = rip_common.rip_preamble("movie", "skive rip movie", _movie_args())

    assert result.stage == existing / "d0"
    assert result.stage.is_dir()
    rip_common.purge_junk.assert_called_once()


def test_duplicate_gate_eof_reads_as_no(tmp_path, monkeypatch):
    """EOF on the confirm (a scripted run, no human) is no explicit yes —
    refuse cleanly like bare Enter."""
    _validation(tmp_path / "rip" / "movies" / "Mamma Mia! (2008)", "157", "Mamma Mia! (2008)")
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    log, _ = _movie_run(monkeypatch)

    def eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)

    with pytest.raises(SystemExit) as e:
        rip_common.rip_preamble("movie", "skive rip movie", _movie_args())

    assert e.value.code == 1
    assert len(_gate_notes(log)) == 1


def test_gate_refusal_lands_in_the_run_log(tmp_path, monkeypatch):
    """No → the refusal is a real phase-"gate" line in the shared run log
    (real RipLog, real JSONL)."""
    _validation(tmp_path / "lossless" / "movies" / "Mamma Mia! (2008)",
                "157", "Mamma Mia! (2008)")
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch, real_log=True)
    monkeypatch.setattr(rip_common, "search_loop", Mock(return_value=MAMMA_PICK))
    monkeypatch.setattr("builtins.input", lambda _p: "")

    with pytest.raises(SystemExit):
        rip_common.rip_preamble("movie", "skive rip movie", _movie_args())

    gates = [line for line in _run_log_lines(tmp_path) if line["phase"] == "gate"]
    assert len(gates) == 1
    assert "already ripped" in gates[0]["status"]
    assert gates[0]["name"] == "Mamma Mia! (2008)"


# -- gate (b): wrong name --

def test_wrong_name_gate_names_both_films_default_no(tmp_path, monkeypatch, capsys):
    """The title folder exists but records a different film (the Mamma Mia
    case): the prompt names both films; bare Enter refuses, nothing staged."""
    folder = tmp_path / "rip" / "movies" / "Mamma Mia! (2008)"
    _validation(folder, "51084", "RED (2010)")
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    log, prompts = _movie_run(monkeypatch, answers=[""])

    with pytest.raises(SystemExit) as e:
        rip_common.rip_preamble("movie", "skive rip movie", _movie_args())

    assert e.value.code == 1
    assert not (folder / "d0").exists()
    gate = _gate_notes(log)
    assert len(gate) == 1 and "not the picked film" in gate[0][1]
    assert prompts == ['folder "Mamma Mia! (2008)" holds "RED (2010)", '
                       'you picked "Mamma Mia! (2008)" — continue? (y/N): ']


def test_wrong_name_gate_silent_when_folder_records_the_same_id(tmp_path, monkeypatch):
    """Same id in the title folder is the documented re-rip path: only gate
    (a)'s already-ripped confirm fires — gate (b) never prompts."""
    folder = _validation(tmp_path / "rip" / "movies" / "Mamma Mia! (2008)",
                         "157", "Mamma Mia! (2008)")
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    monkeypatch.setattr(rip_common, "search_loop", Mock(return_value=MAMMA_PICK))
    prompts = []

    def recording_input(prompt):
        prompts.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", recording_input)

    result = rip_common.rip_preamble("movie", "skive rip movie", _movie_args())

    assert result.stage == folder / "d0"
    assert result.stage.is_dir()
    assert prompts == ["already ripped — continue? (y/N): "]

def test_duplicate_gate_name_match_catches_a_legacy_folder(tmp_path, monkeypatch, capsys):
    """A same-name folder with no validation.json trips the gate on name
    alone — the library today holds zero documents, so ids are blind to
    every finished encode. Bare Enter refuses before staging."""
    existing = tmp_path / "library" / "movies" / "Mamma Mia! (2008)"
    existing.mkdir(parents=True)
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    log, prompts = _movie_run(monkeypatch, answers=[""])

    with pytest.raises(SystemExit) as e:
        rip_common.rip_preamble("movie", "skive rip movie", _movie_args())

    assert e.value.code == 1
    assert prompts == ["already ripped — continue? (y/N): "]
    assert len(_gate_notes(log)) == 1
    assert str(existing) in capsys.readouterr().err


def test_gates_pass_silently_for_a_foreign_legacy_folder(tmp_path, monkeypatch):
    """A legacy folder of a different title never prompts."""
    tmp_path.joinpath("rip", "movies", "Other (2005)").mkdir(parents=True)
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _movie_run(monkeypatch)  # no answers: any prompt stops the test

    result = rip_common.rip_preamble("movie", "skive rip movie", _movie_args())

    assert result.stage == tmp_path / "rip" / "movies" / "Mamma Mia! (2008)" / "d0"


def test_extras_run_skips_the_already_ripped_gate(tmp_path, monkeypatch):
    """-E adds to an owned title: the movie on disk is the normal case —
    the already-ripped gate never fires."""
    tmp_path.joinpath("library", "movies", "Mamma Mia! (2008)").mkdir(parents=True)
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _movie_run(monkeypatch)  # no answers: any prompt stops the test

    result = rip_common.rip_preamble("movie", "skive rip movie", _movie_args(extras=True))

    assert result.stage == tmp_path / "rip" / "movies" / "Mamma Mia! (2008)" / "d0"


# -- TV rip preflight (season gate) --

def test_tv_season_gate_exact_season_escalates_default_no(tmp_path, monkeypatch, capsys):
    """The exact season on disk prints the folders and escalates to the
    default-No confirm; bare Enter refuses (exit 1, gate note in the log)."""
    show = tmp_path / "library" / "tv" / "Show (2020)"
    (show / "Season 01").mkdir(parents=True)
    monkeypatch.setattr("builtins.input", lambda _p: "")
    log = Mock()

    with pytest.raises(SystemExit) as e:
        rip_common.tv_season_gate(tmp_path, log, "Show (2020)", 1)

    assert e.value.code == 1
    assert log.note.call_args.args == (
        "gate", '"Show (2020)" S01 already ripped — nothing ripped')
    assert str(show) in capsys.readouterr().err


def test_tv_season_gate_other_seasons_only_inform(tmp_path, monkeypatch, capsys):
    """A show on disk with other seasons never prompts — a new season of an
    owned show is a normal rip; the info line names both sides."""
    show = tmp_path / "library" / "tv" / "Show (2020)"
    (show / "Season 01").mkdir(parents=True)
    monkeypatch.setattr("builtins.input", lambda _p: pytest.fail("must not prompt"))
    log = Mock()

    rip_common.tv_season_gate(tmp_path, log, "Show (2020)", 2)

    log.note.assert_not_called()
    err = capsys.readouterr().err
    assert "S01" in err and "S02" in err


# -- movies only: TV untouched --

def test_tv_preamble_runs_no_gates(tmp_path, monkeypatch):
    """TV (kind == "tv") is untouched: a duplicate id under the movie roots
    and a wrong-name TV title folder trigger neither prompt nor refusal."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    _mock_preamble_seams(monkeypatch)
    _validation(tmp_path / "rip" / "movies" / "Mamma Mia! (2008)", "42", "Mamma Mia! (2008)")
    _validation(tmp_path / "rip" / "tv" / "Show (2000)", "999", "RED (2010)")
    monkeypatch.setattr(rip_common, "search_loop",
                        Mock(return_value=TMDBPick("Show", "2000", "en", "tv", "42")))
    monkeypatch.setattr("builtins.input", lambda _p: pytest.fail("TV must not prompt"))

    result = rip_common.rip_preamble("tv", "skive rip tv", _tv_args())

    assert result.stage == tmp_path / "rip" / "tv" / "Show (2000)" / "d0"
    assert result.stage.is_dir()
