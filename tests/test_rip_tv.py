"""Tests for the TV rip flow: prompts, confirm table, renames, staging."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from dataclasses import dataclass
import pytest

from skive.rip_tv import (
    staged_eps, print_table, rename_tv, rip_tv, rename_tv_fast, episode_target,
    post_rip_validation,
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


def _range_flow(tmp_path, monkeypatch, mock_titles, mock_episodes, answers,
                staged_names=(), titles_arg="all"):
    """Drive rip_tv through season/range/confirm prompts over a temp stage.

    staged_names are created in the show's title folder before the run
    (already-ripped episodes shape the pre-fill). titles_arg is the -T flag
    value ("all" rips everything). Returns (rename_tv mock, console mock, rc).
    """
    from skive.tmdb import TMDBPick

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True, exist_ok=True)
    for name in staged_names:
        (show_dir / name).touch()

    @dataclass
    class FakeArgs:
        titles = titles_arg
        extras = False
        tmdbid = None
        verified = True

    prompt = MagicMock()
    prompt.ask.side_effect = answers

    # The post-rip validation record probes staged files with ffprobe; flow
    # tests assert prompts and tables, so its probe subprocess stays a fake
    # here (the record itself is tested directly, below).
    real_post = post_rip_validation
    monkeypatch.setattr("skive.rip_tv.post_rip_validation",
                        lambda pre, *a, **k: real_post(pre, *a, run=Mock(return_value=Mock(stdout="600.0\n"))))

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
                                                    # The split phase's probe/cut subprocesses stay
                                                    # fake here too: flow tests assert prompts and
                                                    # tables, and an armed caption run must never
                                                    # shell out (the phase itself is tested
                                                    # directly in test_chapter_split.py).
                                                    with patch("skive.rip_tv.split_combined"):
                                                        try:
                                                            rip_tv(FakeArgs())
                                                        except SystemExit as e:
                                                            return mock_rename_tv, mock_console, e.code
    return mock_rename_tv, mock_console, None


def _table_texts(console) -> list[str]:
    """Render each Table the mocked console printed, to plain text."""
    texts = []
    for c in console.print.call_args_list:
        if c.args and isinstance(c.args[0], Table):
            plain = Console(record=True, width=120)
            plain.print(c.args[0])
            texts.append(plain.export_text())
    return texts


def _captions(console) -> list[str]:
    """Every printed line carrying the play-all-only split caption."""
    return [str(c.args[0]) for c in console.print.call_args_list
            if c.args and "play-all-only disc" in str(c.args[0])]


def test_range_prefill_skips_staged_and_fills_disc(tmp_path, monkeypatch):
    """The map's regression fixture: E01-E03 staged, this disc carries E04-E06.

    Enter accepts the pre-fill 4-6 — the episode guess (E4→t0, E5→t1, E6→t2,
    each ratio 1.00); the three eligible titles get E4-E6 in disc order and
    the play-all (t3) stays extra.
    """
    episodes = [(e, f"ep{e}", rt) for e, rt in
                ((1, 3932), (2, 3897), (3, 3653), (4, 3932), (5, 3897), (6, 3653))]
    staged = [f"Show (2000)_S01E0{e}.mkv" for e in (1, 2, 3)]

    mock_rename_tv, _console, rc = _range_flow(
        tmp_path, monkeypatch, _gk_titles(), episodes, ["", "", ""], staged)

    assert rc == 0
    assert mock_rename_tv.call_args.args[3] == {0: (4, 4), 1: (5, 5), 2: (6, 6), 3: (0, 0)}


def test_overview_before_range_prompt_guess_prefill(tmp_path, monkeypatch):
    """The overview precedes the range prompt: guessed assignments wear
    (est.), the play-all reads "play-all, not ripped", the eligible no-fit
    title reads "extra"; the pre-fill comes from the guess (1-3, not the
    eligible count's 1-4), and Enter accepts it — the rip's assignment equals
    the overview's positional fill."""
    titles = [TitleInfo(0, 3940, 1, "dan", "", False),
              TitleInfo(1, 2855, 1, "dan", "", False),
              TitleInfo(2, 2852, 1, "dan", "", False),
              TitleInfo(3, 9643, 1, "dan", "", False),   # play-all: 3940+2855+2852 = 9647
              TitleInfo(4, 4000, 1, "dan", "", False)]   # 4000/4020 = 0.995, inside the green band — still extra: the walk spent t0 on E1 and only 3 episodes exist
    episodes = [(1, "ep1", 4020), (2, "ep2", 2820), (3, "ep3", 2820)]

    mock_rename_tv, console, rc = _range_flow(tmp_path, monkeypatch, titles, episodes,
                                              ["", "", ""])  # season, accept prefill, Enter=rip

    assert rc == 0
    texts = _table_texts(console)
    overview = texts[0]
    assert overview.count("(est.)") == 3
    assert "play-all, not ripped" in overview
    extra_row = next(ln for ln in overview.splitlines() if "1:06:40" in ln)
    assert "→ extra" in extra_row
    labels = [str(c.args[0]) for c in console.print.call_args_list
              if c.args and "episodes on this disc" in str(c.args[0])]
    assert labels == ["episodes on this disc [1-3]: "]
    assert mock_rename_tv.call_args.args[3] == {0: (1, 1), 1: (2, 2), 2: (3, 3),
                                                3: (0, 0), 4: (0, 0)}


def test_n_reask_reshows_overview_confirm_table_marks_playall(tmp_path, monkeypatch):
    """`n` re-shows the overview; the confirm table keeps its columns and
    renders the play-all "play-all, not ripped" without (est.) marks."""
    episodes = [(1, "ep1", 3932), (2, "ep2", 3897), (3, "ep3", 3653)]

    mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, _gk_titles(), episodes,
        ["", "", "n", "", ""])  # season, range, n at the table, range, Enter=rip

    assert rc == 0
    texts = _table_texts(console)
    assert len(texts) == 4  # overview, confirm, overview (the n re-ask), confirm
    assert texts[0].count("(est.)") == 3
    assert "play-all, not ripped" in texts[0]
    confirm = texts[1]
    assert "play-all, not ripped" in confirm
    assert "(est.)" not in confirm
    assert "audit" in confirm and "assignment" in confirm
    assert mock_rename_tv.call_args.args[3] == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (0, 0)}


def test_no_runtimes_prefill_falls_back_to_count(tmp_path, monkeypatch):
    """No TMDB runtimes (season-0 shape): the prefill stays today's
    count-based default — one episode per eligible title from the first
    unstaged episode — and the overview still prints the positional fill."""
    episodes = [(1, "special 1", 0), (2, "special 2", 0)]

    mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, _gk_titles(), episodes,
        ["0", "", ""])  # season 0, accept prefill, Enter=rip

    assert rc == 0
    labels = [str(c.args[0]) for c in console.print.call_args_list
              if c.args and "episodes on this disc" in str(c.args[0])]
    assert labels == ["episodes on this disc [1-3]: "]
    assert mock_rename_tv.call_args.args[3] == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (0, 0)}


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
                                        with patch("skive.rip_tv.split_combined") as mock_split:
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
    # The extras path has no episode range — it never reaches the split.
    mock_split.assert_not_called()


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
                                                    mock_log.run = "run-1"  # the validation document's run identity
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
                                                with patch("skive.rip_common.RipLog") as MockLog:
                                                    MockLog.return_value.run = "run-1"  # the validation document's run identity
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
                                                with patch("skive.rip_common.RipLog") as MockLog:
                                                    MockLog.return_value.run = "run-1"  # the validation document's run identity
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
                                                with patch("skive.rip_common.RipLog") as MockLog:
                                                    MockLog.return_value.run = "run-1"  # the validation document's run identity
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
                                                with patch("skive.rip_common.RipLog") as MockLog:
                                                    MockLog.return_value.run = "run-1"  # the validation document's run identity
                                                    with patch("skive.rip_tv.Prompt") as mock_prompt:
                                                        # season, then Enter for the range, 3=5 at the table, Enter=rip
                                                        mock_prompt.ask.side_effect = ["", "", "3=5", ""]
                                                        try:
                                                            rip_tv(FakeArgs())
                                                        except SystemExit:
                                                            pass

    assert mock_rename_tv.call_args.args[3] == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (5, 5)}


# The play-all-only caption (ADR 0009): recomputed from the final map every
# confirm-loop iteration, under the table, before the drive spins.
def test_playall_only_caption_arms_on_fix_row(tmp_path, monkeypatch):
    """A disc whose only title carries the whole season: the fix row `0=1-3`
    declares the range and the caption appears under the confirm table,
    naming the title and the episode range it carries."""
    titles = [TitleInfo(0, 9643, 1, "dan", "", False)]
    episodes = [(1, "ep1", 4020), (2, "ep2", 2820), (3, "ep3", 2820)]

    mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, titles, episodes,
        ["", "1", "0=1-3", ""])  # season, range 1, fix row arms it, Enter=rip

    assert rc == 0
    assert mock_rename_tv.call_args.args[3] == {0: (1, 3)}
    captions = _captions(console)
    assert len(captions) == 1
    assert "title 0" in captions[0]
    assert "S01E01-E03" in captions[0]
    assert "split after the rip" in captions[0]
    # Under the table, before the ok? prompt.
    prints = [c for c in console.print.call_args_list if c.args]
    cap_idx = next(i for i, c in enumerate(prints)
                   if "play-all-only disc" in str(c.args[0]))
    tbl_idx = max(i for i, c in enumerate(prints) if isinstance(c.args[0], Table))
    ok_idx = max(i for i, c in enumerate(prints)
                 if str(c.args[0]).startswith("ok?"))
    assert tbl_idx < cap_idx < ok_idx


def test_playall_only_caption_disarms_on_n_reask(tmp_path, monkeypatch):
    """The caption is recomputed every loop iteration: armed by the fix row,
    disarmed again within one session by an `n` re-ask restating a single
    episode for the lone title — the caption prints exactly once."""
    titles = [TitleInfo(0, 9643, 1, "dan", "", False)]
    episodes = [(1, "ep1", 4020), (2, "ep2", 2820), (3, "ep3", 2820)]

    mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, titles, episodes,
        ["", "1", "0=1-3", "n", "1", ""])  # fix row arms, n re-ask disarms, Enter=rip

    assert rc == 0
    assert mock_rename_tv.call_args.args[3] == {0: (1, 1)}
    assert len(_captions(console)) == 1


def test_playall_only_caption_hides_on_multi_title_disc(tmp_path, monkeypatch):
    """The ordinary multi-title disc — double-free, play-all skipped — never
    captions, whatever the table shows."""
    episodes = [(e, f"ep{e}", rt) for e, rt in
                ((1, 3932), (2, 3897), (3, 3653))]

    _mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, _gk_titles(), episodes, ["", "", ""])

    assert rc == 0
    assert _captions(console) == []


def test_playall_only_caption_on_title_selection(tmp_path, monkeypatch):
    """A -T title selection that reduces the table to one eligible title
    qualifies with no special case: the final map reads one ranged title and
    the caption names it."""
    titles = [TitleInfo(0, 3000, 1, "dan", "", False),
              TitleInfo(1, 9643, 1, "dan", "", False)]
    episodes = [(1, "ep1", 4020), (2, "ep2", 2820), (3, "ep3", 2820)]

    mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, titles, episodes,
        ["", "1", "1=1-3", ""], titles_arg="1")  # -T 1, range 1, fix row, Enter=rip


    assert rc == 0
    assert mock_rename_tv.call_args.args[3] == {1: (1, 3)}
    captions = _captions(console)
    assert len(captions) == 1
    assert "title 1" in captions[0] and "S01E01-E03" in captions[0]


# --- The chapter split hook (ADR 0009): fires after the rename, before the
# subtitle note and validation, on both scan paths; its phase internals are
# tested in test_chapter_split.py — these pin the wiring and the containment.


def _split_fake_cut(show_dir, order, expect_combined=True):
    """A fake split_combined: record firing order, require the rename's
    combined file, cut the three pieces, delete the combined file."""
    def fake_split(combined, targets, season, lo, hi, _runtimes, _log, **k):
        order.append("split")
        assert combined.exists() is expect_combined   # the rename ran first
        for t in targets.values():
            t.touch()
        combined.unlink()
    return fake_split


def test_chapter_split_hook_arms_on_playall_only_shape(tmp_path):
    """The lone eligible title carrying a range: the hook names the combined
    file and the per-episode targets and hands them to the split phase."""
    from types import SimpleNamespace
    from skive.rip_tv import chapter_split_hook

    pre = SimpleNamespace(title_dir=tmp_path, display="Show (2000)", log=Mock())
    runtimes = {1: 3240, 2: 3240, 3: 3240}

    with patch("skive.rip_tv.split_combined") as mock_split:
        chapter_split_hook(pre, 1, [(0, 9643, 1)], set(), {0: (1, 3)}, runtimes)

    assert mock_split.call_count == 1
    args = mock_split.call_args.args
    assert args[0] == tmp_path / "Show (2000)_S01E01-E03.mkv"
    assert args[1] == {1: tmp_path / "Show (2000)_S01E01.mkv",
                       2: tmp_path / "Show (2000)_S01E02.mkv",
                       3: tmp_path / "Show (2000)_S01E03.mkv"}
    assert args[2:] == (1, 1, 3, runtimes, pre.log)


def test_chapter_split_hook_ignores_single_episode_map(tmp_path):
    """A lone title assigned one episode is no play-all-only disc."""
    from types import SimpleNamespace
    from skive.rip_tv import chapter_split_hook

    pre = SimpleNamespace(title_dir=tmp_path, display="Show (2000)", log=Mock())
    with patch("skive.rip_tv.split_combined") as mock_split:
        chapter_split_hook(pre, 1, [(0, 9643, 1)], set(), {0: (1, 1)}, {1: 4020})
    mock_split.assert_not_called()


def test_chapter_split_hook_ignores_multi_title_disc(tmp_path):
    """Two eligible titles: doubles territory, never the split (ADR 0009)."""
    from types import SimpleNamespace
    from skive.rip_tv import chapter_split_hook

    pre = SimpleNamespace(title_dir=tmp_path, display="Show (2000)", log=Mock())
    titles = [(0, 3932, 1), (1, 3897, 1)]
    with patch("skive.rip_tv.split_combined") as mock_split:
        chapter_split_hook(pre, 1, titles, set(), {0: (1, 1), 1: (2, 2)},
                           {1: 3932, 2: 3897})
    mock_split.assert_not_called()


def test_split_hook_cuts_after_rename_before_subtitles_full_scan(
        tmp_path, monkeypatch):
    """Full-scan rip of a play-all-only disc: the split runs between the
    rename and the subtitle note, and post-rip validation grades the three
    per-episode pieces under the existing rules — no validation changes."""
    import json
    from skive.tmdb import TMDBPick

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True)
    episodes = [(1, "ep1", 4020), (2, "ep2", 2820), (3, "ep3", 2820)]
    combined = show_dir / "Show (2000)_S01E01-E03.mkv"
    order = []

    def fake_rename(_display, _dir, _season, _assignment):
        combined.touch()

    def fake_subs(_dir):
        order.append("subs")
        assert (show_dir / "Show (2000)_S01E01.mkv").exists()
        assert not combined.exists()      # cut and gone before subtitles
        return "subs=dan"

    durs = {"Show (2000)_S01E01.mkv": 3930, "Show (2000)_S01E02.mkv": 2810,
            "Show (2000)_S01E03.mkv": 2810}

    def fake_run(cmd, **k):
        return Mock(stdout=f"{durs[Path(cmd[-1]).name]}\n")

    real_post = post_rip_validation
    prompt = MagicMock()
    prompt.ask.side_effect = ["", "1", "0=1-3", ""]  # season, range, fix row, rip

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd",
                  side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc",
                  return_value=("DVD-ROM", "", [TitleInfo(0, 9643, 1, "dan", "", False)])), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            patch("skive.rip_tv.Prompt", prompt), \
            patch("skive.rip_tv.fetch_season", return_value=episodes), \
            patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent-test.xml")), \
            patch("skive.rip_tv.rip_titles", return_value=0), \
            patch("skive.rip_tv.rename_tv", side_effect=fake_rename), \
            patch("skive.rip_tv.split_combined",
                  side_effect=_split_fake_cut(show_dir, order)), \
            patch("skive.rip_tv.subtitle_note", side_effect=fake_subs), \
            patch("skive.rip_tv.post_rip_validation",
                  lambda pre, *a, **k: real_post(pre, *a, run=fake_run)):
        with pytest.raises(SystemExit) as e:
            rip_tv(FakeArgs())

    assert e.value.code == 0
    assert order == ["split", "subs"]
    doc = json.loads((show_dir / "validation.json").read_text(encoding="utf-8"))
    assert [r["file"] for r in doc["seasons"][0]["episodes"]] == [
        "Show (2000)_S01E01.mkv", "Show (2000)_S01E02.mkv",
        "Show (2000)_S01E03.mkv"]
    assert all(r["band"] == "green"
               for r in doc["seasons"][0]["episodes"])


def test_split_decline_keeps_combined_and_validation_grades_it(
        tmp_path, monkeypatch):
    """A declined split is the pre-feature shape: the combined file stays,
    and validation records it as one row under its first episode graded
    against the summed runtimes (the existing double rule)."""
    import json
    from skive.tmdb import TMDBPick

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True)
    episodes = [(1, "ep1", 4020), (2, "ep2", 2820), (3, "ep3", 2820)]
    combined = show_dir / "Show (2000)_S01E01-E03.mkv"

    def fake_rename(_display, _dir, _season, _assignment):
        combined.touch()

    durs = {"Show (2000)_S01E01-E03.mkv": 9400}

    def fake_run(cmd, **k):
        return Mock(stdout=f"{durs[Path(cmd[-1]).name]}\n")

    real_post = post_rip_validation
    prompt = MagicMock()
    prompt.ask.side_effect = ["", "1", "0=1-3", ""]

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd",
                  side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc",
                  return_value=("DVD-ROM", "", [TitleInfo(0, 9643, 1, "dan", "", False)])), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            patch("skive.rip_tv.Prompt", prompt), \
            patch("skive.rip_tv.fetch_season", return_value=episodes), \
            patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent-test.xml")), \
            patch("skive.rip_tv.rip_titles", return_value=0), \
            patch("skive.rip_tv.rename_tv", side_effect=fake_rename), \
            patch("skive.rip_tv.split_combined"), \
            patch("skive.rip_tv.subtitle_note", return_value="subs=dan"), \
            patch("skive.rip_tv.post_rip_validation",
                  lambda pre, *a, **k: real_post(pre, *a, run=fake_run)):
        with pytest.raises(SystemExit) as e:
            rip_tv(FakeArgs())

    assert e.value.code == 0
    assert combined.exists()                 # the decline kept it
    doc = json.loads((show_dir / "validation.json").read_text(encoding="utf-8"))
    rows = doc["seasons"][0]["episodes"]
    assert rows == [{"ep": 1, "file": "Show (2000)_S01E01-E03.mkv", "t": 0,
                     "dur_s": 9400, "runtime_s": 9660, "ratio": 0.97,
                     "band": "green"}]


def test_split_hook_fires_on_fast_scan_path(tmp_path, monkeypatch):
    """Fast scan of a play-all-only disc: the real duration join renames the
    lone staged file to the combined name, and the split fires there too —
    after the rename, before the subtitle note."""
    import json
    from skive.tmdb import TMDBPick
    from skive.rip_tv import rename_tv_fast as real_join

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True)
    episodes = [(1, "ep1", 4020), (2, "ep2", 2820), (3, "ep3", 2820)]
    combined = show_dir / "Show (2000)_S01E01-E03.mkv"
    order = []

    def promote_one_junk(stage):
        (show_dir / "B1_t00.mkv").touch()   # what makemkvcon would have staged

    def joined_join(display, dir_, season, assignment, durs, **k):
        return real_join(display, dir_, season, assignment, durs,
                         run=Mock(return_value=Mock(stdout="9643\n")))

    def fake_subs(_dir):
        order.append("subs")
        assert (show_dir / "Show (2000)_S01E01.mkv").exists()
        return "subs=dan"

    durs = {"Show (2000)_S01E01.mkv": 3930, "Show (2000)_S01E02.mkv": 2810,
            "Show (2000)_S01E03.mkv": 2810}

    def fake_run(cmd, **k):
        return Mock(stdout=f"{durs[Path(cmd[-1]).name]}\n")

    real_post = post_rip_validation
    prompt = MagicMock()
    prompt.ask.side_effect = ["", "", "0=1-3", ""]  # season, range prefill, fix row, rip

    @dataclass
    class FakeArgs:
        titles = None          # no -T flag: the fast scan gates the disc
        extras = False
        tmdbid = None
        verified = True

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", return_value=[9643]), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            patch("skive.rip_tv.Prompt", prompt), \
            patch("skive.rip_tv.fetch_season", return_value=episodes), \
            patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent-test.xml")), \
            patch("skive.rip_tv.rip_titles", return_value=0), \
            patch("skive.rip_tv.promote_staged", side_effect=promote_one_junk), \
            patch("skive.rip_tv.rename_tv_fast", side_effect=joined_join), \
            patch("skive.rip_tv.split_combined",
                  side_effect=_split_fake_cut(show_dir, order)), \
            patch("skive.rip_tv.subtitle_note", side_effect=fake_subs), \
            patch("skive.rip_tv.post_rip_validation",
                  lambda pre, *a, **k: real_post(pre, *a, run=fake_run)):
        with pytest.raises(SystemExit) as e:
            rip_tv(FakeArgs())

    assert e.value.code == 0
    assert order == ["split", "subs"]
    doc = json.loads((show_dir / "validation.json").read_text(encoding="utf-8"))
    rows = doc["seasons"][0]["episodes"]
    assert [r["file"] for r in rows] == ["Show (2000)_S01E01.mkv",
                                         "Show (2000)_S01E02.mkv",
                                         "Show (2000)_S01E03.mkv"]
    assert all(r["t"] is None for r in rows)   # fast-scan rows, existing rule


def test_split_interrupt_stays_contained_run_continues(tmp_path, monkeypatch,
                                                       capsys):
    """Ctrl+C at the boundary table: the split sweeps and notes `interrupted`,
    then the run continues through the subtitle note and validation with its
    exit code — no status=interrupted line, no 130, no junk purge."""
    import json
    from skive.tmdb import TMDBPick

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True)
    episodes = [(1, "ep1", 4020), (2, "ep2", 2820), (3, "ep3", 2820)]
    combined = show_dir / "Show (2000)_S01E01-E03.mkv"

    def fake_rename(_display, _dir, _season, _assignment):
        combined.touch()

    # The real split phase runs: probe served inline, Ctrl+C at its prompt.
    starts = [k * 468.0 for k in range(21)]

    def fake_probe(path, run=None):
        return (starts, 9410.0)

    split_prompt = MagicMock()
    split_prompt.ask.side_effect = KeyboardInterrupt

    subs_seen = []

    def fake_subs(_dir):
        subs_seen.append(True)
        return "subs=dan"

    real_post = post_rip_validation
    prompt = MagicMock()
    prompt.ask.side_effect = ["", "1", "0=1-3", ""]

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd",
                  side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc",
                  return_value=("DVD-ROM", "", [TitleInfo(0, 9643, 1, "dan", "", False)])), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            patch("skive.rip_tv.Prompt", prompt), \
            patch("skive.rip_tv.fetch_season", return_value=episodes), \
            patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent-test.xml")), \
            patch("skive.rip_tv.rip_titles", return_value=0), \
            patch("skive.rip_tv.rename_tv", side_effect=fake_rename), \
            patch("skive.chapter_split.probe_chapters", side_effect=fake_probe), \
            patch("skive.chapter_split.Prompt", split_prompt), \
            patch("skive.rip_tv.subtitle_note", side_effect=fake_subs), \
            patch("skive.rip_tv.post_rip_validation",
                  lambda pre, *a, **k: real_post(
                      pre, *a, run=Mock(return_value=Mock(stdout="9400\n")))):
        with pytest.raises(SystemExit) as e:
            rip_tv(FakeArgs())

    assert e.value.code == 0                 # the rip's exit code, unchanged
    assert subs_seen == [True]               # the run continued past the split
    assert combined.exists()                 # the interrupt kept it
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert not any(line["status"] == "interrupted" for line in lines)
    assert "skive: split: interrupted" in capsys.readouterr().err


# --- Fast scan: duration join + rename (ADR 0002, DVD half) ---
GK_DURATIONS = [3932, 3897, 3653, 11482]
# Play-all-free: a fast scan that finds a play-all escalates (ADR 0008), so
# genuine fast-path flow tests carry the three episodes only.
FAST_DURATIONS = GK_DURATIONS[:3]


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

    episodes = [(1, "ep1", 3932), (2, "ep2", 3897), (3, "ep3", 3653)]
    prompt = MagicMock()
    prompt.ask.side_effect = ["", "", ""]  # season, accept range pre-fill, Enter=rip

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", return_value=FAST_DURATIONS), \
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
    assert rip_args.kwargs["total"] == 3

    mock_rename_tv.assert_not_called()
    fast_args = mock_rename_fast.call_args.args
    assert fast_args[3] == {0: (1, 1), 1: (2, 2), 2: (3, 3)}  # no play-all on this disc
    assert fast_args[4] == FAST_DURATIONS


def test_fast_scan_playall_escalates_to_full_scan_and_skips_it(tmp_path, capsys):
    """A play-all on the fast scan escalates (ADR 0008): the full MakeMKV scan
    re-runs, the run joins the full-scan path (per-title minlength, not the TV
    floor; direct rename, no duration join), the escalation is logged, and the
    play-all is skipped in the rip selection."""
    from skive.tmdb import TMDBPick

    @dataclass
    class FakeArgs:
        titles = None
        extras = False
        tmdbid = None
        verified = True

    episodes = [(1, "ep1", 3932), (2, "ep2", 3897), (3, "ep3", 3653)]
    # MakeMKV numbers differ from scanner positions — what the escalation is for.
    mkv_titles = [TitleInfo(10, 3932, 100, "dan", "", False),
                  TitleInfo(11, 3897, 101, "dan", "", False),
                  TitleInfo(12, 3653, 102, "dan", "", False),
                  TitleInfo(13, 11482, 400, "dan", "", False)]
    prompt = MagicMock()
    prompt.ask.side_effect = ["", "", ""]  # season, accept range pre-fill, Enter=rip

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", return_value=GK_DURATIONS), \
            patch("skive.rip_common.scan_disc") as mock_gate_scan, \
            patch("skive.rip_tv.scan_disc", return_value=("DVD-ROM", "", mkv_titles)) as mock_full_scan, \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.RipLog") as mock_log, \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            patch("skive.rip_tv.Prompt", prompt), \
            patch("skive.rip_tv.fetch_season", return_value=episodes), \
            patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent-test.xml")), \
            patch("skive.rip_tv.rip_titles", return_value=0) as mock_rip_titles, \
            patch("skive.rip_tv.rename_tv") as mock_rename_tv, \
            patch("skive.rip_tv.rename_tv_fast") as mock_rename_fast, \
            patch("skive.rip_tv.subtitle_note", return_value="subs=dan"):
        mock_log.return_value.run = "run-1"  # the validation document's run identity
        with pytest.raises(SystemExit) as e:
            rip_tv(FakeArgs())

    assert e.value.code == 0
    mock_gate_scan.assert_not_called()  # the fast scan gated the disc
    mock_full_scan.assert_called_once()  # the play-all escalated it
    mock_log.return_value.note.assert_any_call("scan", "play-all detected — full-scan escalation")
    assert "fast scan found a play-all title — escalating" in capsys.readouterr().err

    rip_args = mock_rip_titles.call_args
    assert rip_args.args[0] == [10, 11, 12]  # the play-all (13) is skipped
    assert rip_args.args[2] == 120  # full-scan rip: MakeMKV minlength, not the TV floor
    assert rip_args.kwargs["total"] == 4

    mock_rename_fast.assert_not_called()  # no duration join after escalation
    mock_rename_tv.assert_called_once()  # direct rename by MakeMKV title numbers
    assert mock_rename_tv.call_args.args[3] == {10: (1, 1), 11: (2, 2), 12: (3, 3), 13: (0, 0)}


def test_full_scan_rip_selection_skips_playall(tmp_path, monkeypatch):
    """Full path (no play-all escalation needed): the rip selection excludes
    the play-all pinned extra, plain extras still rip.

    t2 (3550s) sits within 120s of t0+t1: the selection skips t2 alone. t3
    (300s) is a making-of below the TV floor — no table row, no assignment,
    not a play-all — and still rips: the selection comes from the full scan
    list, not the floored table.
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
                   TitleInfo(2, 3550, 1, "dan", "", False),
                   TitleInfo(3, 300, 1, "dan", "", False)]
    mock_episodes = [(1, "ep1", 1800), (2, "ep2", 1700), (3, "ep3", 3550)]

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Test Show", year="2020", original_language="en", type="tv", id="123")), \
            patch("skive.rip_tv.fetch_season", return_value=mock_episodes), \
            patch("skive.rip_tv.staged_eps", return_value=set()), \
            patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/test.xml")), \
            patch("skive.rip_tv.rip_titles", return_value=0) as mock_rip_titles, \
            patch("skive.rip_tv.rename_tv"), \
            patch("skive.rip_tv.subtitle_note", return_value="subs=dan"), \
            patch("skive.rip_tv.Prompt") as mock_prompt:
        # season, then Enter for the range, Enter=rip
        mock_prompt.ask.side_effect = ["", "", ""]
        try:
            rip_tv(FakeArgs())
        except SystemExit:
            pass

    assert mock_rip_titles.call_args.args[0] == [0, 1, 3]  # play-all t2 skipped; sub-floor making-of t3 rips


def test_fix_row_rescued_playall_rips():
    """A fix row assigning the play-all an episode empties the skip set: the
    selection stays None (rip all), so the rescued play-all rips.

    Same disc as above; `2=3` at the table makes t2 E3.
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
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Test Show", year="2020", original_language="en", type="tv", id="123")), \
            patch("skive.rip_tv.fetch_season", return_value=mock_episodes), \
            patch("skive.rip_tv.staged_eps", return_value=set()), \
            patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/test.xml")), \
            patch("skive.rip_tv.rip_titles", return_value=0) as mock_rip_titles, \
            patch("skive.rip_tv.rename_tv"), \
            patch("skive.rip_tv.subtitle_note", return_value="subs=dan"), \
            patch("skive.rip_tv.console") as mock_console, \
            patch("skive.rip_tv.Prompt") as mock_prompt:
        # season, then Enter for the range, the rescue fix row, Enter=rip
        mock_prompt.ask.side_effect = ["", "", "2=3", ""]
        try:
            rip_tv(FakeArgs())
        except SystemExit:
            pass

    assert mock_rip_titles.call_args.args[0] is None  # skip set empty: rip all

    # The table must not lie about the rescue: before `2=3` t2 reads skipped,
    # after it the re-printed confirm table shows E3 for t2 (texts: overview,
    # confirm before the fix row, confirm after it).
    texts = _table_texts(mock_console)
    assert "play-all, not ripped" in texts[1]
    assert "play-all, not ripped" not in texts[2]
    assert "→ S01E03 (ep3)" in texts[2]


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
            patch("skive.rip_common.fast_scan_dvd", return_value=FAST_DURATIONS), \
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


# -- Post-rip validation record (issue 06, map.md decision 6) ------------------

def _tv_preamble_for(tmp_path, fast_scan=False):
    """A real Preamble (dataclass) for direct post_rip_validation calls:
    Mock log carrying a string run, the Show (2000) pick and folder."""
    from skive.rip_common import Preamble
    from skive.tmdb import TMDBPick

    log = Mock()
    log.run = "newrun1"
    title_dir = tmp_path / "Show (2000)"
    pre = Preamble(log=log, pick=TMDBPick("Show", "2000", "en", "tv", "5"),
                   display="Show (2000)", media="DVD-ROM", titles_full=[],
                   title_dir=title_dir, stage=title_dir / "d0", minlength=120,
                   keep="", disc=0, label="SHOW_S2", fast_scan=fast_scan)
    return pre, log


def test_post_rip_writes_probed_rows_and_identity_block(tmp_path):
    """Full scan: validation.json lands at the show-folder root with the TV
    shape — shared identity block, episode rows carrying the probed actuals
    (not the scan table's promise), t inverted from this run's map, grades
    against the per-episode TMDB runtimes."""
    import json
    pre, log = _tv_preamble_for(tmp_path)
    pre.title_dir.mkdir()
    (pre.title_dir / "Show (2000)_S01E01.mkv").write_bytes(b"x")
    (pre.title_dir / "Show (2000)_S01E02.mkv").write_bytes(b"x")
    durs = {"Show (2000)_S01E01.mkv": 3930, "Show (2000)_S01E02.mkv": 3895}

    def fake_run(cmd, **k):
        return Mock(stdout=f"{durs[Path(cmd[-1]).name]}\n")

    post_rip_validation(pre, 1, {0: (1, 1), 1: (2, 2), 9: (0, 0)},
                        {1: 4140, 2: 4080}, run=fake_run)

    doc = json.loads((pre.title_dir / "validation.json").read_text(encoding="utf-8"))
    assert list(doc) == ["tmdb", "seasons", "encodes"]
    assert doc["tmdb"] == {"id": "5", "type": "tv", "title": "Show (2000)"}
    assert doc["seasons"] == [{
        "season": 1,
        "episodes": [
            {"ep": 1, "file": "Show (2000)_S01E01.mkv", "t": 0, "dur_s": 3930,
             "runtime_s": 4140, "ratio": 0.95, "band": "green"},
            {"ep": 2, "file": "Show (2000)_S01E02.mkv", "t": 1, "dur_s": 3895,
             "runtime_s": 4080, "ratio": 0.95, "band": "green"}],
        "discs": [{"volume_label": "SHOW_S2", "run": "newrun1",
                   "staged": [1, 2]}],  # this run's map; 9=extra never stages
    }]
    assert doc["encodes"] == []
    log.note.assert_any_call("validation", "2 episodes, 0 yellow")


def test_post_rip_double_records_lo_extra_and_junk_skipped(tmp_path):
    """A double (S01E05-E06) records one row under its lo episode, graded
    against the range's summed runtime (a healthy double reads 1.00, never a
    false 2.00); extras (" - Extra NN") and junk-labeled files never match
    the episode shape; the disc entry's staged list expands the double to
    both episodes."""
    import json
    pre, _log = _tv_preamble_for(tmp_path)
    pre.title_dir.mkdir()
    (pre.title_dir / "Show (2000)_S01E05-E06.mkv").write_bytes(b"x")
    (pre.title_dir / "Show (2000) - Extra 01.mkv").write_bytes(b"x")
    (pre.title_dir / "Show (2000)_t00.mkv").write_bytes(b"x")

    post_rip_validation(pre, 1, {0: (5, 6)}, {5: 2580, 6: 2580},
                        run=Mock(return_value=Mock(stdout="5160\n")))

    doc = json.loads((pre.title_dir / "validation.json").read_text(encoding="utf-8"))
    season = doc["seasons"][0]
    assert season["episodes"] == [
        {"ep": 5, "file": "Show (2000)_S01E05-E06.mkv", "t": 0, "dur_s": 5160,
         "runtime_s": 5160, "ratio": 1.0, "band": "green"},
    ]
    assert season["discs"] == [{"volume_label": "SHOW_S2", "run": "newrun1",
                                "staged": [5, 6]}]


def test_post_rip_fast_scan_rows_record_t_none(tmp_path):
    """Fast scan: the table numbers are scanner indices, never real _tNN
    (slice-04 review P2) — rows record t=None even though the map covers the
    episode. Null is honest."""
    import json
    pre, _log = _tv_preamble_for(tmp_path, fast_scan=True)
    pre.title_dir.mkdir()
    (pre.title_dir / "Show (2000)_S01E01.mkv").write_bytes(b"x")

    post_rip_validation(pre, 1, {0: (1, 1)}, {1: 4140},
                        run=Mock(return_value=Mock(stdout="3930\n")))

    doc = json.loads((pre.title_dir / "validation.json").read_text(encoding="utf-8"))
    assert doc["seasons"][0]["episodes"][0]["t"] is None


def test_post_rip_unknown_runtime_and_yellow_band(tmp_path):
    """An episode without a TMDB runtime (the fetch's 0) records runtime_s
    null and never a grade; a real mismatch bands yellow, counted in the
    summary note."""
    import json
    pre, log = _tv_preamble_for(tmp_path)
    pre.title_dir.mkdir()
    (pre.title_dir / "Show (2000)_S01E01.mkv").write_bytes(b"x")
    (pre.title_dir / "Show (2000)_S01E02.mkv").write_bytes(b"x")
    durs = {"Show (2000)_S01E01.mkv": 3930, "Show (2000)_S01E02.mkv": 6000}

    def fake_run(cmd, **k):
        return Mock(stdout=f"{durs[Path(cmd[-1]).name]}\n")

    post_rip_validation(pre, 1, {0: (1, 1), 1: (2, 2)}, {1: 0, 2: 4140}, run=fake_run)

    doc = json.loads((pre.title_dir / "validation.json").read_text(encoding="utf-8"))
    by_ep = {r["ep"]: r for r in doc["seasons"][0]["episodes"]}
    assert by_ep[1]["runtime_s"] is None and by_ep[1]["band"] is None
    assert by_ep[2]["ratio"] == 1.45 and by_ep[2]["band"] == "yellow"
    log.note.assert_any_call("validation", "2 episodes, 1 yellow")


def test_post_rip_unreadable_duration_still_writes(tmp_path, capsys):
    """A file whose duration won't read: null dur row, one warning — the
    document is still written (never crash, never skip the write)."""
    import json
    pre, _log = _tv_preamble_for(tmp_path)
    pre.title_dir.mkdir()
    (pre.title_dir / "Show (2000)_S01E01.mkv").write_bytes(b"x")

    post_rip_validation(pre, 1, {0: (1, 1)}, {1: 4140},
                        run=Mock(return_value=Mock(stdout="")))

    doc = json.loads((pre.title_dir / "validation.json").read_text(encoding="utf-8"))
    assert doc["seasons"][0]["episodes"] == [
        {"ep": 1, "file": "Show (2000)_S01E01.mkv",
         "t": 0, "dur_s": None, "runtime_s": 4140,
         "ratio": None, "band": None}]
    assert "cannot read duration of Show (2000)_S01E01.mkv" in capsys.readouterr().err


def test_post_rip_rerip_same_season_merges_replaces_and_keeps(tmp_path):
    """Re-rip of the next disc, same season (an old one-season document on
    disk): this run's rows replace the episodes they record, episodes this
    disc didn't carry stay recorded, this run's disc entry appends to the
    migrated season's empty index."""
    import json
    pre, _log = _tv_preamble_for(tmp_path)
    pre.title_dir.mkdir()
    old = {"tmdb": {"id": "5", "type": "tv", "title": "Show (2000)", "season": 1},
           "volume_label": "OLD_S1A", "run": "oldrun1", "episodes": [
               {"ep": 1, "file": "Show (2000)_S01E01.mkv", "t": 7,
                "dur_s": 3900, "runtime_s": 4140, "ratio": 0.94, "band": "green"},
               {"ep": 3, "file": "Show (2000)_S01E03.mkv", "t": 9,
                "dur_s": 3840, "runtime_s": 3840, "ratio": 1.0, "band": "green"}],
           "encodes": []}
    (pre.title_dir / "validation.json").write_text(json.dumps(old), encoding="utf-8")
    (pre.title_dir / "Show (2000)_S01E01.mkv").write_bytes(b"x")  # re-staged
    (pre.title_dir / "Show (2000)_S01E02.mkv").write_bytes(b"x")  # this disc

    post_rip_validation(pre, 1, {0: (1, 1), 1: (2, 2)}, {1: 4140, 2: 4080},
                        run=Mock(return_value=Mock(stdout="3930\n")))

    doc = json.loads((pre.title_dir / "validation.json").read_text(encoding="utf-8"))
    assert [s["season"] for s in doc["seasons"]] == [1]
    season = doc["seasons"][0]
    assert [(r["ep"], r["dur_s"], r["t"]) for r in season["episodes"]] == [
        (3, 3840, 9),  # episode this disc didn't carry — kept as recorded
        (1, 3930, 0),  # replaced by this run's row
        (2, 3930, 1),  # this run's new episode appended
    ]
    assert season["discs"] == [{"volume_label": "SHOW_S2", "run": "newrun1",
                                "staged": [1, 2]}]


def test_post_rip_different_id_refuses_and_keeps_old_file(tmp_path, capsys):
    """An existing validation.json naming a different show is never
    overwritten: stderr note, logged, the old file kept byte-for-byte."""
    pre, log = _tv_preamble_for(tmp_path)
    pre.title_dir.mkdir()
    old_text = '{\n  "tmdb": {"id": "999"}\n}'
    path = pre.title_dir / "validation.json"
    path.write_text(old_text, encoding="utf-8")
    (pre.title_dir / "Show (2000)_S01E01.mkv").write_bytes(b"x")

    post_rip_validation(pre, 1, {0: (1, 1)}, {1: 4140},
                        run=Mock(return_value=Mock(stdout="3930\n")))

    assert path.read_text(encoding="utf-8") == old_text
    assert "records a different show" in capsys.readouterr().err
    log.note.assert_any_call("validation",
                             "existing validation.json names a different show — kept")


def test_post_rip_second_season_merges_into_show_document(tmp_path):
    """The same show's second season merges into the folder's document: the
    seasons list holds both, sorted, each season keeping its own rows and
    disc entry (the old one-season-per-document refusal is gone — the
    Downton Abbey case)."""
    import json
    pre, log = _tv_preamble_for(tmp_path)
    pre.title_dir.mkdir()
    old = json.dumps({
        "tmdb": {"id": "5", "type": "tv", "title": "Show (2000)", "season": 2},
        "volume_label": "S2", "run": "oldrun1",
        "episodes": [{"ep": 1, "file": "Show (2000)_S02E01.mkv", "t": 0,
                      "dur_s": 3900, "runtime_s": 4140, "ratio": 0.94,
                      "band": "green"}],
        "encodes": []})
    (pre.title_dir / "validation.json").write_text(old, encoding="utf-8")
    (pre.title_dir / "Show (2000)_S01E01.mkv").write_bytes(b"x")

    post_rip_validation(pre, 1, {0: (1, 1)}, {1: 4140},
                        run=Mock(return_value=Mock(stdout="3930\n")))

    doc = json.loads((pre.title_dir / "validation.json").read_text(encoding="utf-8"))
    assert [s["season"] for s in doc["seasons"]] == [1, 2]
    assert doc["seasons"][0]["episodes"] == [
        {"ep": 1, "file": "Show (2000)_S01E01.mkv", "t": 0, "dur_s": 3930,
         "runtime_s": 4140, "ratio": 0.95, "band": "green"}]
    assert doc["seasons"][0]["discs"] == [
        {"volume_label": "SHOW_S2", "run": "newrun1", "staged": [1]}]
    # season 2's recorded rows survive the merge, index empty (old shape)
    assert doc["seasons"][1]["episodes"][0]["file"] == "Show (2000)_S02E01.mkv"
    assert doc["seasons"][1]["discs"] == []
    assert log.note.called


def test_post_rip_corrupt_existing_file_reads_fresh(tmp_path):
    """A corrupt validation.json reads as no file: this run writes fresh."""
    import json
    pre, _log = _tv_preamble_for(tmp_path)
    pre.title_dir.mkdir()
    (pre.title_dir / "validation.json").write_text("{oops", encoding="utf-8")
    (pre.title_dir / "Show (2000)_S01E01.mkv").write_bytes(b"x")

    post_rip_validation(pre, 1, {0: (1, 1)}, {1: 4140},
                        run=Mock(return_value=Mock(stdout="3930\n")))

    doc = json.loads((pre.title_dir / "validation.json").read_text(encoding="utf-8"))
    assert doc["tmdb"]["id"] == "5"
    assert [r["ep"] for r in doc["seasons"][0]["episodes"]] == [1]


@dataclass
class _ValidationFakeArgs:
    titles = None
    extras = False
    tmdbid = None
    verified = True


def test_full_scan_flow_writes_validation_beside_subtitle_note(tmp_path):
    """The record is born at the show-folder root after the renames, beside
    the subtitle note: rows carry the probed actuals and the map-inverted t;
    nothing is written before the subtitle note (the interrupt's exit point
    is earlier), and the run log closes after the write."""
    import json
    from skive.tmdb import TMDBPick

    episodes = [(1, "ep1", 4140), (2, "ep2", 4080), (3, "ep3", 3840),
                (4, "ep4", 3600)]
    prompt = MagicMock()
    prompt.ask.side_effect = ["", "", ""]  # season, accept range pre-fill, Enter=rip
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True, exist_ok=True)
    durs = {"Show (2000)_S01E01.mkv": 3930, "Show (2000)_S01E02.mkv": 3895,
            "Show (2000)_S01E03.mkv": 3653}

    def fake_run(cmd, **k):
        return Mock(stdout=f"{durs[Path(cmd[-1]).name]}\n")

    def promote_three_junk(stage):
        for n in range(3):
            (show_dir / f"B1_t0{n}.mkv").write_bytes(b"x")

    def subs_after_renames(_dir):
        assert not (show_dir / "validation.json").exists()  # born later, never sooner
        return "subs=dan"

    real_post = post_rip_validation
    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd",
                  side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", _gk_titles())), \
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
            patch("skive.rip_tv.promote_staged", side_effect=promote_three_junk), \
            patch("skive.rip_tv.subtitle_note", side_effect=subs_after_renames), \
            patch("skive.rip_tv.post_rip_validation",
                  lambda pre, *a, **k: real_post(pre, *a, run=fake_run)):
        mock_log.return_value.run = "run-1"  # the validation document's run identity
        with pytest.raises(SystemExit) as e:
            rip_tv(_ValidationFakeArgs())

    assert e.value.code == 0
    doc = json.loads((show_dir / "validation.json").read_text(encoding="utf-8"))
    assert doc["tmdb"] == {"id": "5", "type": "tv", "title": "Show (2000)"}
    season = doc["seasons"][0]
    assert season["season"] == 1
    assert season["discs"] == [{"volume_label": "", "run": "run-1",
                                "staged": [1, 2, 3]}]  # the scan mock carries no label
    assert season["episodes"] == [
        {"ep": 1, "file": "Show (2000)_S01E01.mkv", "t": 0, "dur_s": 3930,
         "runtime_s": 4140, "ratio": 0.95, "band": "green"},
        {"ep": 2, "file": "Show (2000)_S01E02.mkv", "t": 1, "dur_s": 3895,
         "runtime_s": 4080, "ratio": 0.95, "band": "green"},
        {"ep": 3, "file": "Show (2000)_S01E03.mkv", "t": 2, "dur_s": 3653,
         "runtime_s": 3840, "ratio": 0.95, "band": "green"},
    ]
    log = mock_log.return_value
    log.note.assert_any_call("validation", "3 episodes, 0 yellow")
    log.phase_end.assert_called_once()  # the write precedes the phase close


def test_fast_scan_flow_rows_record_t_none(tmp_path):
    """Fast-path rip: the record still lands after the renames, and its rows
    carry t=None — the fast scan's table numbers are scanner indices, not
    real _tNN (slice-04 review P2)."""
    import json
    from skive.tmdb import TMDBPick

    episodes = [(1, "ep1", 4140), (2, "ep2", 4080), (3, "ep3", 3840)]
    prompt = MagicMock()
    prompt.ask.side_effect = ["", "", ""]  # season, accept range pre-fill, Enter=rip
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True, exist_ok=True)

    def join_creates_episodes(_display, _dir, _season, _assignment, _durs, **k):
        for ep in (1, 2, 3):
            (show_dir / f"Show (2000)_S01E0{ep}.mkv").write_bytes(b"x")
        return True

    durs = {"Show (2000)_S01E01.mkv": 3930, "Show (2000)_S01E02.mkv": 3895,
            "Show (2000)_S01E03.mkv": 3653}

    def fake_run(cmd, **k):
        return Mock(stdout=f"{durs[Path(cmd[-1]).name]}\n")

    real_post = post_rip_validation
    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd", return_value=FAST_DURATIONS), \
            patch("skive.rip_common.scan_disc") as mock_scan_disc, \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.RipLog") as mock_log, \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            patch("skive.rip_tv.Prompt", prompt), \
            patch("skive.rip_tv.fetch_season", return_value=episodes), \
            patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent-test.xml")), \
            patch("skive.rip_tv.rip_titles", return_value=0), \
            patch("skive.rip_tv.rename_tv_fast", side_effect=join_creates_episodes), \
            patch("skive.rip_tv.subtitle_note", return_value="subs=dan"), \
            patch("skive.rip_tv.post_rip_validation",
                  lambda pre, *a, **k: real_post(pre, *a, run=fake_run)):
        mock_log.return_value.run = "run-1"  # the validation document's run identity
        with pytest.raises(SystemExit) as e:
            rip_tv(_ValidationFakeArgs())

    assert e.value.code == 0
    mock_scan_disc.assert_not_called()  # the fast scan gated the disc
    doc = json.loads((show_dir / "validation.json").read_text(encoding="utf-8"))
    episodes = doc["seasons"][0]["episodes"]
    assert [r["ep"] for r in episodes] == [1, 2, 3]
    assert [r["dur_s"] for r in episodes] == [3930, 3895, 3653]
    assert [r["t"] for r in episodes] == [None, None, None]


def test_interrupt_midrip_writes_no_validation(tmp_path, capsys):
    """Ctrl+C mid-rip: the in-flight partial is purged, exit 130 — and no
    validation.json exists: the write sits beside the subtitle note, after
    the interrupt's exit point."""
    import json
    from skive.tmdb import TMDBPick

    episodes = [(1, "ep1", 4140), (2, "ep2", 4080), (3, "ep3", 3840),
                (4, "ep4", 3600)]
    prompt = MagicMock()
    prompt.ask.side_effect = ["", "", ""]  # season, accept range pre-fill, Enter=rip
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    d0 = show_dir / "d0"

    def rip_a_partial_then_interrupt(*a, **k):
        d0.mkdir(parents=True, exist_ok=True)  # the preamble already made it
        (d0 / "B1_t0.mkv").write_text("in-flight partial")  # makemkvcon's junk label
        raise KeyboardInterrupt

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.fast_scan_dvd",
                  side_effect=RuntimeError("tests force the full-scan fallback")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", _gk_titles())), \
            patch("skive.rip_common.ensure_mounted"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            patch("skive.rip_tv.Prompt", prompt), \
            patch("skive.rip_tv.fetch_season", return_value=episodes), \
            patch("skive.rip_tv.inject_profile", return_value=tmp_path / "absent-profile.xml"), \
            patch("skive.rip_tv.rip_titles", side_effect=rip_a_partial_then_interrupt):
        with pytest.raises(SystemExit) as e:
            rip_tv(_ValidationFakeArgs())

    assert e.value.code == 130
    assert not (d0 / "B1_t0.mkv").exists()  # the partial is purged
    # An interrupted run writes no Validation file — partial rips stay
    # unrecorded rather than lying (issue 06).
    assert not (show_dir / "validation.json").exists()
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert lines[-1]["phase"] == "total" and lines[-1]["status"] == "interrupted"
    err = capsys.readouterr().err
    assert "skive: interrupted — partial deleted, kept 0 titles" in err
    assert err.count("skive: interrupted") == 1
