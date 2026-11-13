"""End-to-end tests for rip_movie with mocked hardware/network."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from skive.rip_movie import rip_movie
from skive.scan import TitleInfo
from skive.tmdb import TMDBPick


@pytest.fixture(autouse=True)
def _stage_root(tmp_path, monkeypatch):
    """Keep staging off the real /mnt/videos."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))


@pytest.fixture(autouse=True)
def _fast_scan_fails(monkeypatch):
    """Default movie tests land on the full-scan fallback, exactly like the
    pre-fast-scan flow (issue 07 fast-path tests override these seams)."""
    from skive import rip_common
    monkeypatch.setattr(rip_common, "fast_scan_dvd",
                        Mock(side_effect=RuntimeError("tests force the full-scan fallback")))
    monkeypatch.setattr(rip_common, "fast_scan_bluray",
                        Mock(side_effect=RuntimeError("tests force the full-scan fallback")))


@pytest.fixture(autouse=True)
def _confirm_enter(monkeypatch):
    """Every movie run now ends its pre-rip gate in a shared prompt; default
    it to Enter (=rip) so the flow tests keep their shape."""
    monkeypatch.setattr("skive.ui.ask", lambda *a, **k: k.get("default", ""))
    monkeypatch.setattr("skive.ui.confirm", lambda *a, **k: False)


class FakeArgs:
    """Minimal argparse.Namespace for rip_movie."""
    def __init__(
        self,
        titles=None,
        extras=False,
        part=None,
        tmdbid=None,
        verified=False,
        include_extras=False,
    ):
        self.titles = titles
        self.extras = extras
        self.part = part
        self.tmdbid = tmdbid
        self.verified = verified
        self.include_extras = include_extras


def test_rip_movie_refuses_recordable():
    """Test burned disc refuses without --verified."""
    args = FakeArgs(verified=False)

    with patch("skive.rip_common.sniff_disc", return_value=("DVD+R", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD+R", "", [])):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
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

    with patch("skive.rip_common.sniff_disc", return_value=("DVD+R", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD+R", "", [])):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=mock_pick):
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

    with patch("skive.rip_common.sniff_disc", return_value=("", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("", "", [])):
        with patch("skive.rip_common.ensure_mounted"):
            try:
                rip_movie(args)
                assert False, "Should have exited"
            except SystemExit as e:
                assert e.code == 1


def test_rip_movie_tmdbid():
    """Test --tmdbid skips search."""
    args = FakeArgs(tmdbid="movie:12345")

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    mock_pick = TMDBPick(
                        title="TMDB Movie",
                        year="2019",
                        original_language="en",
                        type="movie",
                        id="12345",
                    )
                    with patch("skive.rip_common.fetch_by_id", return_value=mock_pick):
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

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=mock_pick):
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

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=mock_pick):
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

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=mock_pick):
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

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=mock_pick):
                        with patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")):
                            with patch("skive.rip_movie.rip_titles", return_value=1):
                                with patch("skive.rip_movie.rename_staged"):
                                    with patch("skive.rip_movie.subtitle_note", return_value="subs=dan"):
                                        with patch("pathlib.Path.unlink") as mock_unlink:
                                            with patch("skive.rip_common.RipLog") as MockLog:
                                                # RipLog.note should be called with rip note
                                                log_instance = MockLog.return_value
                                                log_instance.run = "run-1"  # the validation document's run identity
                                                try:
                                                    rip_movie(args)
                                                except SystemExit:
                                                    pass
                                            # Check the rip note was made
                                            log_instance.note.assert_any_call("rip", "makemkvcon rc=1 — partial rip, renaming what completed")


def test_rip_movie_runtime_note_when_known():
    """Identity note: a known movie runtime is recorded beside the type note."""
    args = FakeArgs()
    mock_pick = TMDBPick(title="Mamma Mia!", year="2008", original_language="en",
                         type="movie", id="471", runtime_secs=6480)

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=mock_pick):
                        with patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")):
                            with patch("skive.rip_movie.rip_titles", return_value=0):
                                with patch("skive.rip_movie.rename_staged"):
                                    with patch("skive.rip_movie.subtitle_note", return_value="subs=dan"):
                                        with patch("pathlib.Path.unlink"):
                                            with patch("skive.rip_common.RipLog") as MockLog:
                                                log_instance = MockLog.return_value
                                                log_instance.run = "run-1"  # the validation document's run identity
                                                try:
                                                    rip_movie(args)
                                                except SystemExit:
                                                    pass
                                                log_instance.note.assert_any_call("type", "type=movie")
                                                log_instance.note.assert_any_call("runtime", "runtime=6480s")


def test_rip_movie_no_runtime_note_when_unknown():
    """An unknown runtime stays absent from the identity notes (never 0)."""
    args = FakeArgs()
    mock_pick = TMDBPick(title="Mystery Film", year="2010", original_language="de",
                         type="movie", id="99", runtime_secs=None)

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=mock_pick):
                        with patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")):
                            with patch("skive.rip_movie.rip_titles", return_value=0):
                                with patch("skive.rip_movie.rename_staged"):
                                    with patch("skive.rip_movie.subtitle_note", return_value="subs=dan"):
                                        with patch("pathlib.Path.unlink"):
                                            with patch("skive.rip_common.RipLog") as MockLog:
                                                log_instance = MockLog.return_value
                                                log_instance.run = "run-1"  # the validation document's run identity
                                                try:
                                                    rip_movie(args)
                                                except SystemExit:
                                                    pass
                                                runtime_notes = [c for c in log_instance.note.call_args_list
                                                                 if c.args and c.args[0] == "runtime"]
                                                assert runtime_notes == []

def test_rip_movie_clean_rip_exits_0():
    """A rc=0 rip finishes the flow (log/finish) and exits 0."""
    args = FakeArgs(verified=True)
    mock_pick = TMDBPick(title="Test Movie", year="2020", original_language="en", type="movie", id="12345")

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=mock_pick):
                        with patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")):
                            with patch("skive.rip_movie.rip_titles", return_value=0):
                                with patch("skive.rip_movie.rename_staged"):
                                    with patch("skive.rip_movie.subtitle_note", return_value="subs=dan langs=dan"):
                                        with patch("pathlib.Path.unlink"):
                                            try:
                                                rip_movie(args)
                                            except SystemExit as e:
                                                assert e.code == 0


def test_rip_movie_partial_rip_exits_1():
    """A partial rip (rc!=0) still exits 1 after renaming what completed."""
    args = FakeArgs()
    mock_pick = TMDBPick(title="Test Movie", year="2020", original_language="en", type="movie", id="12345")

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=mock_pick):
                        with patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")):
                            with patch("skive.rip_movie.rip_titles", return_value=1):
                                with patch("skive.rip_movie.rename_staged"):
                                    with patch("skive.rip_movie.subtitle_note", return_value="subs=dan langs=dan"):
                                        with patch("pathlib.Path.unlink"):
                                            try:
                                                rip_movie(args)
                                            except SystemExit as e:
                                                assert e.code == 1


# --- Fast-scan rip flow (ADR 0002, issue 07) ---

FAST_DURATIONS = [7021, 599]  # main feature + a 10-minute short


def test_rip_movie_i_run_fast_scan_rips_all_with_two_line_header(monkeypatch):
    """A movie -i run on the fast scan (the only movie run that still takes
    it — default runs select the main and need MakeMKV numbers, ADR 0015):
    rip all in one call (titles=None, no resolve_titles dedupe), a two-line
    header (no 0.0 B / ? preview), and rename_staged keeping MakeMKV's own
    _tNN numbers."""
    from skive import rip_common
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=FAST_DURATIONS))

    args = FakeArgs(include_extras=True)
    mock_pick = TMDBPick(
        title="Fast Movie",
        year="2021",
        original_language="en",
        type="movie",
        id="1",
    )

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])) as mock_scan, \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=mock_pick), \
            patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")), \
            patch("skive.rip_movie.rip_titles", return_value=0) as mock_rip, \
            patch("skive.rip_movie.resolve_titles") as mock_resolve, \
            patch("skive.rip_movie.rename_staged") as mock_rename, \
            patch("skive.rip_movie.subtitle_note", return_value="subs=none"), \
            patch("pathlib.Path.unlink"):
        with pytest.raises(SystemExit) as e:
            rip_movie(args)
        assert e.value.code == 0

    mock_scan.assert_not_called()
    mock_resolve.assert_not_called()
    mock_rip.assert_called_once()
    rip_args, rip_kwargs = mock_rip.call_args
    assert rip_args[0] is None
    assert rip_kwargs["total"] == len(FAST_DURATIONS)
    assert [t.number for t in rip_kwargs["infos"]] == list(range(len(FAST_DURATIONS)))
    assert rip_kwargs["header"] == ("Fast Movie (2021)\n"
                                    f"  titles: {len(FAST_DURATIONS)} (fast scan — no size/audio preview)")
    mock_rename.assert_called_once()
    assert mock_rename.call_args[1]["suffix"] == "t"


def test_rip_movie_default_run_skips_fast_scan_takes_full_scan(monkeypatch):
    """A default movie run never touches the fast scan even when the scanner
    works (ADR 0015): selecting the main alone needs MakeMKV's real title
    numbers, so the full scan runs — and with no titles above the main floor
    the biggest title becomes the rip set."""
    from skive import rip_common
    fast = Mock(return_value=[600, 900])
    monkeypatch.setattr(rip_common, "fast_scan_dvd", fast)

    titles = [TitleInfo(21, 6400, 8_000_000_000, "en", "", False),  # the main
              TitleInfo(4, 600, 100_000_000, "en", "", False)]       # an extra
    log, prompt, rip = _movie_flow(monkeypatch, titles, answers=("",))
    monkeypatch.setattr(rip_common, "scan_disc",
                        Mock(return_value=("DVD-ROM", "", titles)))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 0
    fast.assert_not_called()
    rip_common.scan_disc.assert_called_once()
    rip.assert_called_once()
    rip_args, rip_kwargs = rip.call_args
    assert rip_args[0] == [21]  # the arbitrated main alone
    assert [t.number for t in rip_kwargs["infos"]] == [21]
    assert rip_kwargs["total"] == 2  # the table still shows every title
    assert ("titles", "rip set: t21 (main only — extras not ripped)") in \
        [tuple(c.args) for c in log.note.call_args_list]


# -- Interrupt (Ctrl+C) mid-rip (CONTEXT.md: Interrupt) ------------------------

def test_rip_movie_interrupt_purges_partial_keeps_renamed_exits_130(tmp_path, capsys):
    """Ctrl+C mid-rip: the junk-labeled in-flight partial is deleted, completed
    renamed files are kept, the run log records status=interrupted, one line on
    stderr, exit 130."""
    import json

    mock_pick = TMDBPick("Test Movie", "2020", "en", "movie", "1")
    title_dir = tmp_path / "rip" / "movies" / "Test Movie (2020)"
    d0 = title_dir / "d0"
    title_dir.mkdir(parents=True)
    (title_dir / "Test Movie (2020)_t01.mkv").touch()  # completed rename — kept

    def rip_a_partial_then_interrupt(*a, **k):
        d0.mkdir(parents=True, exist_ok=True)  # the preamble already made it
        (d0 / "C2_t1.mkv").write_text("in-flight partial")  # makemkvcon's junk label
        raise KeyboardInterrupt

    # The name gate sees the interrupted run's staged titles — confirm through
    with patch("builtins.input", return_value="y"), \
            patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=mock_pick), \
            patch("skive.rip_movie.inject_profile", return_value=tmp_path / "absent-profile.xml"), \
            patch("skive.rip_movie.rip_titles", side_effect=rip_a_partial_then_interrupt):
        with pytest.raises(SystemExit) as e:
            rip_movie(FakeArgs())

    assert e.value.code == 130
    assert not (d0 / "C2_t1.mkv").exists()
    assert (title_dir / "Test Movie (2020)_t01.mkv").exists()
    # An interrupted run writes no Validation file — partial rips stay
    # unrecorded rather than lying (issue 05). The write sits beside the
    # subtitle note, after the interrupt's exit point.
    assert not (title_dir / "validation.json").exists()
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert lines[-1]["phase"] == "total" and lines[-1]["status"] == "interrupted"
    err = capsys.readouterr().err
    assert "fail  interrupted — partial deleted, kept 1 titles" in err
    assert err.count("fail  interrupted") == 2  # event plus interrupted result line


def test_rip_movie_interrupt_fail_line_survives_a_closed_tty_view(tmp_path, capsys):
    """Regression (P1): with a seam TTY view set, view.close() runs before
    interrupted_exit's ui.fail — the fail line must still reach stderr
    (a closed view's refresh is a no-op), exit 130."""
    from skive import ui

    mock_pick = TMDBPick("Test Movie", "2020", "en", "movie", "1")
    d0 = tmp_path / "rip" / "movies" / "Test Movie (2020)" / "d0"

    def interrupt(*a, **k):
        d0.mkdir(parents=True, exist_ok=True)
        k["on_title_start"](3)  # a title's frame is open when Ctrl+C lands
        raise KeyboardInterrupt

    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch("builtins.input", return_value="y"), \
                patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
                patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])), \
                patch("skive.rip_common.ensure_mounted"), \
                patch("skive.rip_common.require_online"), \
                patch("skive.rip_common.search_loop", return_value=mock_pick), \
                patch("skive.rip_movie.inject_profile", return_value=tmp_path / "absent-profile.xml"), \
                patch("skive.rip_movie.rip_titles", side_effect=interrupt):
            with pytest.raises(SystemExit) as e:
                rip_movie(FakeArgs())
    finally:
        ui.clear_view()

    assert e.value.code == 130
    err = capsys.readouterr().err
    assert "fail  interrupted" in err
    assert err.count("fail  interrupted") == 2  # event plus interrupted result line
    # the opener box: title in the border, scope lines inside, before the frame
    assert (err.index("\u256d\u2500 skive rip movie \u2500") < err.index("\u2502 Test Movie (2020) ")
            < err.index("\u2502 disc 0 \u00b7 ") < err.index("\u256d\u2500 1/0  title 3"))
    # the open frame closes as interrupted before the summary
    assert err.index("\u2570\u2500 \u2717 interrupted ") < err.index("fail  interrupted")


def test_rip_movie_summary_prints_after_view_closed(monkeypatch):
    """Regression (P1): on the success path view.close() runs before
    ui.run_summary — the summary never prints under a live Live region."""
    from skive import ui

    _log, _prompt, _rip = _movie_flow(monkeypatch, MIXED_TITLES, answers=("", ""))
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    seen = []
    real = ui.run_summary
    monkeypatch.setattr(ui, "run_summary",
                        lambda *a, **k: seen.append(view._closed) or real(*a, **k))
    try:
        with pytest.raises(SystemExit) as e:
            rip_movie(FakeArgs())
    finally:
        ui.clear_view()

    assert e.value.code == 0
    assert seen == [True]  # the view was already closed when the summary printed


def test_rip_movie_preamble_interrupt_exits_130_with_one_line(tmp_path, capsys):
    """Ctrl+C at the TMDB prompt (inside rip_preamble): the preamble logged the
    interrupted status, main's fallback owns the one-line exit — no traceback."""
    import json

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            rip_movie(FakeArgs())

    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert lines[-1]["status"] == "interrupted"


# --- Pre-rip gate: table, main pick, confirm (issue 04) ----------------------

MAMMA = TMDBPick("Mamma Mia!", "2008", "en", "movie", "157", runtime_secs=6480)


def _movie_flow(monkeypatch, titles, pick=MAMMA, answers=("",)):
    """Movie flow seams for the pre-rip gate tests: full scan over `titles`,
    `pick` from search, `answers` feeding the shared prompt in order (the main
    pick when it appears, then the confirm; StopIteration kills the run on an
    unexpected prompt). Returns (log, prompt, rip): the mock RipLog, the mock
    prompt, and the mock rip_titles."""
    from skive import rip_common

    log = Mock()
    log.run = "a1b2c3d4"  # the validation document's run identity
    prompt = Mock()
    chain = iter(answers)
    prompt.ask.side_effect = lambda *a, **k: next(chain)
    rip = Mock(return_value=0)

    monkeypatch.setattr(rip_common, "RipLog", Mock(return_value=log))
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=("DVD-ROM", "", "")))
    monkeypatch.setattr(rip_common, "scan_disc", Mock(return_value=("DVD-ROM", "", titles)))
    monkeypatch.setattr(rip_common, "ensure_mounted", Mock())
    monkeypatch.setattr(rip_common, "purge_junk", Mock())
    monkeypatch.setattr(rip_common, "require_online", Mock())
    monkeypatch.setattr(rip_common, "search_loop", Mock(return_value=pick))
    monkeypatch.setattr("skive.ui.ask", prompt.ask)
    monkeypatch.setattr("skive.ui.confirm", lambda *a, **k: next(chain).strip().lower() in ("y", "yes"))
    monkeypatch.setattr("skive.rip_movie.inject_profile", Mock(return_value=Path("/tmp/test.xml")))
    monkeypatch.setattr("skive.rip_movie.rip_titles", rip)
    monkeypatch.setattr("skive.rip_movie.rename_staged", Mock())
    monkeypatch.setattr("skive.rip_movie.subtitle_note", Mock(return_value="subs=none"))
    return log, prompt, rip


def _main_notes(log):
    """The (phase, text) notes a mock RipLog recorded under phase "main"."""
    return [tuple(c.args) for c in log.note.call_args_list if c.args[0] == "main"]


# The Mamma Mia disc: two feature-length titles near the 6480s runtime
MIXED_TITLES = [TitleInfo(21, 6252, 4_300_000_000, "dan", "", False),
                TitleInfo(12, 6662, 19_000_000_000, "dan", "", False)]


def test_pre_rip_several_candidates_default_taken_on_enter(monkeypatch):
    """Two titles near the runtime: the pick prompt appears and bare Enter
    takes the biggest-bytes default; the run then rips."""
    log, prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES, answers=("", ""))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 0
    assert prompt.ask.call_count == 2  # the pick prompt, then the confirm
    assert ("main", "main=t12 default (biggest bytes) — 2 titles near the runtime") in _main_notes(log)
    rip.assert_called_once()


def test_pre_rip_several_candidates_explicit_pick(monkeypatch):
    """A number at the pick prompt chooses that title as the main — and as
    the main-only rip set (ADR 0015): the other near-runtime title stays on
    disc."""
    log, prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES, answers=("21", ""))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 0
    assert ("main", "main=t21 picked by hand — 2 titles near the runtime") in _main_notes(log)
    rip.assert_called_once()
    assert rip.call_args[0][0] == [21]


def test_pre_rip_several_candidates_q_quits_before_any_rip(monkeypatch, capsys):
    """q at the pick prompt quits with nothing ripped — no rip call at all."""
    log, prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES, answers=("q",))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 1
    rip.assert_not_called()
    assert "info  quit — nothing ripped." in capsys.readouterr().err


def test_pre_rip_sole_near_candidate_automatic_without_prompt(monkeypatch, capsys):
    """One candidate near the runtime, one short: the main is automatic —
    only the confirm prompts."""
    titles = [TitleInfo(5, 6400, 8_000_000_000, "en", "", False),
              TitleInfo(2, 600, 100_000_000, "en", "", False)]
    log, prompt, rip = _movie_flow(monkeypatch, titles, answers=("",))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 0
    assert prompt.ask.call_count == 1  # confirm only, no pick prompt
    assert ("main", "main=t5 near the runtime — automatic") in _main_notes(log)
    assert "0.99" in capsys.readouterr().err  # the candidate's graded ratio cell
    rip.assert_called_once()
    assert rip.call_args[0][0] == [5]  # the main alone (ADR 0015)


def test_pre_rip_zero_candidates_main_only_picks_biggest(monkeypatch, capsys):
    """Shorts only (nothing at/above the floor) on a default run: the
    biggest title becomes the main — a main-only run must name a title to
    rip (ADR 0015) — one info line names the rip set, and the run rips that
    title alone after its confirm."""
    titles = [TitleInfo(1, 600, 100_000_000, "en", "", False),
              TitleInfo(2, 900, 200_000_000, "en", "", False)]
    log, prompt, rip = _movie_flow(monkeypatch, titles, answers=("",))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 0
    assert prompt.ask.call_count == 1
    assert _main_notes(log) == [("main", "main=t2 biggest bytes — no candidate main on the disc")]
    assert "rip set: t2 only — extras not ripped (-i to include them)" in capsys.readouterr().err
    rip.assert_called_once()
    assert rip.call_args[0][0] == [2]


def test_pre_rip_zero_candidates_i_run_records_none(monkeypatch):
    """Shorts only on a -i run (rip-all): no main logic names a title, the
    log records main=none — transcode keeps its biggest-bytes fallback."""
    titles = [TitleInfo(1, 600, 100_000_000, "en", "", False),
              TitleInfo(2, 900, 200_000_000, "en", "", False)]
    log, prompt, rip = _movie_flow(monkeypatch, titles, answers=("",))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs(include_extras=True))

    assert e.value.code == 0
    assert prompt.ask.call_count == 1
    assert _main_notes(log) == [("main", "main=none — no candidate main on the disc")]
    rip.assert_called_once()
    assert rip.call_args[0][0] is None  # rip all


def test_pre_rip_yellow_escalation_default_no_aborts(monkeypatch, capsys):
    """Any yellow row escalates the confirm to y/N default No: bare Enter
    aborts with nothing ripped (the automatic main still logged first)."""
    titles = [TitleInfo(1, 6300, 5_000_000_000, "en", "", False),   # 0.97 green
              TitleInfo(2, 3000, 6_000_000_000, "en", "", False)]   # 0.46 yellow
    log, prompt, rip = _movie_flow(monkeypatch, titles, answers=("",))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 1
    rip.assert_not_called()
    assert ("main", "main=t1 near the runtime — automatic") in _main_notes(log)
    assert "info  quit — nothing ripped." in capsys.readouterr().err


def test_pre_rip_yellow_escalation_explicit_yes_proceeds(monkeypatch, capsys):
    """An explicit y past the yellow escalation rips."""
    titles = [TitleInfo(1, 6300, 5_000_000_000, "en", "", False),
              TitleInfo(2, 3000, 6_000_000_000, "en", "", False)]
    _log, _prompt, rip = _movie_flow(monkeypatch, titles, answers=("y",))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 0
    rip.assert_called_once()
    # The shared confirm owns the rendered y/N prompt; this flow seam only
    # supplies its answer.
    capsys.readouterr()


def test_pre_rip_plain_confirm_q_aborts(monkeypatch, capsys):
    """The confirm stands on every run: q quits with nothing ripped."""
    _log, _prompt, rip = _movie_flow(monkeypatch, [], answers=("q",))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 1
    rip.assert_not_called()
    assert "info  quit — nothing ripped." in capsys.readouterr().err


def test_pre_rip_extras_no_ratio_column_and_no_main(monkeypatch, capsys):
    """-E runs: no ratio column, no main note, plain confirm — even with two
    feature-length titles that would otherwise force the pick prompt."""
    log, prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES, answers=("",))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs(extras=True))

    assert e.value.code == 0
    assert prompt.ask.call_count == 1  # the plain confirm only
    assert _main_notes(log) == []
    out = capsys.readouterr().out
    assert "0.96" not in out and "1.03" not in out  # no graded ratio cells
    rip.assert_called_once()


def test_pre_rip_confirm_gates_the_T_menu(monkeypatch, capsys):
    """-T keeps its menu, but the confirm stands over it: q at the confirm
    quits before the menu ever opens."""
    from skive import rip_movie as rm
    monkeypatch.setattr(rm, "resolve_titles", Mock())
    _log, _prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES, answers=("q",))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs(titles="0"))

    assert e.value.code == 1
    rm.resolve_titles.assert_not_called()
    rip.assert_not_called()


def test_pre_rip_confirm_then_T_menu_still_opens(monkeypatch):
    """Enter at the pick prompt and the confirm, and the -T menu still opens,
    then the rip runs."""
    from skive import rip_movie as rm
    monkeypatch.setattr(rm, "resolve_titles", Mock(return_value=[12]))
    _log, prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES, answers=("", ""))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs(titles="0"))

    assert e.value.code == 0
    assert prompt.ask.call_count == 2  # the pick prompt, then the confirm
    rm.resolve_titles.assert_called_once()
    rip.assert_called_once()


def test_pre_rip_confirm_returns_the_validation_seam(monkeypatch):
    """The post-rip write's seam (slice 05): the chosen main as "tNN" and the
    per-title graded rows come back from one call, title order, sub-floor
    rows ungraded."""
    from skive import rip_movie as rm
    log = Mock()
    prompt = Mock()
    prompt.ask.side_effect = ["21", ""]  # hand-pick t21 at the prompt, confirm
    monkeypatch.setattr("skive.ui.ask", prompt.ask)

    main, rows = rm.pre_rip_confirm(log, "Mamma Mia! (2008)", MIXED_TITLES, 6480)

    assert main == "t21"
    assert rows == [
        {"t": 12, "dur_s": 6662, "ratio": 6662 / 6480, "band": "green"},
        {"t": 21, "dur_s": 6252, "ratio": 6252 / 6480, "band": "green"},
    ]
    log.note.assert_any_call("main", "main=t21 picked by hand — 2 titles near the runtime")


def test_pre_rip_confirm_main_only_shorts_take_biggest(monkeypatch, capsys):
    """The seam call with main_only=True and no candidate main (a shorts
    disc): the biggest title comes back as the main so the caller can rip it
    alone (ADR 0015), and the rip-set info line prints before the confirm."""
    from skive import rip_movie as rm
    prompt = Mock()
    prompt.ask.return_value = ""  # confirm Enter
    monkeypatch.setattr("skive.ui.ask", prompt.ask)
    log = Mock()
    titles = [TitleInfo(1, 600, 100_000_000, "en", "", False),
              TitleInfo(2, 900, 200_000_000, "en", "", False)]

    main, rows = rm.pre_rip_confirm(log, "Short Film (2015)", titles, 900,
                                    main_only=True)

    assert main == "t2"
    assert ("main", "main=t2 biggest bytes — no candidate main on the disc") in \
        [tuple(c.args) for c in log.note.call_args_list]
    assert "rip set: t2 only — extras not ripped (-i to include them)" in capsys.readouterr().err


def test_pre_rip_confirm_unknown_runtime_blanks_rows(monkeypatch):
    """No runtime: rows come back ungraded (never yellow) and no main is chosen."""
    from skive import rip_movie as rm
    prompt = Mock()
    prompt.ask.return_value = ""  # confirm Enter
    monkeypatch.setattr("skive.ui.ask", prompt.ask)

    main, rows = rm.pre_rip_confirm(Mock(), "Mystery Film (2010)", MIXED_TITLES, None)

    assert main == "t12"  # biggest-bytes fallback under an unknown runtime
    assert rows == [
        {"t": 12, "dur_s": 6662, "ratio": None, "band": None},
        {"t": 21, "dur_s": 6252, "ratio": None, "band": None},
    ]


def test_pre_rip_confirm_extras_rows_ungraded_and_main_none(monkeypatch):
    """-E runs: main None, every row ungraded — no main logic at all."""
    from skive import rip_movie as rm
    prompt = Mock()
    prompt.ask.return_value = ""
    monkeypatch.setattr("skive.ui.ask", prompt.ask)

    main, rows = rm.pre_rip_confirm(Mock(), "Bonus Disc", MIXED_TITLES, 6480, extras=True)

    assert main is None
    assert rows == [
        {"t": 12, "dur_s": 6662, "ratio": None, "band": None},
        {"t": 21, "dur_s": 6252, "ratio": None, "band": None},
    ]


# --- Post-rip report + validation.json (issue 05, map.md decision 5) --------


def test_probe_staged_probes_actuals_and_warns_on_unreadable(tmp_path, capsys):
    """The probe records ffprobe actuals (never the scan table), skips names
    without a final _tNN, and records an unreadable file with null duration
    and one stderr warning — never crashing."""
    from skive.rip_movie import probe_staged

    title = tmp_path / "M"
    title.mkdir()
    (title / "M_t01.mkv").write_bytes(b"x")   # 600s — sub-floor, ungraded
    (title / "M_t05.mkv").write_bytes(b"x")   # 6249s — graded against 6480
    (title / "M_t02.mkv").write_bytes(b"x")   # ffprobe reads nothing
    (title / "M - Extra 01.mkv").write_bytes(b"x")  # no _tNN — skipped

    def fake_run(cmd, **k):
        name = cmd[-1]
        out = {"M_t01.mkv": "600.0\n", "M_t05.mkv": "6249.5\n", "M_t02.mkv": ""}[name.split("/")[-1]]
        return Mock(stdout=out)

    rows = probe_staged(title, set(), 6480, run=fake_run)

    assert rows == [
        {"file": "M_t01.mkv", "t": 1, "dur_s": 600, "ratio": None, "band": None},
        {"file": "M_t02.mkv", "t": 2, "dur_s": None, "ratio": None, "band": None},
        {"file": "M_t05.mkv", "t": 5, "dur_s": 6249, "ratio": 0.96, "band": "green"},
    ]
    assert "cannot read duration of M_t02.mkv" in capsys.readouterr().err


def test_probe_staged_skips_preexisting_files(tmp_path):
    """Files that rested in the folder before the rip started (an earlier
    rip's) are never probed — the re-rip merge keeps their rows."""
    from skive.rip_movie import probe_staged

    title = tmp_path / "M"
    title.mkdir()
    old = title / "M_t02.mkv"
    old.write_bytes(b"x")
    new = title / "M_t10.mkv"
    new.write_bytes(b"x")

    rows = probe_staged(title, {old}, 6480, run=Mock(return_value=Mock(stdout="600\n")))

    assert rows == [{"file": "M_t10.mkv", "t": 10, "dur_s": 600,
                     "ratio": None, "band": None}]


def _preamble_for(tmp_path, pick=MAMMA, fast_scan=False):
    """A real Preamble (dataclass) for direct post_rip_validation calls:
    Mock log carrying a string run, MAMMA display, the volume label."""
    from skive.rip_common import Preamble
    log = Mock()
    log.run = "newrun1"
    title_dir = tmp_path / "Mamma Mia! (2008)"
    pre = Preamble(log=log, pick=pick, display="Mamma Mia! (2008)", media="DVD-ROM",
                   titles_full=[], title_dir=title_dir, stage=title_dir / "d0",
                   minlength=120, keep="", disc=0, label="MAMMA_MIA",
                   fast_scan=fast_scan)
    return pre, log


# --- Run view wiring (slice 06: absorption + suspend + run summary) ---------


def test_movie_pick_prints_normally_under_a_seam_view(monkeypatch, capsys):
    """The pick runs inside view.suspend(): the candidate table reaches
    stderr (scrollback) while a seam view is set, and the run completes after
    the redraw."""
    from skive import ui
    log, prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES, answers=("", ""))
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with pytest.raises(SystemExit) as e:
            rip_movie(FakeArgs())
    finally:
        ui.clear_view()
    assert e.value.code == 0
    err = capsys.readouterr().err
    assert "0.96" in err  # the shared table reached scrollback
    assert prompt.ask.call_count == 2  # main pick and shared confirm
    rip.assert_called_once()


def test_rip_movie_prints_run_summary_rows_and_count(monkeypatch, tmp_path, capsys):
    """The rip run summary: per-title rows (title × duration × size × result)
    and the ok/failed count, printed after the view closed."""
    from skive import rip_movie as rm

    title_dir = tmp_path / "rip" / "movies" / "Mamma Mia! (2008)"
    durs = {"Mamma Mia! (2008)_t21.mkv": 6249, "Mamma Mia! (2008)_t12.mkv": 6600}

    def stage_files(_display, _dir, **k):
        for name in durs:
            (title_dir / name).write_bytes(b"x")

    def fake_run(cmd, **k):
        return Mock(stdout=f"{durs[cmd[-1].split('/')[-1]]}.5\n")

    log, _prompt, _rip = _movie_flow(monkeypatch, MIXED_TITLES, answers=("21", ""))
    monkeypatch.setattr("skive.rip_movie.rename_staged", Mock(side_effect=stage_files))
    real = rm.post_rip_validation
    monkeypatch.setattr(rm, "post_rip_validation",
                        lambda pre, pex, main, rows, **k: real(pre, pex, main, rows, run=fake_run))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())
    assert e.value.code == 0

    err = capsys.readouterr().err
    for col in ("Title", "duration", "size", "result"):
        assert col in err
    rows21 = [l for l in err.splitlines() if "1:44:09" in l]
    assert any("_t21" in l and "1.0 B" in l and "ok" in l for l in rows21)
    assert "ok    2 ok\n" in err


def test_rip_movie_failed_rip_with_no_rows_ends_on_a_failure_line(monkeypatch, capsys):
    """makemkvcon fails and nothing staged: no summary rows, but the result
    line still reports the failure the exit code carries."""
    _log, _prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES, answers=("", ""))
    rip.return_value = 2

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 2
    assert capsys.readouterr().err.endswith("fail  failed · 0 ok\n")


def test_post_rip_flow_writes_probed_actuals_and_flags_main(monkeypatch, tmp_path, capsys):
    """Full-scan rip: validation.json lands at the title-folder root with the
    full movie shape, durations probed from the staged files (6249/6600 — not
    the scan table's 6252/6662), the main resolved to the final _tNN, and the
    report beside the subtitle note flags it."""
    import json
    from skive import rip_movie as rm

    title_dir = tmp_path / "rip" / "movies" / "Mamma Mia! (2008)"
    durs = {"Mamma Mia! (2008)_t21.mkv": 6249, "Mamma Mia! (2008)_t12.mkv": 6600}

    def stage_files(_display, _dir, **k):
        for name in durs:
            (title_dir / name).write_bytes(b"x")

    def fake_run(cmd, **k):
        return Mock(stdout=f"{durs[cmd[-1].split('/')[-1]]}.5\n")

    log, _prompt, _rip = _movie_flow(monkeypatch, MIXED_TITLES, answers=("21", ""))
    monkeypatch.setattr("skive.rip_movie.rename_staged", Mock(side_effect=stage_files))
    real = rm.post_rip_validation
    monkeypatch.setattr(rm, "post_rip_validation",
                        lambda pre, pex, main, rows, **k: real(pre, pex, main, rows, run=fake_run))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())
    assert e.value.code == 0

    doc = json.loads((title_dir / "validation.json").read_text(encoding="utf-8"))
    assert doc["tmdb"] == {"id": "157", "type": "movie",
                           "title": "Mamma Mia! (2008)", "runtime_s": 6480}
    assert doc["volume_label"] == ""
    assert doc["run"] == "a1b2c3d4"
    assert doc["main"] == "t21"
    assert doc["titles"] == [
        {"file": "Mamma Mia! (2008)_t12.mkv", "t": 12, "dur_s": 6600,
         "ratio": 1.02, "band": "green"},
        {"file": "Mamma Mia! (2008)_t21.mkv", "t": 21, "dur_s": 6249,
         "ratio": 0.96, "band": "green"},
    ]
    assert doc["encodes"] == []
    err = capsys.readouterr().err
    assert "Mamma Mia! (2008)_t21.mkv  1:44:09  green  MAIN" in err
    assert "Mamma Mia! (2008)_t12.mkv  1:50:00  green" in err
    assert "validation: 2 files, 1 green main, 0 yellow" in err
    log.note.assert_any_call("validation", "2 files, 1 green main, 0 yellow")


def test_post_rip_fast_scan_main_is_duration_joined_not_the_index(monkeypatch, tmp_path, capsys):
    """Fast scan (a -i movie run — the movie shape that still fast-scans,
    ADR 0015): the table's main is a scanner index (t0) — the recorded
    main is the real final _tNN joined by duration, never the index."""
    import json
    from skive import rip_common
    from skive import rip_movie as rm
    monkeypatch.setattr(rip_common, "fast_scan_dvd", Mock(return_value=[7021, 599]))

    title_dir = tmp_path / "rip" / "movies" / "Fast Movie (2021)"
    durs = {"Fast Movie (2021)_t09.mkv": 7020, "Fast Movie (2021)_t03.mkv": 599}

    def stage_files(_display, _dir, **k):
        for name in durs:
            (title_dir / name).write_bytes(b"x")

    def fake_run(cmd, **k):
        return Mock(stdout=f"{durs[cmd[-1].split('/')[-1]]}.0\n")

    _log, _prompt, _rip = _movie_flow(
        monkeypatch, [], pick=TMDBPick("Fast Movie", "2021", "en", "movie", "1"))
    monkeypatch.setattr("skive.rip_movie.rename_staged", Mock(side_effect=stage_files))
    real = rm.post_rip_validation
    monkeypatch.setattr(rm, "post_rip_validation",
                        lambda pre, pex, main, rows, **k: real(pre, pex, main, rows, run=fake_run))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs(include_extras=True))
    assert e.value.code == 0

    doc = json.loads((title_dir / "validation.json").read_text(encoding="utf-8"))
    assert doc["main"] == "t9"  # joined by duration — t0 was a scanner index


def test_post_rip_unreadable_duration_still_writes(tmp_path, capsys):
    """A file whose duration won't read: null row, one warning — and the
    document is still written (never crash, never skip the write)."""
    import json
    from skive.rip_movie import post_rip_validation

    pre, _log = _preamble_for(tmp_path)
    pre.title_dir.mkdir()
    (pre.title_dir / "Mamma Mia! (2008)_t21.mkv").write_bytes(b"x")

    post_rip_validation(pre, set(), "t21",
                        [{"t": 21, "dur_s": 6252, "ratio": None, "band": None}],
                        run=Mock(return_value=Mock(stdout="")))

    doc = json.loads((pre.title_dir / "validation.json").read_text(encoding="utf-8"))
    assert doc["titles"] == [{"file": "Mamma Mia! (2008)_t21.mkv", "t": 21,
                              "dur_s": None, "ratio": None, "band": None}]
    # Full scan: the final _tNN equals the table number — main stands even
    # without a readable duration.
    assert doc["main"] == "t21"
    assert "cannot read duration of Mamma Mia! (2008)_t21.mkv" in capsys.readouterr().err


def test_post_rip_part_file_probed_rows_and_main_resolve(tmp_path, capsys):
    """A multi-part rip's .pN file (<display>_tNN.pN.mkv) is probed like any
    staged file: its own row under its base title's number — in the report
    and the document — and the full-scan main still resolves with only the
    part present (transcode merges the parts by stream signature)."""
    import json
    from skive.rip_movie import post_rip_validation

    pre, _log = _preamble_for(tmp_path)
    pre.title_dir.mkdir()
    (pre.title_dir / "Mamma Mia! (2008)_t01.p2.mkv").write_bytes(b"x")

    post_rip_validation(pre, set(), "t1",
                        [{"t": 1, "dur_s": 3240, "ratio": 0.5, "band": "yellow"}],
                        run=Mock(return_value=Mock(stdout="3240.0\n")))

    doc = json.loads((pre.title_dir / "validation.json").read_text(encoding="utf-8"))
    assert doc["main"] == "t1"  # full scan: exact number ignores the .pN suffix
    assert doc["titles"] == [{"file": "Mamma Mia! (2008)_t01.p2.mkv", "t": 1,
                              "dur_s": 3240, "ratio": 0.5, "band": "yellow"}]
    err = capsys.readouterr().err
    assert "Mamma Mia! (2008)_t01.p2.mkv  0:54:00  yellow  MAIN" in err
    assert "validation: 1 files, 0 green main, 1 yellow" in err


def test_post_rip_rerip_same_id_appends(tmp_path):
    """Re-rip into the folder: same id — existing rows kept, this run's
    appended; this run's main resolution and run id win."""
    import json
    from skive.rip_movie import post_rip_validation

    pre, _log = _preamble_for(tmp_path)
    pre.title_dir.mkdir()
    old = {"tmdb": {"id": "157", "type": "movie", "title": "Mamma Mia! (2008)",
                    "runtime_s": 6480},
           "volume_label": "OLD", "run": "oldrun1", "main": "t12",
           "titles": [{"file": "Mamma Mia! (2008)_t12.mkv", "t": 12,
                       "dur_s": 6662, "ratio": 1.03, "band": "green"}],
           "encodes": []}
    (pre.title_dir / "validation.json").write_text(json.dumps(old), encoding="utf-8")
    (pre.title_dir / "Mamma Mia! (2008)_t21.mkv").write_bytes(b"x")

    post_rip_validation(pre, set(), "t21",
                        [{"t": 21, "dur_s": 6252, "ratio": 0.96, "band": "green"}],
                        run=Mock(return_value=Mock(stdout="6252\n")))

    doc = json.loads((pre.title_dir / "validation.json").read_text(encoding="utf-8"))
    assert doc["run"] == "newrun1"                      # latest wins
    assert doc["main"] == "t21"                         # this run's resolution wins
    assert [r["t"] for r in doc["titles"]] == [12, 21]  # old kept, new appended
    assert doc["volume_label"] == "MAMMA_MIA"


def test_post_rip_rerip_unresolved_main_keeps_old(tmp_path):
    """Re-rip where this run resolves no main: the old main stands."""
    import json
    from skive.rip_movie import post_rip_validation

    pre, _log = _preamble_for(tmp_path)
    pre.title_dir.mkdir()
    old = {"tmdb": {"id": "157", "type": "movie", "title": "Mamma Mia! (2008)",
                    "runtime_s": 6480},
           "volume_label": "OLD", "run": "oldrun1", "main": "t12",
           "titles": [], "encodes": []}
    (pre.title_dir / "validation.json").write_text(json.dumps(old), encoding="utf-8")
    (pre.title_dir / "Mamma Mia! (2008)_t02.mkv").write_bytes(b"x")

    post_rip_validation(pre, set(), None, [], run=Mock(return_value=Mock(stdout="600\n")))

    doc = json.loads((pre.title_dir / "validation.json").read_text(encoding="utf-8"))
    assert doc["main"] == "t12"
    assert [r["t"] for r in doc["titles"]] == [2]


def test_post_rip_different_id_refuses_and_keeps_old_file(tmp_path, capsys):
    """An existing validation.json naming a different film is never
    overwritten: stderr note, logged, the old file kept byte-for-byte."""
    from skive.rip_movie import post_rip_validation

    pre, log = _preamble_for(tmp_path)
    pre.title_dir.mkdir()
    old_text = '{\n  "tmdb": {"id": "999"}\n}'
    path = pre.title_dir / "validation.json"
    path.write_text(old_text, encoding="utf-8")
    (pre.title_dir / "Mamma Mia! (2008)_t21.mkv").write_bytes(b"x")

    post_rip_validation(pre, set(), "t21",
                        [{"t": 21, "dur_s": 6252, "ratio": None, "band": None}],
                        run=Mock(return_value=Mock(stdout="6252\n")))

    assert path.read_text(encoding="utf-8") == old_text
    assert "records a different film" in capsys.readouterr().err
    log.note.assert_any_call("validation", "existing validation.json names a different film — kept")
