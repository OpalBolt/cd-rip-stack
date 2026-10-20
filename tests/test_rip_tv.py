"""Tests for TV rip logic: range assignment, fix rows, naming, staged exclusion."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from dataclasses import dataclass
import pytest

from skive.rip_tv import (
    staged_eps, parse_range, next_unstaged, assign_range, range_mismatch_note,
    range_past_end_note, parse_fix_rows, apply_fixes, map_str, print_table,
    rename_tv, rip_tv, tv_floor, sum_disqualified,
)
from skive.makemkv import scan_disc, TitleInfo


# Episode identity: stated range + disc position (ADR 0001)
def test_parse_range():
    """Range answers parse to (lo, hi); junk and inverted ranges are None."""
    assert parse_range("5") == (5, 5)
    assert parse_range("4-6") == (4, 6)
    assert parse_range(" 4 - 6 ") == (4, 6)
    assert parse_range("0") is None
    assert parse_range("6-4") is None
    assert parse_range("x") is None
    assert parse_range("") is None


def test_next_unstaged():
    """Pre-fill starts at the smallest named episode not already staged."""
    names = {1: "ep1", 2: "ep2", 3: "ep3"}
    assert next_unstaged(names, {1}) == 2
    assert next_unstaged(names, {1, 2, 3}) == 4  # all named staged: max + 1
    assert next_unstaged({}, set()) == 1


def test_assign_range_generation_kill():
    """Disc order fills the stated range; the play-all stays extra."""
    titles = [(0, 3932, 1), (1, 3897, 1), (2, 3653, 1), (3, 11482, 1)]
    assert assign_range(titles, {3}, 1, 3) == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (0, 0)}


def test_assign_range_more_titles_than_range():
    """Eligible titles past the range size are extras."""
    titles = [(0, 1000, 1), (1, 1000, 1), (2, 1000, 1), (3, 1000, 1)]
    assert assign_range(titles, set(), 1, 3) == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (0, 0)}


def test_assign_range_fewer_titles_than_range():
    """Fewer eligible titles than the range: only part of it gets covered."""
    titles = [(0, 1000, 1), (1, 1000, 1)]
    assert assign_range(titles, set(), 1, 4) == {0: (1, 1), 1: (2, 2)}


def test_range_mismatch_note():
    """Matching counts -> None; mismatch -> a one-line note naming the escapes."""
    titles = [(0, 1000, 1), (1, 1000, 1), (2, 1000, 1)]
    assert range_mismatch_note(titles, set(), 1, 3) is None

    note = range_mismatch_note(titles, set(), 1, 2)
    assert note is not None and "\n" not in note
    # The suggested fix must always apply: 2=3 is refused past the season's
    # last episode, 2=e is not.
    assert "2=e" in note and "n to re-ask" in note

    assert range_mismatch_note(titles[:1], set(), 1, 4) is not None
    assert range_mismatch_note([], set(), 1, 4) is None  # no eligible titles


def test_range_past_end_note():
    """A range past the season's last episode warns; in-range and unknown-size
    (max_ep=0) do not."""
    assert range_past_end_note(5, 3) is not None
    assert "E5" in range_past_end_note(5, 3) and "3" in range_past_end_note(5, 3)
    assert range_past_end_note(3, 3) is None
    assert range_past_end_note(3, 5) is None
    assert range_past_end_note(5, 0) is None


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


def test_parse_fix_rows():
    """Parse fix row grammar."""
    rows = parse_fix_rows("1=5,2=1-2,3=e")
    assert rows == [(1, "ep", 5, 5), (2, "range", 1, 2), (3, "extra", 0, 0)]

    # Empty
    assert parse_fix_rows("") == []

    # Invalid range
    with pytest.raises(ValueError, match="bad double-episode range"):
        parse_fix_rows("1=2-1")

    # Invalid episode
    with pytest.raises(ValueError, match="bad episode number"):
        parse_fix_rows("1=0")

    # Bad format
    with pytest.raises(ValueError, match="bad episode-map entry"):
        parse_fix_rows("1=x")


def test_fix_rows_repoint():
    """Test fix row re-pointing."""
    assignment = {0: (1, 1), 1: (2, 2)}
    titles = [0, 1]
    staged = set()
    title_to_eps = {1: 0, 2: 1}

    # E3 is free; title 0 re-points to it, releasing E1
    ok = apply_fixes(assignment, titles, staged, "0=3", max_ep=5, title_to_eps=title_to_eps)

    assert ok
    assert assignment[0] == (3, 3)
    assert 1 not in title_to_eps or title_to_eps[1] != 0


def test_fix_rows_clash_rejection():
    """Reject row claiming already-claimed episode."""
    assignment = {0: (1, 1)}
    titles = [0, 1]  # Add title 1 even though it's not assigned yet
    staged = set()
    title_to_eps = {1: 0}

    # Try to assign title 1 to E1 (already claimed by title 0)
    ok = apply_fixes(assignment, titles, staged, "1=1", max_ep=5, title_to_eps=title_to_eps)

    # Should be rejected (E1 already claimed)
    assert not ok


def test_fix_rows_already_staged():
    """Reject row claiming already-staged episode."""
    assignment = {0: (1, 1)}
    titles = [0]
    staged = {1}  # E1 already staged
    title_to_eps = {}

    ok = apply_fixes(assignment, titles, staged, "0=1", max_ep=5, title_to_eps=title_to_eps)

    # Should be rejected
    assert not ok


def _gk_titles():
    """Generation Kill disc 1 shape: three episodes and a play-all (t3)."""
    return [TitleInfo(0, 3932, 1, "dan", "", False),
            TitleInfo(1, 3897, 1, "dan", "", False),
            TitleInfo(2, 3653, 1, "dan", "", False),
            TitleInfo(3, 11482, 1, "dan", "", False)]


def _range_flow(tmp_path, monkeypatch, mock_titles, mock_episodes, answers, staged_names=()):
    """Drive rip_tv through season/range/confirm prompts over a temp stage.

    staged_names are created in the staging dir before the run (already-ripped
    episodes shape the pre-fill). Returns (rename_tv mock, console mock, rc).
    """
    from skive.tmdb import TMDBPick

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    stage = tmp_path / "rip" / "tv"
    stage.mkdir(parents=True, exist_ok=True)
    for name in staged_names:
        (stage / name).touch()

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True

    prompt = MagicMock()
    prompt.ask.side_effect = answers

    with patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
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


def test_map_str():
    """Serialize assignment."""
    assignment = {0: (1, 1), 1: (2, 3), 2: (0, 0)}
    assert map_str(assignment) == "0=1,1=2-3,2=e"


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


def test_scan_disc():
    """One info run returns the media token + parsed titles."""
    output = """
DRV:0,2,0,0,"DVD-ROM hp CDDVDW TS-L633N"
TINFO:0,9,"0:21:00"
TINFO:0,11,"1234567890"
TINFO:1,9,"0:22:30"
TINFO:1,11,"2345678901"
TINFO:2,9,"0:15:00"
TINFO:2,11,"3456789012"
"""

    mock_run = Mock(return_value=Mock(stdout=output))

    media, _label, titles = scan_disc(120, run=mock_run)

    assert media == "DVD-ROM"
    # titles sorted by number, with seconds and size
    assert len(titles) == 3
    assert titles[0].number == 0
    assert titles[0].seconds == 21 * 60  # 21:00
    assert titles[0].size == 1234567890
    assert titles[1].number == 1
    assert titles[1].seconds == 22 * 60 + 30  # 22:30
    assert titles[2].number == 2
    assert titles[2].seconds == 15 * 60


def test_scan_disc_makemkv_info_override():
    """Honor MAKEMKV_INFO env var."""
    output = """
DRV:0,2,0,0,"DVD-ROM"
TINFO:0,9,"0:21:00"
TINFO:0,11,"1234567890"
"""
    mock_run = Mock(return_value=Mock(stdout=output))

    with patch.dict(os.environ, {"MAKEMKV_INFO": "echo test"}):
        media, _label, titles = scan_disc(120, run=mock_run)

    assert media == "DVD-ROM"
    assert len(titles) == 1


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

    with patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", [])):
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

    with patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
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
    stage = tmp_path / "rip" / "tv"

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

    poisoned = stage / "Show (2000)_S01E01-E02.mkv"
    stage.mkdir(parents=True, exist_ok=True)
    poisoned.touch()

    prompt = MagicMock()
    prompt.ask.side_effect = answers

    with patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
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


def test_tv_floor():
    """Floor never drops below 600s, never raises above minlength."""
    assert tv_floor(120) == 600
    assert tv_floor(3000) == 3000


def test_sum_disqualified_generation_kill():
    """11482 = 3932+3897+3653 exactly: title 3 is the play-all."""
    titles = [(0, 3932, 1), (1, 3897, 1), (2, 3653, 1), (3, 11482, 1)]
    assert sum_disqualified(titles) == {3}


def test_sum_disqualified_clean_list():
    """No subset sums -> nothing disqualified."""
    titles = [(0, 2500, 1), (1, 2450, 1), (2, 2400, 1)]
    assert sum_disqualified(titles) == set()


def test_sum_disqualified_tolerance():
    """±120s tolerance: sum +100s still disqualifies, +200s does not."""
    plus_100 = [(0, 1000, 1), (1, 1500, 1), (2, 2600, 1)]
    assert sum_disqualified(plus_100) == {2}

    plus_200 = [(0, 1000, 1), (1, 1500, 1), (2, 2700, 1)]
    assert sum_disqualified(plus_200) == set()


def test_sum_disqualified_overlapping_playalls():
    """Several play-alls on one disc all get disqualified."""
    titles = [(0, 1000, 1), (1, 1000, 1), (2, 2000, 1), (3, 3000, 1), (4, 4000, 1)]
    assert sum_disqualified(titles) == {2, 3, 4}


def test_sum_disqualified_subset_size_cap():
    """Sum of 7 components does not disqualify (subset must be 2..6)."""
    titles = [(i, 600, 1) for i in range(7)] + [(7, 4200, 1)]
    assert sum_disqualified(titles) == set()


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

    with patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
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

    with patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
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

    with patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
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

    with patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
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
