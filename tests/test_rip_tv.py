"""Tests for the TV rip flow: prompts, confirm table, renames, staging."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from dataclasses import dataclass
import pytest

from skive.rip_tv import (
    staged_eps, print_table, rename_tv, rip_tv, rename_tv_fast, episode_target,
)
from skive.scan import TitleInfo
from skive.tv_match import tv_floor
from rich.console import Console
from rich.table import Table


def test_staged_eps_singles_and_doubles():
    """Parse singles and doubles from filenames."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stage = Path(tmpdir)
        (stage / "Show (2000)_S01E01.mkv").touch()
        (stage / "Show (2000)_S01E05-E06.mkv").touch()
        (stage / "Other Show (2001)_S01E02.mkv").touch()  # Different show - ignored

        result = staged_eps(stage, "Show (2000)", 1)
        assert result == {1, 5, 6}


def test_staged_eps_second_disc():
    """A second disc's staged set shows what disc one already laid down."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stage = Path(tmpdir)
        # Created by disc one: a single and a double
        (stage / "Show (2000)_S01E01.mkv").touch()
        (stage / "Show (2000)_S01E05-E06.mkv").touch()

        staged = staged_eps(stage, "Show (2000)", 1)

        assert staged == {1, 5, 6}


def test_staged_sets_read_the_title_folder_after_promotion(tmp_path):
    """The re-pointed staging shape (slice 7): episodes promoted out of d{disc}
    rest directly in the show's title folder, and staged_eps / show_staging
    read them there; the drive subdir holds nothing once promotion has run."""
    from skive.rip_tv import show_staging

    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    disc_dir = show_dir / "d0"
    disc_dir.mkdir(parents=True)
    (show_dir / "Show (2000)_S01E01.mkv").touch()
    (show_dir / "Show (2000)_S01E05-E06.mkv").touch()
    (show_dir / "Show (2000) - Extra 01.mkv").touch()

    assert staged_eps(show_dir, "Show (2000)", 1) == {1, 5, 6}
    assert [p.name for p in show_staging(show_dir, "Show (2000)")] == [
        "Show (2000) - Extra 01.mkv",
        "Show (2000)_S01E01.mkv",
        "Show (2000)_S01E05-E06.mkv",
    ]
    assert not list(disc_dir.glob("*.mkv"))


def _gk_titles():
    """Generation Kill disc 1 shape: three episodes and a play-all (t3)."""
    return [TitleInfo(0, 3932, 1, "dan", "", False),
            TitleInfo(1, 3897, 1, "dan", "", False),
            TitleInfo(2, 3653, 1, "dan", "", False),
            TitleInfo(3, 11482, 1, "dan", "", False)]


def _range_flow(tmp_path, monkeypatch, mock_titles, mock_episodes, answers, staged_names=()):
    """Drive rip_tv through season/range/confirm prompts over a temp stage.

    staged_names are created in the show's title folder before the run
    (already-ripped episodes shape the pre-fill). Returns (rename_tv mock,
    console mock, rc).
    """
    from skive.tmdb import TMDBPick

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True, exist_ok=True)
    for name in staged_names:
        (show_dir / name).touch()

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True

    prompt = MagicMock()
    prompt.ask.side_effect = answers

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=TMDBPick(
                            title="Show", year="2000", original_language="en", type="tv", id="5")):
                        with patch("skive.rip_tv.Prompt", prompt):
                            with patch("skive.rip_tv.fetch_season", return_value=mock_episodes):
                                with patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent-test.xml")):
                                    with patch("skive.rip_tv.rip_titles", return_value=0):
                                        with patch("skive.rip_tv.rename_tv") as mock_rename_tv:
                                            with patch("skive.rip_tv.subtitle_note", return_value="subs=dan"):
                                                with patch("skive.rip_tv.console") as mock_console:
                                                    try:
                                                        rip_tv(FakeArgs())
                                                    except SystemExit as e:
                                                        return mock_rename_tv, mock_console, e.code
    return mock_rename_tv, mock_console, None


def test_range_prefill_skips_staged_and_fills_disc(tmp_path, monkeypatch):
    """The map's regression fixture: E01-E03 staged, this disc carries E04-E06.

    Enter accepts the pre-fill 4-6; the three eligible titles get E4-E6 in
    disc order and the play-all (t3) stays extra.
    """
    episodes = [(e, f"ep{e}", 3600) for e in range(1, 7)]
    staged = [f"Show (2000)_S01E0{e}.mkv" for e in (1, 2, 3)]

    mock_rename_tv, _console, rc = _range_flow(
        tmp_path, monkeypatch, _gk_titles(), episodes, ["", "", ""], staged)

    assert rc == 0
    assert mock_rename_tv.call_args.args[3] == {0: (4, 4), 1: (5, 5), 2: (6, 6), 3: (0, 0)}


def test_range_mismatch_note_printed(tmp_path, monkeypatch):
    """More eligible titles than the range covers: the tail is extra and a
    one-line note names the fix-row escape."""
    titles = [TitleInfo(0, 1260, 1, "dan", "", False),
              TitleInfo(1, 1300, 1, "dan", "", False),
              TitleInfo(2, 1250, 1, "dan", "", False),
              TitleInfo(3, 1290, 1, "dan", "", False)]
    episodes = [(e, f"ep{e}", 1250) for e in range(1, 5)]

    mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, titles, episodes, ["", "1-3", ""])

    assert rc == 0
    assert mock_rename_tv.call_args.args[3] == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (0, 0)}
    printed = " ".join(str(c.args[0]) for c in console.print.call_args_list if c.args)
    assert "eligible titles: 4 but 1-3 covers 3" in printed
    assert "title 3 left extra; fix with 3=e" in printed


def test_n_command_reasks_range(tmp_path, monkeypatch):
    """`n` re-asks the range with a fresh pre-fill (`n=5` spells the same).

    First answer 1-2 leaves t2 extra; n re-asks, the user states 5, and the
    rebuild gives t0=E5 with the rest extra.
    """
    episodes = [(e, f"ep{e}", 3600) for e in range(1, 6)]

    mock_rename_tv, _console, rc = _range_flow(
        tmp_path, monkeypatch, _gk_titles(), episodes, ["", "1-2", "n", "5", ""])

    assert rc == 0
    assert mock_rename_tv.call_args.args[3] == {0: (5, 5), 1: (0, 0), 2: (0, 0), 3: (0, 0)}


def test_n_equals_5_spells_bare_n(tmp_path, monkeypatch):
    """`n=5` at the table spells the same as bare `n`: the =-suffix is ignored
    and the range prompt follows (season, range, confirm, re-ask range,
    confirm). Stating 5 there gives t0=E5, the rest extra."""
    episodes = [(e, f"ep{e}", 3600) for e in range(1, 6)]

    mock_rename_tv, _console, rc = _range_flow(
        tmp_path, monkeypatch, _gk_titles(), episodes, ["", "1-2", "n=5", "5", ""])

    assert rc == 0
    assert mock_rename_tv.call_args.args[3][0] == (5, 5)


def test_fix_row_double_at_table(tmp_path, monkeypatch):
    """A fix row may still turn one title into a double (1=1-2).

    Stating range 3-3 leaves t1 extra with E1-E2 free; the double repoints it.
    """
    titles = [TitleInfo(0, 2400, 1, "dan", "", False),
              TitleInfo(1, 2450, 1, "dan", "", False)]
    episodes = [(e, f"ep{e}", 2400) for e in range(1, 5)]

    mock_rename_tv, _console, rc = _range_flow(
        tmp_path, monkeypatch, titles, episodes, ["", "3", "1=1-2", ""])

    assert rc == 0
    assert mock_rename_tv.call_args.args[3] == {0: (3, 3), 1: (1, 2)}


def test_print_table_audit_column_generation_kill(mock_console):
    """Generation Kill disc 1 renders 0.95/0.96/0.95 green; the play-all's
    audit cell stays blank."""
    titles = [(0, 3932, 1), (1, 3897, 1), (2, 3653, 1), (3, 11482, 1)]
    assignment = {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (0, 0)}
    ep_names = {1: "Get Some", 2: "The Cradle", 3: "Sic(vis)"}
    ep_runtimes = {1: 4140, 2: 4080, 3: 3840}

    print_table("Generation Kill (2008)", 1, titles, assignment, ep_names, ep_runtimes)

    mock_console.print.assert_called()
    table = next(c.args[0] for c in mock_console.print.call_args_list
                 if c.args and isinstance(c.args[0], Table))

    plain = Console(record=True, width=100)
    plain.print(table)
    text = plain.export_text()
    assert "audit" in text
    assert text.count("0.95") == 2 and "0.96" in text  # the three episode rows
    extra = next(ln for ln in text.splitlines() if "3:11:22" in ln)  # t3 row
    assert "0.95" not in extra and "0.96" not in extra  # extra audits blank

    styled = Console(record=True, width=100, color_system="standard")
    styled.print(table)
    # Exactly the three ratio cells are green; nothing else in the table is.
    assert styled.export_text(styles=True).count("\x1b[32m") == 3


def test_naming_doubles():
    """Double episodes name correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stage = Path(tmpdir)

        # Create a junk-labeled file
        (stage / "B1_t01.mkv").touch()

        assignment = {1: (1, 2)}  # Title 1 (B1_t01) -> S01E01-E02
        ep_names = {1: "Ep 1", 2: "Ep 2"}

        rename_tv("Show (2000)", stage, 1, assignment)

        # Should have renamed
        assert (stage / "B1_t01.mkv").exists() is False
        assert (stage / "Show (2000)_S01E01-E02.mkv").exists()


def test_naming_specials():
    """Season 0 (specials) names correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stage = Path(tmpdir)
        (stage / "C2_t03.mkv").touch()

        assignment = {3: (1, 1)}  # Title 3 (C2_t03), season 0
        ep_names = {1: "Pilot"}

        rename_tv("Show (2000)", stage, 0, assignment)

        assert (stage / "Show (2000)_S00E01.mkv").exists()


def test_naming_extras_rolling():
    """Extras roll past existing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stage = Path(tmpdir)

        # Existing extra
        (stage / "Show (2000) - Extra 01.mkv").touch()

        # New junk file
        (stage / "D1_t02.mkv").touch()

        assignment = {2: (0, 0)}  # Title 2 is extra

        rename_tv("Show (2000)", stage, 1, assignment)

        # Should create Extra 02 (rolling past Extra 01)
        assert (stage / "Show (2000) - Extra 02.mkv").exists()


def test_naming_no_clobber():
    """Never clobber existing target."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stage = Path(tmpdir)

        # Existing episode file
        (stage / "Show (2000)_S01E01.mkv").touch()

        # Junk file we'd like to rename
        (stage / "B1_t02.mkv").touch()

        assignment = {0: (1, 1)}

        rename_tv("Show (2000)", stage, 1, assignment)

        # Original should stay, junk file untouched with warning
        assert (stage / "Show (2000)_S01E01.mkv").exists()
        assert (stage / "B1_t02.mkv").exists()


def test_extras_flow():
    """Test --extras bypasses season/table and uses suffix e."""
    from unittest.mock import MagicMock, patch
    from dataclasses import dataclass
    from skive.tmdb import TMDBPick

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = True
        tmdbid = None
        verified = True

    args = FakeArgs()

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=TMDBPick(
                        title="Test Show", year="2020", original_language="en", type="tv", id="123"
                    )):
                        with patch("skive.rip_tv.rename_staged") as mock_rename:
                            with patch("skive.rip_tv.subtitle_note", return_value="subs=dan"):
                                with patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/test.xml")):
                                    with patch("skive.rip_tv.rip_titles", return_value=0):
                                        with patch("pathlib.Path.unlink"):
                                            try:
                                                rip_tv(args)
                                                # Should exit with rc from rip_titles
                                            except SystemExit as e:
                                                assert e.code == 0  # Default rip_titles returns 0

    # Check rename was called with suffix="e"
    mock_rename.assert_called()
    call_kwargs = mock_rename.call_args[1]
    assert call_kwargs.get("suffix") == "e"


def test_rip_only_requested_titles_that_survived_scan():
    """Requested titles absent from the confirmed scan are not ripped.

    -T 0,2 asks for titles 0 and 2; the scan only confirms t0, so the rip and
    the table see t0 alone.
    """
    from unittest.mock import MagicMock, patch
    from skive.tmdb import TMDBPick
    from rich.prompt import Prompt

    @dataclass
    class FakeArgs:
        titles = "0,2"
        extras = False
        tmdbid = None
        verified = True

    args = FakeArgs()

    mock_titles = [TitleInfo(0, 1260, 1, "dan", "", False),
                   TitleInfo(1, 1300, 1, "dan", "", False)]  # Two titles
    mock_episodes = [(1, "ep1", 0), (2, "ep2", 0)]  # No runtimes

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=TMDBPick(
                        title="Test Show", year="2020", original_language="en", type="tv", id="123"
                    )):
                        with patch("skive.rip_tv.fetch_season", return_value=mock_episodes):
                            with patch("skive.rip_tv.staged_eps", return_value=set()):
                                with patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/test.xml")):
                                    with patch("skive.rip_tv.rip_titles", return_value=0) as mock_rip_titles:
                                        with patch("skive.rip_tv.rename_tv") as mock_rename_tv:
                                            with patch("skive.rip_tv.subtitle_note", return_value="subs=dan"):
                                                with patch("skive.rip_common.RipLog") as mock_log_class:
                                                    mock_log = MagicMock()
                                                    mock_log_class.return_value = mock_log
                                                    with patch("skive.rip_tv.Prompt") as mock_prompt:
                                                        # season, then Enter for the range, Enter=rip
                                                        mock_prompt.ask.side_effect = ["", "", ""]
                                                        try:
                                                            rip_tv(args)
                                                        except SystemExit as e:
                                                            pass

    # Title 2 was requested but absent from the confirmed scan, so it must not be ripped.
    assert mock_rip_titles.call_args.args[0] == [0]
    mock_rename_tv.assert_called()


# Test discipline fixtures
@pytest.fixture(autouse=True)
def _stage_root(tmp_path, monkeypatch):
    """Keep staging off the real /mnt/videos."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))


@pytest.fixture
def mock_console():
    """Mock rich console for print_table tests."""
    with patch("skive.rip_tv.console") as m:
        yield m


def test_show_staging_scopes_to_one_show(tmp_path):
    """show_staging returns the show's episodes and extras, nothing else."""
    from skive.rip_tv import show_staging
    for name in ["Generation Kill (2008)_S01E01-E02.mkv",
                 "Generation Kill (2008)_S02E03.mkv",
                 "Generation Kill (2008) - Extra 01.mkv",
                 "Other Show (1999)_S01E01.mkv",
                 "B1_t00.mkv",
                 "Generation Kill (2008).mkv"]:
        (tmp_path / name).touch()
    files = show_staging(tmp_path, "Generation Kill (2008)")
    assert [f.name for f in files] == [
        "Generation Kill (2008) - Extra 01.mkv",
        "Generation Kill (2008)_S01E01-E02.mkv",
        "Generation Kill (2008)_S02E03.mkv",
    ]


def _clear_staged_flow(tmp_path, monkeypatch, answers):
    """Run rip tv over a poisoned staging dir; return the poisoned path."""
    from unittest.mock import MagicMock, patch
    from dataclasses import dataclass
    from skive.tmdb import TMDBPick
    from skive.rip_tv import rip_tv

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True

    mock_titles = [TitleInfo(0, 21 * 60, 1, "dan", "", False),
                   TitleInfo(1, 22 * 60, 1, "dan", "", False),
                   TitleInfo(2, 23 * 60, 1, "dan", "", False)]
    # fetch_season is mocked past its minutes->seconds conversion
    episodes = [(1, "ep1", 21 * 60), (2, "ep2", 22 * 60), (3, "ep3", 23 * 60)]

    poisoned = show_dir / "Show (2000)_S01E01-E02.mkv"
    show_dir.mkdir(parents=True, exist_ok=True)
    poisoned.touch()

    prompt = MagicMock()
    prompt.ask.side_effect = answers

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=TMDBPick(
                            title="Show", year="2000", original_language="en", type="tv", id="5")):
                        with patch("skive.rip_tv.Prompt", prompt):
                            with patch("skive.rip_tv.fetch_season", return_value=episodes):
                                with patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent-test.xml")):
                                    with patch("skive.rip_tv.rip_titles", return_value=0):
                                        with patch("skive.rip_tv.rename_tv"):
                                            with patch("skive.rip_tv.subtitle_note", return_value="subs=dan"):
                                                try:
                                                    rip_tv(FakeArgs())
                                                except SystemExit as e:
                                                    return poisoned, e.code
    return poisoned, None


def test_clear_staged_at_table_deletes_and_rips(tmp_path, monkeypatch):
    """c + y at the table deletes the show's staged files and rips."""
    poisoned, rc = _clear_staged_flow(tmp_path, monkeypatch,
                                      ["1", "", "c", "y", ""])  # season, range, clear, yes, Enter=rip
    assert not poisoned.exists()
    assert rc == 0


def test_clear_staged_declined_keeps_files(tmp_path, monkeypatch):
    """Declining the clear leaves staging untouched."""
    poisoned, rc = _clear_staged_flow(tmp_path, monkeypatch,
                                      ["1", "", "c", "n", "q"])  # season, range, clear, no, quit
    assert poisoned.exists()
    assert rc == 1


def test_floor_drops_short_titles_before_rip():
    """Under-floor titles (30s menu loop) never reach the table or the rip.

    Same patch set as test_n_command_renumbers_in_order; the short title is
    gone from titles_info before matching, so total= and the assignment the
    rip renames with only ever see the long title.
    """
    from skive.tmdb import TMDBPick

    @dataclass
    class FakeArgs:
        titles = None
        extras = False
        tmdbid = None
        verified = True

    mock_titles = [TitleInfo(0, 2400, 1, "dan", "", False),
                   TitleInfo(1, 30, 1, "dan", "", False)]  # 30s menu loop
    mock_episodes = [(1, "ep1", 2400)]

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=TMDBPick(
                        title="Test Show", year="2020", original_language="en", type="tv", id="123"
                    )):
                        with patch("skive.rip_tv.fetch_season", return_value=mock_episodes):
                            with patch("skive.rip_tv.staged_eps", return_value=set()):
                                with patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/test.xml")):
                                    with patch("skive.rip_tv.rip_titles", return_value=0) as mock_rip_titles:
                                        with patch("skive.rip_tv.rename_tv") as mock_rename_tv:
                                            with patch("skive.rip_tv.subtitle_note", return_value="subs=dan"):
                                                with patch("skive.rip_common.RipLog"):
                                                    with patch("skive.rip_tv.Prompt") as mock_prompt:
                                                        # season, Enter for the range, Enter=rip at the table
                                                        mock_prompt.ask.side_effect = ["", "", ""]
                                                        try:
                                                            rip_tv(FakeArgs())
                                                        except SystemExit:
                                                            pass

    # The 30s title is not counted in the rip and gets no assignment row.
    assert mock_rip_titles.call_args.kwargs["total"] == 1
    assert mock_rename_tv.call_args.args[3] == {0: (1, 1)}


def test_playall_forced_to_extra():
    """A play-all is forced back to extra even when the stated range would
    cover it.

    Title 2 (3550s) sits within 120s of t0+t1 (3500s): sum-disqualification
    pins it to (0, 0) whatever range the user states.
    """
    from skive.tmdb import TMDBPick

    @dataclass
    class FakeArgs:
        titles = None
        extras = False
        tmdbid = None
        verified = True

    mock_titles = [TitleInfo(0, 1800, 1, "dan", "", False),
                   TitleInfo(1, 1700, 1, "dan", "", False),
                   TitleInfo(2, 3550, 1, "dan", "", False)]
    mock_episodes = [(1, "ep1", 1800), (2, "ep2", 1700), (3, "ep3", 3550)]

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=TMDBPick(
                        title="Test Show", year="2020", original_language="en", type="tv", id="123"
                    )):
                        with patch("skive.rip_tv.fetch_season", return_value=mock_episodes):
                            with patch("skive.rip_tv.staged_eps", return_value=set()):
                                with patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/test.xml")):
                                    with patch("skive.rip_tv.rip_titles", return_value=0):
                                        with patch("skive.rip_tv.rename_tv") as mock_rename_tv:
                                            with patch("skive.rip_tv.subtitle_note", return_value="subs=dan"):
                                                with patch("skive.rip_common.RipLog"):
                                                    with patch("skive.rip_tv.Prompt") as mock_prompt:
                                                        # season, then Enter for the range, Enter=rip
                                                        mock_prompt.ask.side_effect = ["", "", ""]
                                                        try:
                                                            rip_tv(FakeArgs())
                                                        except SystemExit:
                                                            pass

    assert mock_rename_tv.call_args.args[3][2] == (0, 0)


def test_playall_pin_survives_n_reask():
    """The `n` re-ask re-applies the play-all pin (Generation Kill shape).

    The stated range 1-3 maps t0-t2 to E1-E3 and leaves the play-all extra;
    re-asking with `n` and taking the same pre-fill must not turn the
    play-all into an episode: t0-t2 -> E1-E3, t3 -> extra.
    """
    from skive.tmdb import TMDBPick

    @dataclass
    class FakeArgs:
        titles = None
        extras = False
        tmdbid = None
        verified = True

    mock_titles = [TitleInfo(0, 3932, 1, "dan", "", False),
                   TitleInfo(1, 3897, 1, "dan", "", False),
                   TitleInfo(2, 3653, 1, "dan", "", False),
                   TitleInfo(3, 11482, 1, "dan", "", False)]
    mock_episodes = [(1, "ep1", 3932), (2, "ep2", 3897), (3, "ep3", 3653), (4, "ep4", 3600)]

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=TMDBPick(
                        title="Test Show", year="2020", original_language="en", type="tv", id="123"
                    )):
                        with patch("skive.rip_tv.fetch_season", return_value=mock_episodes):
                            with patch("skive.rip_tv.staged_eps", return_value=set()):
                                with patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/test.xml")):
                                    with patch("skive.rip_tv.rip_titles", return_value=0):
                                        with patch("skive.rip_tv.rename_tv") as mock_rename_tv:
                                            with patch("skive.rip_tv.subtitle_note", return_value="subs=dan"):
                                                with patch("skive.rip_common.RipLog"):
                                                    with patch("skive.rip_tv.Prompt") as mock_prompt:
                                                        # season, range 1-3, n re-ask, Enter for pre-fill, Enter=rip
                                                        mock_prompt.ask.side_effect = ["", "1-3", "n", "", ""]
                                                        try:
                                                            rip_tv(FakeArgs())
                                                        except SystemExit:
                                                            pass

    assert mock_rename_tv.call_args.args[3] == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (0, 0)}


def test_fix_row_can_assign_playall_to_episode():
    """A typed fix row on a play-all sticks — the deliberate escape hatch.

    Same Generation Kill disc as above, but the user accepts the pre-filled
    range and types `3=5`, deliberately making the play-all E5. Only the
    automatic rebuilds re-pin; a fix row is never reverted.
    """
    from skive.tmdb import TMDBPick

    @dataclass
    class FakeArgs:
        titles = None
        extras = False
        tmdbid = None
        verified = True

    mock_titles = [TitleInfo(0, 3932, 1, "dan", "", False),
                   TitleInfo(1, 3897, 1, "dan", "", False),
                   TitleInfo(2, 3653, 1, "dan", "", False),
                   TitleInfo(3, 11482, 1, "dan", "", False)]
    mock_episodes = [(1, "ep1", 3932), (2, "ep2", 3897), (3, "ep3", 3653),
                     (4, "ep4", 3600), (5, "ep5", 3700)]

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
        with patch("skive.rip_common.ensure_mounted"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=TMDBPick(
                        title="Test Show", year="2020", original_language="en", type="tv", id="123"
                    )):
                        with patch("skive.rip_tv.fetch_season", return_value=mock_episodes):
                            with patch("skive.rip_tv.staged_eps", return_value=set()):
                                with patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/test.xml")):
                                    with patch("skive.rip_tv.rip_titles", return_value=0):
                                        with patch("skive.rip_tv.rename_tv") as mock_rename_tv:
                                            with patch("skive.rip_tv.subtitle_note", return_value="subs=dan"):
                                                with patch("skive.rip_common.RipLog"):
                                                    with patch("skive.rip_tv.Prompt") as mock_prompt:
                                                        # season, then Enter for the range, 3=5 at the table, Enter=rip
                                                        mock_prompt.ask.side_effect = ["", "", "3=5", ""]
                                                        try:
                                                            rip_tv(FakeArgs())
                                                        except SystemExit:
                                                            pass

    assert mock_rename_tv.call_args.args[3] == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (5, 5)}


# --- Fast scan: duration join + rename (ADR 0002, DVD half) ---
GK_DURATIONS = [3932, 3897, 3653, 11482]


def _ffprobe_run(durations: dict[str, int]):
    """Fake subprocess.run: ffprobe returns the mapped duration for a file."""
    def fake_run(cmd, **kwargs):
        assert cmd[:1] == ["ffprobe"]
        name = Path(cmd[-1]).name
        return Mock(returncode=0, stdout=f"{durations[name]}\n")
    return fake_run


def test_rename_tv_fast_happy_path(tmp_path):
    """Four GK junk files join by duration: three episodes + one extra named."""
    for n, dur in enumerate(GK_DURATIONS):
        (tmp_path / f"GENERATION_KILL_t{n}.mkv").touch()

    rename_tv_fast("Show (2000)", tmp_path, 1,
                   {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (0, 0)},
                   GK_DURATIONS, run=_ffprobe_run({f"GENERATION_KILL_t{n}.mkv": d
                                                   for n, d in enumerate(GK_DURATIONS)}))

    assert (tmp_path / "Show (2000)_S01E01.mkv").exists()
    assert (tmp_path / "Show (2000)_S01E02.mkv").exists()
    assert (tmp_path / "Show (2000)_S01E03.mkv").exists()
    assert (tmp_path / "Show (2000) - Extra 01.mkv").exists()


def test_rename_tv_fast_duplicate_duration_keeps_lowest_tnn(tmp_path, capsys):
    """Two staged files within ±1s of one title: lowest _tNN wins, the loser
    is warned and stays junk-labeled."""
    (tmp_path / "GENERATION_KILL_t00.mkv").touch()
    (tmp_path / "GENERATION_KILL_t05.mkv").touch()  # duplicate/decoy of t00

    rename_tv_fast("Show (2000)", tmp_path, 1, {0: (1, 1)}, [3932],
                   run=_ffprobe_run({"GENERATION_KILL_t00.mkv": 3932,
                                     "GENERATION_KILL_t05.mkv": 3932}))

    assert (tmp_path / "Show (2000)_S01E01.mkv").exists()
    assert (tmp_path / "GENERATION_KILL_t05.mkv").exists()  # loser stays junk
    err = capsys.readouterr().err
    assert "t05" in err
    assert err.count("GENERATION_KILL_t05") == 1  # one warning, not one per join


def test_rename_tv_fast_second_claimant_never_warned_as_loser(tmp_path, capsys):
    """Two scanner titles share a duration, two staged files: the file title 1
    claims gets renamed — it was never a loser, so no junk warning. One
    disposition per staged file."""
    (tmp_path / "GENERATION_KILL_t00.mkv").touch()
    (tmp_path / "GENERATION_KILL_t01.mkv").touch()

    ok = rename_tv_fast("Show (2000)", tmp_path, 1,
                        {0: (1, 1), 1: (2, 2)}, [3932, 3932],
                        run=_ffprobe_run({"GENERATION_KILL_t00.mkv": 3932,
                                          "GENERATION_KILL_t01.mkv": 3932}))

    assert ok is True
    assert (tmp_path / "Show (2000)_S01E01.mkv").exists()
    assert (tmp_path / "Show (2000)_S01E02.mkv").exists()
    err = capsys.readouterr().err
    assert "junk-labeled" not in err  # title 1 claimed it — no loser warning
    assert "GENERATION_KILL → Show (2000)_S01E01" in err
    assert "GENERATION_KILL → Show (2000)_S01E02" in err


def test_rename_tv_fast_double_loser_warned_once(tmp_path, capsys):
    """A file losing two same-duration joins is warned exactly once, in the
    single post-join pass — not once per losing title."""
    for n in range(3):
        (tmp_path / f"GENERATION_KILL_t{n:02d}.mkv").touch()

    rename_tv_fast("Show (2000)", tmp_path, 1,
                   {0: (1, 1), 1: (2, 2)}, [3932, 3932],
                   run=_ffprobe_run({f"GENERATION_KILL_t{n:02d}.mkv": 3932
                                     for n in range(3)}))

    assert (tmp_path / "Show (2000)_S01E01.mkv").exists()
    assert (tmp_path / "Show (2000)_S01E02.mkv").exists()
    assert (tmp_path / "GENERATION_KILL_t02.mkv").exists()  # loser stays junk
    assert capsys.readouterr().err.count("GENERATION_KILL_t02") == 1


def test_rename_tv_fast_unjoinable_file_stays_junk(tmp_path, capsys):
    """A staged file within no scanner title's ±1s is warned and left alone."""
    (tmp_path / "GENERATION_KILL_t00.mkv").touch()
    (tmp_path / "GENERATION_KILL_t09.mkv").touch()  # 500s menu loop: unjoinable

    rename_tv_fast("Show (2000)", tmp_path, 1, {0: (1, 1)}, [3932],
                   run=_ffprobe_run({"GENERATION_KILL_t00.mkv": 3932,
                                     "GENERATION_KILL_t09.mkv": 500}))

    assert (tmp_path / "Show (2000)_S01E01.mkv").exists()
    assert (tmp_path / "GENERATION_KILL_t09.mkv").exists()
    assert "matches no scanned title" in capsys.readouterr().err


def test_rename_tv_fast_missing_episode_aborts_before_any_rename(tmp_path, capsys):
    """An eligible title without a staged file returns False and renames NOTHING."""
    (tmp_path / "GENERATION_KILL_t00.mkv").touch()  # only title 0's file

    ok = rename_tv_fast("Show (2000)", tmp_path, 1,
                        {0: (1, 1), 2: (3, 3)},  # title 2 -> E3 has no file
                        [3932, 3897, 3653],
                        run=_ffprobe_run({"GENERATION_KILL_t00.mkv": 3932}))

    assert ok is False
    err = capsys.readouterr().err
    assert "title 2 (E03) has no staged file — rename aborted" in err
    assert (tmp_path / "GENERATION_KILL_t00.mkv").exists()  # nothing renamed
    assert not (tmp_path / "Show (2000)_S01E01.mkv").exists()


def test_rename_tv_fast_missing_double_names_the_range(tmp_path, capsys):
    """The abort line names a double assignment as E01-E02."""
    ok = rename_tv_fast("Show (2000)", tmp_path, 1, {0: (1, 2)}, [3932],
                        run=_ffprobe_run({}))
    assert ok is False
    assert "title 0 (E01-E02)" in capsys.readouterr().err


def test_rename_tv_fast_unmatched_extra_warns_without_abort(tmp_path, capsys):
    """An extra (lo=0) with no staged file only warns; episodes still rename."""
    (tmp_path / "GENERATION_KILL_t00.mkv").touch()

    rename_tv_fast("Show (2000)", tmp_path, 1,
                   {0: (1, 1), 3: (0, 0)},  # title 3 extra: file never ripped
                   [3932, 3897, 3653, 11482],
                   run=_ffprobe_run({"GENERATION_KILL_t00.mkv": 3932}))

    assert (tmp_path / "Show (2000)_S01E01.mkv").exists()
    assert "title 3" not in capsys.readouterr().err


def test_rename_tv_fast_never_clobbers(tmp_path, capsys):
    """An existing episode target wins; the joined file stays junk-labeled."""
    (tmp_path / "Show (2000)_S01E01.mkv").write_text("existing")
    (tmp_path / "GENERATION_KILL_t00.mkv").touch()

    rename_tv_fast("Show (2000)", tmp_path, 1, {0: (1, 1)}, [3932],
                   run=_ffprobe_run({"GENERATION_KILL_t00.mkv": 3932}))

    assert (tmp_path / "Show (2000)_S01E01.mkv").read_text() == "existing"
    assert (tmp_path / "GENERATION_KILL_t00.mkv").exists()
    assert "already exists" in capsys.readouterr().err


def test_episode_target_singles_doubles_and_seasons():
    """The shared target helper names singles, doubles and specials."""
    stage = Path("/stage")
    assert episode_target(stage, "Show (2000)", 1, 3, 3) == stage / "Show (2000)_S01E03.mkv"
    assert episode_target(stage, "Show (2000)", 1, 1, 2) == stage / "Show (2000)_S01E01-E02.mkv"
    assert episode_target(stage, "Show (2000)", 0, 5, 5) == stage / "Show (2000)_S00E05.mkv"


def test_fast_scan_rip_rips_all_at_floor_and_joins(tmp_path, monkeypatch):
    """Fast-path rip_tv: rip_titles(None, ..., tv_floor) and rename_tv_fast
    with the scanner durations — never the full-scan rename_tv."""
    from skive.tmdb import TMDBPick

    @dataclass
    class FakeArgs:
        titles = None
        extras = False
        tmdbid = None
        verified = True

    episodes = [(1, "ep1", 3932), (2, "ep2", 3897), (3, "ep3", 3653), (4, "ep4", 3600)]
    prompt = MagicMock()
    prompt.ask.side_effect = ["", "", ""]  # season, accept range pre-fill, Enter=rip

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", return_value=GK_DURATIONS), \
            patch("skive.rip_common.scan_disc") as mock_scan_disc, \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            patch("skive.rip_tv.Prompt", prompt), \
            patch("skive.rip_tv.fetch_season", return_value=episodes), \
            patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent-test.xml")), \
            patch("skive.rip_tv.rip_titles", return_value=0) as mock_rip_titles, \
            patch("skive.rip_tv.rename_tv") as mock_rename_tv, \
            patch("skive.rip_tv.rename_tv_fast") as mock_rename_fast, \
            patch("skive.rip_tv.subtitle_note", return_value="subs=dan"):
        with pytest.raises(SystemExit) as e:
            rip_tv(FakeArgs())

    assert e.value.code == 0
    mock_scan_disc.assert_not_called()  # the fast scan gated the disc

    rip_args = mock_rip_titles.call_args
    assert rip_args.args[0] is None  # rip all in one call
    assert rip_args.args[2] == tv_floor(120) == 600  # MakeMKV minlength = TV floor
    assert rip_args.kwargs["total"] == 4

    mock_rename_tv.assert_not_called()
    fast_args = mock_rename_fast.call_args.args
    assert fast_args[3] == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (0, 0)}  # play-all extra
    assert fast_args[4] == GK_DURATIONS


def test_fast_scan_rename_abort_logs_and_exits_before_subtitle_note(tmp_path):
    """rename_tv_fast returning False: the caller logs the abort, closes the
    log with rc 1, and exits 1 — before the subtitle note. Promotion has
    already moved this disc's rip up, so the joined junk rests in the show
    folder for the next run's purge; nothing is renamed."""
    from skive.tmdb import TMDBPick

    @dataclass
    class FakeArgs:
        titles = None
        extras = False
        tmdbid = None
        verified = True

    episodes = [(1, "ep1", 3932), (2, "ep2", 3897), (3, "ep3", 3653), (4, "ep4", 3600)]
    prompt = MagicMock()
    prompt.ask.side_effect = ["", "", ""]  # season, accept range pre-fill, Enter=rip
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True, exist_ok=True)
    (show_dir / "B1_t00.mkv").touch()
    (show_dir / "B1_t01.mkv").touch()

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", return_value=GK_DURATIONS), \
            patch("skive.rip_common.scan_disc") as mock_scan_disc, \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            patch("skive.rip_common.RipLog") as mock_log, \
            patch("skive.rip_tv.Prompt", prompt), \
            patch("skive.rip_tv.fetch_season", return_value=episodes), \
            patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent-test.xml")), \
            patch("skive.rip_tv.rip_titles", return_value=0), \
            patch("skive.rip_tv.promote_staged") as mock_promote, \
            patch("skive.rip_tv.rename_tv_fast", return_value=False) as mock_rename_fast, \
            patch("skive.rip_tv.subtitle_note", return_value="subs=dan") as mock_subs:
        with pytest.raises(SystemExit) as e:
            rip_tv(FakeArgs())

    assert e.value.code == 1
    mock_scan_disc.assert_not_called()  # the fast scan gated the disc
    mock_promote.assert_called_once()  # promotion precedes the rename gate
    mock_rename_fast.assert_called_once()
    log = mock_log.return_value
    log.note.assert_any_call("rename", "join aborted — an episode's staged file is missing")
    log.phase_end.assert_called_once()  # the abort reaches rip.log ...
    log.finish.assert_called_once_with(1)  # ... with a final total line
    mock_subs.assert_not_called()
    assert sorted(p.name for p in show_dir.glob("*.mkv")) == ["B1_t00.mkv", "B1_t01.mkv"]


# -- Interrupt (Ctrl+C) at the prompts (CONTEXT.md: Interrupt) -----------------

def test_season_prompt_interrupt_purges_logs_and_exits_130(tmp_path, monkeypatch, capsys):
    """Ctrl+C at the season prompt: the run log records status=interrupted, the
    exit is 130 with ONE summary line, and no traceback escapes."""
    import json
    from skive.tmdb import TMDBPick

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True)
    (show_dir / "Show (2000)_S01E01.mkv").touch()  # completed rename — kept

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True

    prompt = MagicMock()
    prompt.ask.side_effect = KeyboardInterrupt

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", _gk_titles())), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            patch("skive.rip_tv.Prompt", prompt):
        with pytest.raises(SystemExit) as e:
            rip_tv(FakeArgs())

    assert e.value.code == 130
    assert (show_dir / "Show (2000)_S01E01.mkv").exists()
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert lines[-1]["phase"] == "total" and lines[-1]["status"] == "interrupted"
    err = capsys.readouterr().err
    assert "skive: interrupted — partial deleted, kept 1 titles" in err
    assert err.count("skive: interrupted") == 1


def test_confirm_table_interrupt_drops_inflight_partial(tmp_path, monkeypatch):
    """Ctrl+C at the confirm table (after a previous disc's rips): the exit is
    130 and the junk the table's run would have re-ripped is left clean."""
    import json
    from skive.tmdb import TMDBPick

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    (tmp_path / "rip" / "tv").mkdir(parents=True)

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True

    prompt = MagicMock()
    # season Enter, range Enter, then Ctrl+C at the confirm table
    prompt.ask.side_effect = ["", "", KeyboardInterrupt]

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", _gk_titles())), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            patch("skive.rip_tv.Prompt", prompt), \
            patch("skive.rip_tv.fetch_season", return_value=[(e, f"ep{e}", 3600) for e in range(1, 5)]):
        with pytest.raises(SystemExit) as e:
            rip_tv(FakeArgs())

    assert e.value.code == 130
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert lines[-1]["status"] == "interrupted"
