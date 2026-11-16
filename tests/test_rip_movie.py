"""End-to-end tests for rip_movie with mocked hardware/network."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

import skive.rip_movie
from skive.rip_movie import rip_movie
from skive.scan import TitleInfo
from skive.tmdb import TMDBPick


@pytest.fixture(autouse=True)
def _stage_root(tmp_path, monkeypatch):
    """Keep staging off the real /mnt/videos."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))


@pytest.fixture(autouse=True)
def _table_accept(monkeypatch):
    """Every movie run states its scope in the selection table; default the
    gate to a tty view (live=False — never a Live under pytest) and an
    accepted empty-actions table so the flow tests keep their shape."""
    from skive import ui
    ui.set_view(ui.RunView(tty=True, live=False, perf=False))
    monkeypatch.setattr("skive.rip_movie.run_selection_table", lambda *a, **k: {})
    yield
    ui.clear_view()


class FakeArgs:
    """Minimal argparse.Namespace for rip_movie."""
    def __init__(
        self,
        part=None,
        tmdbid=None,
        verified=False,
        keep_all=False,
    ):
        self.part = part
        self.tmdbid = tmdbid
        self.verified = verified
        self.keep_all = keep_all


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

    _log, _prompt, _rip = _movie_flow(monkeypatch, MIXED_TITLES)
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


# --- Pre-rip gate: the selection table (map.md decision 3, issue 03) --------

MAMMA = TMDBPick("Mamma Mia!", "2008", "en", "movie", "157", runtime_secs=6480)

# The Mamma Mia disc: two feature-length titles near the 6480s runtime
MIXED_TITLES = [TitleInfo(21, 6252, 4_300_000_000, "dan", "", False),
                TitleInfo(12, 6662, 19_000_000_000, "dan", "", False)]


def _main_notes(log):
    """The (phase, text) notes a mock RipLog recorded under phase "main"."""
    return [tuple(c.args) for c in log.note.call_args_list if c.args[0] == "main"]


def test_table_several_near_prefills_biggest_with_the_note(monkeypatch, capsys):
    """Two titles near the runtime: biggest prefills as the main, the
    ambiguity lands in the run log, and the run rips that title alone."""
    log, _prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES)

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 0
    assert ("main", "main=t12 default (biggest bytes) — 2 titles near the runtime") in _main_notes(log)
    assert "rip set: t12 only" in capsys.readouterr().err
    rip.assert_called_once()
    assert rip.call_args[0][0] == [12]


def test_table_moved_main_is_the_rip_set(monkeypatch):
    """The accepted table states the scope: `m` at another row moves the
    single main, and the moved main is what rips."""
    log, _prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES,
                                    table_actions={21: "main", 12: "skip"})

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 0
    rip.assert_called_once()
    assert rip.call_args[0][0] == [21]


def test_table_accept_with_extras_rides_the_i_machinery(monkeypatch, tmp_path, capsys):
    """Accepted extra rows: main + extras rip together under the title
    suffix, the extras marker lands in validation.json (include, superfast —
    never forced), and the rip set line names both."""
    import json

    titles = [TitleInfo(21, 6252, 4_300_000_000, "dan", "", False),
              TitleInfo(12, 300, 500_000_000, "dan", "", False)]
    log, _prompt, rip = _movie_flow(monkeypatch, titles,
                                    table_actions={21: "main", 12: "extra"},
                                    root=tmp_path)

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 0
    rip.assert_called_once()
    assert rip.call_args[0][0] == [21, 12]
    err = capsys.readouterr().err
    assert "rip set: t21 + extras t12 (extras marked for encoding)" in err
    doc = json.loads((tmp_path / "rip" / "movies" / "Mamma Mia! (2008)"
                      / "validation.json").read_text())
    assert doc["extras"] == {"include": True, "quality": "superfast"}
    assert doc["main"] == "t21"


def test_table_quit_exits_1_with_nothing_ripped(monkeypatch, capsys):
    """q at the table quits with nothing ripped — no rip call at all."""
    _log, _prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES,
                                     table_actions="quit")

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 1
    rip.assert_not_called()
    assert "quit — nothing ripped." in capsys.readouterr().err


def test_table_interrupt_follows_the_repo_interrupt(monkeypatch, tmp_path, capsys):
    """Ctrl+C at the table: after Textual restores the terminal the flow's
    interrupt handler fires — partial purge, interrupted status, exit 130."""
    log, _prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES,
                                    table_actions="interrupt", root=tmp_path)

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 130
    rip.assert_not_called()
    log.interrupted.assert_called_once()


def test_movie_run_without_a_terminal_refuses(monkeypatch, capsys):
    """Non-TTY runs refuse before the table: one stderr line, nonzero exit,
    nothing spun."""
    from skive import ui
    _log, _prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES)
    ui.set_view(ui.RunView(tty=False, live=False, perf=False))
    table = Mock(side_effect=AssertionError("the table must not launch"))
    monkeypatch.setattr("skive.rip_movie.run_selection_table", table)
    try:
        with pytest.raises(SystemExit) as e:
            rip_movie(FakeArgs())
    finally:
        ui.clear_view()

    assert e.value.code == 1
    rip.assert_not_called()
    table.assert_not_called()
    err = capsys.readouterr().err
    assert "selection table needs a terminal" in err
    assert err.strip().endswith("selection table needs a terminal")


def test_movie_run_on_a_plain_tty_still_launches(monkeypatch, capsys):
    """NO_COLOR on a real terminal is plainness, not a missing terminal: the
    table still launches (stderr isatty() is true-terminal presence)."""
    import sys
    from skive import ui
    log, _prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES)
    monkeypatch.setenv("NO_COLOR", "1")
    ui.set_view(ui.RunView(tty=False, live=False, perf=False))
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True, raising=False)
    try:
        with pytest.raises(SystemExit) as e:
            rip_movie(FakeArgs())
    finally:
        ui.clear_view()

    assert e.value.code == 0
    rip.assert_called_once()


def test_table_accept_with_extras_never_marks_a_foreign_film(monkeypatch, tmp_path, capsys):
    """An existing validation.json naming a different film is left untouched
    by post_rip_validation — and the accepted-extras marker must not sneak
    an extras key into it either: the record stays byte-identical."""
    import json
    from skive import rip_movie as rm

    titles = [TitleInfo(21, 6252, 4_300_000_000, "dan", "", False),
              TitleInfo(12, 300, 500_000_000, "dan", "", False)]
    log, _prompt, rip = _movie_flow(monkeypatch, titles,
                                    table_actions={21: "main", 12: "extra"},
                                    root=tmp_path)
    from skive import rip_common
    monkeypatch.setattr(rip_common, "_confirm", Mock(return_value=True))
    title_dir = tmp_path / "rip" / "movies" / "Mamma Mia! (2008)"
    title_dir.mkdir(parents=True)
    foreign = {"tmdb": {"id": "999", "type": "movie", "title": "Other Film",
                        "runtime_s": 5400},
               "volume_label": "", "run": "oldrun1", "main": None,
               "titles": [], "encodes": []}
    path = title_dir / "validation.json"
    path.write_text(json.dumps(foreign), encoding="utf-8")
    before = path.read_bytes()

    def fake_run(cmd, **k):
        return Mock(stdout="6252\n")

    real = rm.post_rip_validation
    monkeypatch.setattr(rm, "post_rip_validation",
                        lambda pre, pex, main, rows, **k: real(pre, pex, main, rows, run=fake_run))

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 0
    assert path.read_bytes() == before
    assert "extras" not in json.loads(path.read_text(encoding="utf-8"))
    log.note.assert_any_call("validation",
                             "existing validation.json names a different film — kept")


def _movie_flow(monkeypatch, titles, pick=MAMMA, table_actions=None, root=None):
    """Movie flow seams for the pre-rip gate tests: full scan over `titles`,
    `pick` from search, `table_actions` as the selection table's accepted
    result (None = the audited prefill over `titles`). Returns (log, prompt, rip):
    the mock RipLog, the mock prompt, and the mock rip_titles."""
    from skive import rip_common

    log = Mock()
    log.run = "a1b2c3d4"  # the validation document's run identity
    prompt = Mock()
    rip = Mock(return_value=0)

    # Off the real NAS mount: completing runs stage a 1-byte .mkv that must
    # never reach the transcode box's rip/ tree.
    import tempfile
    monkeypatch.setenv("VIDEOS_MOUNT", str(root) if root else tempfile.mkdtemp())

    monkeypatch.setattr(rip_common, "RipLog", Mock(return_value=log))
    monkeypatch.setattr(rip_common, "sniff_disc", Mock(return_value=("DVD-ROM", "", "")))
    monkeypatch.setattr(rip_common, "scan_disc", Mock(return_value=("DVD-ROM", "", titles)))
    monkeypatch.setattr(rip_common, "ensure_mounted", Mock())
    monkeypatch.setattr(rip_common, "purge_junk", Mock())
    monkeypatch.setattr(rip_common, "require_online", Mock())
    monkeypatch.setattr(rip_common, "search_loop", Mock(return_value=pick))
    if table_actions is None:
        from skive import selection
        # the audited prefill logs its "main" outcome at gate time
        table_actions = selection.prefill(titles, pick.runtime_secs, log)[0]
    monkeypatch.setattr("skive.rip_movie.run_selection_table",
                        Mock(return_value=table_actions))
    monkeypatch.setattr("skive.rip_movie.inject_profile", Mock(return_value=Path("/tmp/test.xml")))
    monkeypatch.setattr("skive.rip_movie.rip_titles", rip)
    def _stage_one(_display, stage_dir, **k):
        # A completing run must leave a staged file — the zero-staged guard
        # (run ebe649b3) fails a run whose stage is empty at rc 0.
        (Path(stage_dir) / f"{_display}_t21.mkv").write_bytes(b"x")
    monkeypatch.setattr("skive.rip_movie.rename_staged", Mock(side_effect=_stage_one))
    monkeypatch.setattr("skive.rip_movie.subtitle_note", Mock(return_value="subs=none"))
    return log, prompt, rip


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


def _preamble_for(tmp_path, pick=MAMMA):
    """A real Preamble (dataclass) for direct post_rip_validation calls:
    Mock log carrying a string run, MAMMA display, the volume label."""
    from skive.rip_common import Preamble
    log = Mock()
    log.run = "newrun1"
    title_dir = tmp_path / "Mamma Mia! (2008)"
    pre = Preamble(log=log, pick=pick, display="Mamma Mia! (2008)", media="DVD-ROM",
                   titles_full=[], title_dir=title_dir, stage=title_dir / "d0",
                   minlength=120, keep="", disc=0, label="MAMMA_MIA")

    return pre, log


# --- Run view wiring (slice 06: absorption + suspend + run summary) ---------


def test_movie_pick_prints_normally_under_a_seam_view(monkeypatch, capsys):
    """The accepted table echo runs inside view.suspend(): the rip set line
    reaches stderr (scrollback) while a seam view is set, and the run
    completes after the redraw."""
    from skive import ui
    log, _prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with pytest.raises(SystemExit) as e:
            rip_movie(FakeArgs())
    finally:
        ui.clear_view()
    assert e.value.code == 0
    err = capsys.readouterr().err
    assert "rip set: t12 only" in err  # the accepted echo reached scrollback
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

    log, _prompt, _rip = _movie_flow(monkeypatch, MIXED_TITLES, table_actions={21: "main"}, root=tmp_path)
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
    _log, _prompt, rip = _movie_flow(monkeypatch, MIXED_TITLES)
    rip.return_value = 2
    monkeypatch.setattr("skive.rip_movie.rename_staged", Mock())  # nothing staged

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 2
    assert capsys.readouterr().err.endswith("fail  failed · 0 ok\n")


def test_rip_movie_rc0_zero_staged_fails_and_keeps_the_raw_log(monkeypatch, capsys):
    """The BD-RE case (run ebe649b3): makemkvcon aborts every title save but
    exits 0 — nothing staged must still fail the run (rc 1) so the raw log
    with the actual MakeMKV error survives finish()."""
    log, _prompt, _rip = _movie_flow(monkeypatch, MIXED_TITLES)
    monkeypatch.setattr("skive.rip_movie.rename_staged", Mock())  # nothing staged

    with pytest.raises(SystemExit) as e:
        rip_movie(FakeArgs())

    assert e.value.code == 1
    assert capsys.readouterr().err.endswith("fail  failed · 0 ok\n")
    log.finish.assert_called_once_with(1)
    status = [str(c.args[0]) for c in log.phase_end.call_args_list if c.args]
    assert any("nothing staged" in s for s in status)


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

    log, _prompt, _rip = _movie_flow(monkeypatch, MIXED_TITLES, table_actions={21: "main"}, root=tmp_path)
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


def _run_rip_movie_capturing_audio_note(args):
    """Run rip_movie with the standard mocked hardware/network scaffolding and
    return the RipLog instance so callers can assert on notes."""
    mock_pick = TMDBPick(title="Audio Note Movie", year="2021",
                         original_language="en", type="movie", id="42")
    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common._confirm", return_value=True), \
            patch("skive.rip_common.search_loop", return_value=mock_pick), \
            patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")), \
            patch("skive.rip_movie.rip_titles", return_value=0), \
            patch("skive.rip_movie.rename_staged"), \
            patch("skive.rip_movie.subtitle_note", return_value="subs=dan"), \
            patch("pathlib.Path.unlink"), \
            patch("skive.rip_common.RipLog") as MockLog:
        log_instance = MockLog.return_value
        log_instance.run = "run-1"
        try:
            rip_movie(args)
        except SystemExit:
            pass
        return log_instance


def test_rip_movie_audio_note_records_widened_and_keep_all():
    """The audio-phase note stays greppable: widened=… and keep-all=… fields
    are always present on the baseline run (widened=none, keep-all=no)."""
    log_instance = _run_rip_movie_capturing_audio_note(FakeArgs())
    audio_notes = [c.args for c in log_instance.note.call_args_list
                   if c.args and c.args[0] == "audio"]
    assert len(audio_notes) == 1
    text = audio_notes[0][1]
    assert text.startswith("kept=")
    assert "widened=none" in text
    assert "keep-all=no" in text


def test_rip_movie_audio_note_keep_all_override():
    """A -K/--keep-all run records keep-all=yes in the audio note."""
    log_instance = _run_rip_movie_capturing_audio_note(FakeArgs(keep_all=True))
    audio_notes = [c.args for c in log_instance.note.call_args_list
                   if c.args and c.args[0] == "audio"]
    assert len(audio_notes) == 1
    assert "keep-all=yes" in audio_notes[0][1]


def _rip_movie_flow(args, mock_pick, *, gate_return, title_dir, staged):
    gate = Mock(return_value=gate_return)
    """Drive rip_movie with the hardware/network mocked; the audio gate is the
    seam under test. Staged files are created in the title folder first."""
    # Staged files are created during the rip (files present at rip start are
    # preexisting and the gate must skip them), so rip_titles' stand-in lays
    # them down mid-flow.
    rip_titles_stub = Mock(return_value=0,
                           side_effect=lambda *a, **k: ([ (title_dir / n).touch() for n in staged ], 0)[1])
    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common._confirm", return_value=True), \
            patch("skive.rip_common.search_loop", return_value=mock_pick), \
            patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")), \
            patch("skive.rip_movie.rip_titles", rip_titles_stub), \
            patch("skive.rip_movie.rename_staged"), \
            patch("skive.rip_movie.subtitle_note", return_value="subs=dan"), \
            patch("skive.rip_movie.post_rip_validation", return_value=[{"file": staged[0], "dur_s": 600}] if staged else []), \
            patch("skive.rip_movie.silent_staged", gate), \
            patch("pathlib.Path.unlink"):
        try:
            rip_movie(args)
            return None, gate
        except SystemExit as e:
            return e.code, gate


def test_rip_movie_audio_gate_trips_and_keeps_staged(tmp_path, monkeypatch, capsys):
    """A silent main fails the run loudly — note names the file and the -K
    remedy — and the staged files stay put (ADR 0019/0022)."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    title_dir = tmp_path / "rip" / "movies" / "Test Movie (2020)"
    title_dir.mkdir(parents=True)
    main, extra = "Test Movie (2020)_t01.mkv", "Test Movie (2020) - Extra 01.mkv"
    args = FakeArgs(verified=True)
    mock_pick = TMDBPick(title="Test Movie", year="2020",
                         original_language="en", type="movie", id="12345")

    rc, gate = _rip_movie_flow(args, mock_pick, gate_return=[title_dir / main],
                               title_dir=title_dir, staged=[main, extra])

    assert rc == 1
    assert (title_dir / main).exists() and (title_dir / extra).exists()
    err = capsys.readouterr().err
    assert f"{main}: no audio stream" in err
    assert "--keep-all" in err
    # Extras are exempt: only the episode-shaped main is ever probed.
    assert gate.call_args.args[0] == [title_dir / main]


def test_rip_movie_audio_gate_healthy_is_silent(tmp_path, monkeypatch, capsys):
    """A main with audio passes with zero new output; rc stays 0."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    title_dir = tmp_path / "rip" / "movies" / "Test Movie (2020)"
    title_dir.mkdir(parents=True)
    main = "Test Movie (2020)_t01.mkv"
    args = FakeArgs(verified=True)
    mock_pick = TMDBPick(title="Test Movie", year="2020",
                         original_language="en", type="movie", id="12345")

    rc, gate = _rip_movie_flow(args, mock_pick, gate_return=[],
                               title_dir=title_dir, staged=[main])

    assert rc == 0
    assert (title_dir / main).exists()
    assert "no audio stream" not in capsys.readouterr().err


def test_rip_movie_subtitle_shortfall_warns_and_stays_green(tmp_path, monkeypatch):
    """A scan-promised keep-set language missing from the staged main warns
    (stderr + "subtitle" phase note in the run log) and never fails the run."""
    from skive import ui

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    warning = "Test Movie (2020)_t01.mkv: scan promised dan, staged none of it"
    title_dir = tmp_path / "rip" / "movies" / "Test Movie (2020)"
    title_dir.mkdir(parents=True)
    main = "Test Movie (2020)_t01.mkv"
    # A scan row whose subtitle track promises dan — the shortfall mapping
    # must carry the staged main with that promise.
    scan_row = TitleInfo(1, 5400, 4_300_000_000, "en", "dan", False)
    captured = {}

    def fake_rename_staged(*_a, **_k):
        (title_dir / main).write_bytes(b"x" * 1024)

    args = FakeArgs(verified=True)
    mock_pick = TMDBPick(title="Test Movie", year="2020", original_language="en",
                         type="movie", id="12345")

    with patch("skive.rip_common.sniff_disc", return_value=("DVD+R", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [scan_row])), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common._confirm", return_value=True), \
            patch("skive.rip_common.search_loop", return_value=mock_pick), \
            patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")), \
            patch("skive.rip_movie.rip_titles", return_value=0), \
            patch("skive.rip_movie.rename_staged", side_effect=fake_rename_staged), \
            patch("skive.rip_movie.subtitle_note", return_value="subs=none"), \
            patch("skive.rip_movie.post_rip_validation",
                  return_value=[{"file": main, "dur_s": 600}]), \
            patch("skive.rip_movie.subtitle_shortfall",
                  lambda files_promises: captured.update(files_promises) or [warning]), \
            patch("skive.ui.warn") as mock_warn, \
            patch("pathlib.Path.unlink"):
        try:
            rip_movie(args)
        except SystemExit as e:
            assert e.code == 0  # warn, never fail (ADR 0022)

    captured_expected = {title_dir / main: {"dan"}}
    assert captured == captured_expected, f"shortfall mapping {captured} != {captured_expected}"
    assert "Test Movie (2020)_t01.mkv: scan promised dan, staged none of it" in \
        [c.args[0] for c in mock_warn.call_args_list]
    lines = [line for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    notes = [line for line in lines if '"subtitle"' in line and "scan promised dan" in line]
    assert notes, "shortfall note missing from the subtitle phase in the run log"


def test_rip_movie_subtitle_shortfall_quiet_when_nothing_promised(tmp_path, monkeypatch):
    """No keep-set subtitles promised — zero shortfall output, rc 0."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))

    args = FakeArgs(verified=True)
    mock_pick = TMDBPick(title="Test Movie", year="2020", original_language="en",
                         type="movie", id="12345")

    with patch("skive.rip_common.sniff_disc", return_value=("DVD+R", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD+R", "", [])), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=mock_pick), \
            patch("skive.rip_movie.inject_profile", return_value=Path("/tmp/test.xml")), \
            patch("skive.rip_movie.rip_titles", return_value=0), \
            patch("skive.rip_movie.rename_staged"), \
            patch("skive.rip_movie.subtitle_note", return_value="subs=none"), \
            patch("skive.rip_movie.subtitle_shortfall", return_value=[]), \
            patch("skive.ui.warn") as mock_warn, \
            patch("pathlib.Path.unlink"):
        try:
            rip_movie(args)
        except SystemExit as e:
            assert e.code == 0

    mock_warn.assert_not_called()
