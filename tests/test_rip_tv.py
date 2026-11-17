"""Tests for the TV rip flow: season prompt, the selection-table wiring, renames, staging."""

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from dataclasses import dataclass
import pytest

from skive.rip_tv import (
    staged_eps, rename_tv, rip_tv, episode_target,
    post_rip_validation,
)
from skive.scan import TitleInfo


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


@contextmanager
def _prompts(prompt=None):
    """Script ui.ask / ui.confirm from prompt.ask's answers; an empty answer
    takes the prompt's default, as the real helpers do."""
    prompt = prompt or MagicMock()

    def ask(question, choices="", default=""):
        return prompt.ask(question, default=default).strip() or default

    def confirm(question, default=False):
        ans = prompt.ask(question).strip().lower()
        return ans in ("y", "yes") if ans else default

    with patch("skive.ui.ask", ask), patch("skive.ui.confirm", confirm):
        yield prompt


def _gk_titles():
    """Generation Kill disc 1 shape: three episodes and a play-all (t3)."""
    return [TitleInfo(0, 3932, 1, "dan", "", False),
            TitleInfo(1, 3897, 1, "dan", "", False),
            TitleInfo(2, 3653, 1, "dan", "", False),
            TitleInfo(3, 11482, 1, "dan", "", False)]


@pytest.fixture(autouse=True)
def _table_accepts_the_prefill(monkeypatch):
    """The selection table is a real terminal app (tested headless in
    test_selection.py); flow tests stand in for it. The default stand-in
    accepts the prefill; a test passes its own to edit or refuse."""
    monkeypatch.setattr("skive.ui.terminal_present", lambda: True)
    monkeypatch.setattr("skive.rip_tv.run_episode_table",
                        lambda display, emap, ls, cs: dict(emap.assign))


def _range_flow(tmp_path, monkeypatch, mock_titles, mock_episodes, answers=("",),
                staged_names=(), table=None):
    """Drive rip_tv through the season prompt and the table over a temp stage.

    staged_names are created in the show's title folder before the run
    (already-ripped episodes shape the prefill). table replaces the default
    accept-the-prefill stand-in: table(display, emap, list_staging,
    clear_staging) returns the accepted map, QUIT or INTERRUPT. Returns
    (rename_tv mock, console mock, rc).
    """
    from skive.tmdb import TMDBPick

    if table is not None:
        monkeypatch.setattr("skive.rip_tv.run_episode_table", table)
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True, exist_ok=True)
    for name in staged_names:
        (show_dir / name).touch()

    @dataclass
    class FakeArgs:
        tmdbid = None
        verified = True
        keep_all = False

    prompt = MagicMock()
    prompt.ask.side_effect = list(answers)

    # The post-rip validation record probes staged files with ffprobe; flow
    # tests assert the wiring, so its probe subprocess stays a fake here (the
    # record itself is tested directly, below).
    real_post = post_rip_validation
    monkeypatch.setattr("skive.rip_tv.post_rip_validation",
                        lambda pre, *a, **k: real_post(pre, *a, run=Mock(return_value=Mock(stdout="600.0\n"))))

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", mock_titles)):
        with patch("skive.rip_common.ensure_share"):
            with patch("skive.rip_common.purge_junk"):
                with patch("skive.rip_common.require_online"):
                    with patch("skive.rip_common.search_loop", return_value=TMDBPick(
                            title="Show", year="2000", original_language="en", type="tv", id="5")):
                        with _prompts(prompt):
                            with patch("skive.rip_tv.fetch_season", return_value=mock_episodes):
                                with patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent-test.xml")):
                                    with patch("skive.rip_tv.rip_titles", return_value=0) as mock_rip_titles:
                                        with patch("skive.rip_tv.rename_tv") as mock_rename_tv:
                                            with patch("skive.rip_tv.subtitle_note", return_value="subs=dan"):
                                                with patch("skive.rip_tv.console") as mock_console:
                                                    mock_console.prompt = prompt
                                                    mock_console.rip_titles = mock_rip_titles
                                                    # The split phase's probe/cut subprocesses stay
                                                    # fake here too: an armed caption run must never
                                                    # shell out (the phase itself is tested
                                                    # directly in test_chapter_split.py).
                                                    with patch("skive.rip_tv.split_combined"):
                                                        try:
                                                            rip_tv(FakeArgs())
                                                        except SystemExit as e:
                                                            return mock_rename_tv, mock_console, e.code
    return mock_rename_tv, mock_console, None


def _table_accepts_map(monkeypatch, accepted):
    """Stand in for the table accepting exactly `accepted`."""
    monkeypatch.setattr("skive.rip_tv.run_episode_table",
                        lambda display, emap, ls, cs: dict(accepted))


def _captions(console) -> list[str]:
    """Every printed line carrying the play-all-only split caption."""
    return [str(c.args[0]) for c in console.print.call_args_list
            if c.args and "play-all-only disc" in str(c.args[0])]


def test_table_prefill_skips_staged_and_fills_disc(tmp_path, monkeypatch):
    """The map's regression fixture: E01-E03 staged, this disc carries E04-E06.

    Enter accepts the prefill — the episode guess (E4→t0, E5→t1, E6→t2, each
    ratio 1.00); the play-all (t3) prefills skip, so it is not in the accepted
    map and not ripped.
    """
    episodes = [(e, f"ep{e}", rt) for e, rt in
                ((1, 3932), (2, 3897), (3, 3653), (4, 3932), (5, 3897), (6, 3653))]
    staged = [f"Show (2000)_S01E0{e}.mkv" for e in (1, 2, 3)]

    mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, _gk_titles(), episodes, staged_names=staged)

    assert rc == 0
    assert mock_rename_tv.call_args.args[3] == {0: (4, 4), 1: (5, 5), 2: (6, 6)}
    assert console.rip_titles.call_args.args[0] == [0, 1, 2]


def test_table_gets_guess_prefill_with_playall_evidence_and_extra_tail(tmp_path, monkeypatch):
    """The table opens on the guess: the play-all prefills skip carrying its
    `≈ sum of …` evidence, and the eligible no-fit title prefills extra (the
    walk spent t0 on E1 and only 3 episodes exist)."""
    titles = [TitleInfo(0, 3940, 1, "dan", "", False),
              TitleInfo(1, 2855, 1, "dan", "", False),
              TitleInfo(2, 2852, 1, "dan", "", False),
              TitleInfo(3, 9643, 1, "dan", "", False),   # play-all: 3940+2855+2852 = 9647
              TitleInfo(4, 4000, 1, "dan", "", False)]   # 4000/4020 = 0.995, in the band — still extra
    episodes = [(1, "ep1", 4020), (2, "ep2", 2820), (3, "ep3", 2820)]
    seen = {}

    def table(display, emap, list_staging, clear_staging):
        seen["display"], seen["emap"] = display, emap
        return dict(emap.assign)

    mock_rename_tv, _console, rc = _range_flow(tmp_path, monkeypatch, titles, episodes,
                                               table=table)

    assert rc == 0
    emap = seen["emap"]
    assert seen["display"] == "Show (2000)"
    assert emap.assign == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: None, 4: (0, 0)}
    assert emap.evidence[3] == "≈ sum of 0, 1, 2"
    assert mock_rename_tv.call_args.args[3] == {0: (1, 1), 1: (2, 2), 2: (3, 3),
                                                4: (0, 0)}


def test_no_runtimes_prefill_falls_back_to_count(tmp_path, monkeypatch):
    """No TMDB runtimes (season-0 shape): the prefill stays the count-based
    default — one episode per eligible title from the first unstaged
    episode."""
    episodes = [(1, "special 1", 0), (2, "special 2", 0)]

    mock_rename_tv, _console, rc = _range_flow(
        tmp_path, monkeypatch, _gk_titles(), episodes, ["0"])  # season 0

    assert rc == 0
    assert mock_rename_tv.call_args.args[3] == {0: (1, 1), 1: (2, 2), 2: (3, 3)}


def test_accepted_double_reaches_rename(tmp_path, monkeypatch):
    """The accepted table's double (one title, E01-E02) is the rename's map,
    kept as one file."""
    titles = [TitleInfo(0, 2400, 1, "dan", "", False),
              TitleInfo(1, 2450, 1, "dan", "", False)]
    episodes = [(e, f"ep{e}", 2400) for e in range(1, 5)]

    mock_rename_tv, _console, rc = _range_flow(
        tmp_path, monkeypatch, titles, episodes,
        table=lambda display, emap, ls, cs: {0: (3, 3), 1: (1, 2)})

    assert rc == 0
    assert mock_rename_tv.call_args.args[3] == {0: (3, 3), 1: (1, 2)}


def test_all_decoys_disc_prefills_nothing_warns_and_still_rips_an_assignment(
        tmp_path, monkeypatch):
    """Every eligible title decoy-clustered: the table opens prefilled with
    NOTHING, the warn says so loudly, no exit — and a decoy row the operator
    assigns an episode at the cursor still rips (the rescue path)."""
    titles = [TitleInfo(0, 3932, 1_000_000_000, "dan", "", False,
                        decoy_cluster=2),
              TitleInfo(1, 3897, 1_000_000_000, "dan", "", True)]
    episodes = [(1, "ep1", 3932), (2, "ep2", 3897)]
    warns: list[str] = []
    monkeypatch.setattr("skive.rip_tv.ui.warn",
                        lambda text, **k: warns.append(text))
    seen = {}

    def table(display, emap, ls, cs):
        seen["emap"] = emap
        return {0: (1, 1), 1: None}  # the operator assigns the decoy at t0

    mock_rename_tv, _console, rc = _range_flow(
        tmp_path, monkeypatch, titles, episodes, table=table)

    assert rc == 0
    assert seen["emap"].assign == {0: None, 1: None}
    assert seen["emap"].evidence == {0: "decoy cluster ×2", 1: "shuffled map"}
    assert len(warns) == 1 and "decoy" in warns[0]
    assert mock_rename_tv.call_args.args[3] == {0: (1, 1)}


def test_table_quit_rips_nothing_and_exits_1(tmp_path, monkeypatch):
    from skive.selection import QUIT
    mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, _gk_titles(), [(1, "ep1", 3932)],
        table=lambda *a: QUIT)

    assert rc == 1
    console.rip_titles.assert_not_called()
    mock_rename_tv.assert_not_called()


def test_run_without_a_terminal_refuses_before_the_table(tmp_path, monkeypatch, capsys):
    """Non-TTY runs refuse: one stderr line, nonzero exit, nothing spun."""
    monkeypatch.setattr("skive.ui.terminal_present", lambda: False)
    table = Mock(side_effect=AssertionError("the table must not launch"))

    mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, _gk_titles(), [(1, "ep1", 3932)], table=table)

    assert rc == 1
    table.assert_not_called()
    console.rip_titles.assert_not_called()
    assert "selection table needs a terminal" in capsys.readouterr().err


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


# Test discipline fixtures
@pytest.fixture(autouse=True)
def _stage_root(tmp_path, monkeypatch):
    """Keep staging off the real /mnt/videos."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))


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


def _clear_staged_flow(tmp_path, monkeypatch, confirm):
    """Run rip tv over a poisoned staging dir with a table stand-in that
    presses `c`: lists the show's staging, and on `confirm` clears it as the
    real table does after its explicit y. Returns (poisoned path, rc,
    listed names, staged set after the table)."""
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True, exist_ok=True)
    poisoned = show_dir / "Show (2000)_S01E01-E02.mkv"
    poisoned.touch()
    titles = [TitleInfo(0, 21 * 60, 1, "dan", "", False),
              TitleInfo(1, 22 * 60, 1, "dan", "", False),
              TitleInfo(2, 23 * 60, 1, "dan", "", False)]
    # fetch_season is mocked past its minutes->seconds conversion
    episodes = [(1, "ep1", 21 * 60), (2, "ep2", 22 * 60), (3, "ep3", 23 * 60)]
    seen = {}

    def table(display, emap, list_staging, clear_staging):
        doomed = list_staging()
        seen["listed"] = [f.name for f in doomed]
        if confirm:
            seen["staged"] = clear_staging(doomed)
            return dict(emap.assign)
        from skive.selection import QUIT
        return QUIT

    _rename, _console, rc = _range_flow(tmp_path, monkeypatch, titles, episodes,
                                        table=table)
    return poisoned, rc, seen


def test_clear_staged_at_table_deletes_and_rips(tmp_path, monkeypatch):
    """c + confirm at the table deletes the show's staged files, refreshes
    the staged set, and the run rips."""
    poisoned, rc, seen = _clear_staged_flow(tmp_path, monkeypatch, confirm=True)
    assert seen["listed"] == ["Show (2000)_S01E01-E02.mkv"]
    assert not poisoned.exists()
    assert seen["staged"] == set()
    assert rc == 0


def test_clear_staged_declined_keeps_files(tmp_path, monkeypatch):
    """Without the table's explicit confirm nothing is deleted."""
    poisoned, rc, seen = _clear_staged_flow(tmp_path, monkeypatch, confirm=False)
    assert seen["listed"] == ["Show (2000)_S01E01-E02.mkv"]
    assert poisoned.exists()
    assert rc == 1


def test_sub_floor_title_is_visible_prefilled_skip_and_not_ripped(tmp_path, monkeypatch):
    """A 30s menu loop is under the floor: it lists last in the table with
    its real disc number, prefilled skip, outside the episode guess — and an
    accepted skip is never ripped (total= still counts the floored titles)."""
    titles = [TitleInfo(0, 30, 1, "dan", "", False),   # sub-floor, scanned first
              TitleInfo(1, 2400, 1, "dan", "", False)]
    seen = {}

    def table(display, emap, list_staging, clear_staging):
        seen["emap"] = emap
        return dict(emap.assign)

    mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, titles, [(1, "ep1", 2400)], table=table)

    emap = seen["emap"]
    assert [t.number for t in emap.titles] == [1, 0]  # the sub-floor row sits last
    assert emap.assign == {0: None, 1: (1, 1)}
    assert emap.evidence[0] == "below the TV floor"
    assert console.rip_titles.call_args.args[0] == [1]
    assert console.rip_titles.call_args.kwargs["total"] == 1
    assert mock_rename_tv.call_args.args[3] == {1: (1, 1)}


def test_sub_floor_rescue_rips_and_joins_the_map(tmp_path, monkeypatch):
    """Typing an episode at a sub-floor row rescues it: it rips, mapped."""
    titles = [TitleInfo(0, 30, 1, "dan", "", False),
              TitleInfo(1, 2400, 1, "dan", "", False)]

    mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, titles, [(1, "ep1", 2400), (2, "ep2", 600)],
        table=lambda display, emap, ls, cs: {0: (2, 2), 1: (1, 1)})

    assert console.rip_titles.call_args.args[0] is None  # nothing skipped: all
    assert mock_rename_tv.call_args.args[3] == {0: (2, 2), 1: (1, 1)}


def test_playall_prefilled_skip_is_not_ripped(tmp_path, monkeypatch):
    """A play-all prefills skip whatever the episode guess says: title 2
    (3550s) sits within 120s of t0+t1 (3500s), so the rip selection leaves it
    out and the map never names it."""
    titles = [TitleInfo(0, 1800, 1, "dan", "", False),
              TitleInfo(1, 1700, 1, "dan", "", False),
              TitleInfo(2, 3550, 1, "dan", "", False)]
    episodes = [(1, "ep1", 1800), (2, "ep2", 1700), (3, "ep3", 3550)]

    mock_rename_tv, console, rc = _range_flow(tmp_path, monkeypatch, titles, episodes)

    assert console.rip_titles.call_args.args[0] == [0, 1]
    assert mock_rename_tv.call_args.args[3] == {0: (1, 1), 1: (2, 2)}


def test_rescued_playall_rips_with_its_episode(tmp_path, monkeypatch):
    """Assigning a play-all an episode in the table rescues it: nothing is
    skipped, so every title rips and the map carries the play-all's episode."""
    episodes = [(1, "ep1", 3932), (2, "ep2", 3897), (3, "ep3", 3653),
                (4, "ep4", 3600), (5, "ep5", 3700)]

    def table(display, emap, list_staging, clear_staging):
        accepted = dict(emap.assign)
        accepted[3] = (5, 5)
        return accepted

    mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, _gk_titles(), episodes, table=table)

    assert console.rip_titles.call_args.args[0] is None
    assert mock_rename_tv.call_args.args[3] == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (5, 5)}


# The play-all-only caption (ADR 0009): derived from the accepted map, echoed
# after the accepted table, before the drive spins.
def test_playall_only_caption_arms_on_accepted_range(tmp_path, monkeypatch):
    """A disc whose only title carries the whole season: the accepted table's
    lone `E01-E03` arms the caption, naming the title and the episode range
    it carries — and the chapter-split trigger sees the same map."""
    titles = [TitleInfo(0, 9643, 1, "dan", "", False)]
    episodes = [(1, "ep1", 4020), (2, "ep2", 2820), (3, "ep3", 2820)]

    mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, titles, episodes,
        table=lambda display, emap, ls, cs: {0: (1, 3)})

    assert rc == 0
    assert mock_rename_tv.call_args.args[3] == {0: (1, 3)}
    captions = _captions(console)
    assert len(captions) == 1
    assert "title 0" in captions[0]
    assert "S01E01-E03" in captions[0]
    assert "split after the rip" in captions[0]


def test_playall_only_caption_hides_on_multi_title_disc(tmp_path, monkeypatch):
    """The ordinary multi-title disc — double-free, play-all skipped — never
    captions, whatever the table shows."""
    episodes = [(e, f"ep{e}", rt) for e, rt in
                ((1, 3932), (2, 3897), (3, 3653))]

    _mock_rename_tv, console, rc = _range_flow(
        tmp_path, monkeypatch, _gk_titles(), episodes)

    assert rc == 0
    assert _captions(console) == []


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
    prompt.ask.side_effect = [""]  # season
    _table_accepts_map(monkeypatch, {0: (1, 3)})
    # pytest's stdin is not a terminal; the split must reach the table to be
    # interrupted at it.
    monkeypatch.setattr("skive.chapter_split.sys.stdin.isatty", lambda: True)

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True
        keep_all = False

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc",
                  return_value=("DVD-ROM", "", [TitleInfo(0, 9643, 1, "dan", "", False)])), \
            patch("skive.rip_common.ensure_share"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            _prompts(prompt), \
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
    prompt.ask.side_effect = [""]
    _table_accepts_map(monkeypatch, {0: (1, 3)})

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True
        keep_all = False

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc",
                  return_value=("DVD-ROM", "", [TitleInfo(0, 9643, 1, "dan", "", False)])), \
            patch("skive.rip_common.ensure_share"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            _prompts(prompt), \
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


def test_split_interrupt_stays_contained_run_continues(tmp_path, monkeypatch,
                                                       capsys):
    """Ctrl+C at the boundary table (# spill: chapter-split-table — the
    interrupt is staged at the run_boundary_table seam now): the split sweeps
    and notes `interrupted`,
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

    subs_seen = []

    def fake_subs(_dir):
        subs_seen.append(True)
        return "subs=dan"

    real_post = post_rip_validation
    prompt = MagicMock()
    prompt.ask.side_effect = [""]  # season
    _table_accepts_map(monkeypatch, {0: (1, 3)})
    # pytest's stdin is not a terminal; the split must reach the table to be
    # interrupted at it.
    monkeypatch.setattr("skive.chapter_split.sys.stdin.isatty", lambda: True)

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True
        keep_all = False

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc",
                  return_value=("DVD-ROM", "", [TitleInfo(0, 9643, 1, "dan", "", False)])), \
            patch("skive.rip_common.ensure_share"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            _prompts(prompt), \
            patch("skive.rip_tv.fetch_season", return_value=episodes), \
            patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent-test.xml")), \
            patch("skive.rip_tv.rip_titles", return_value=0), \
            patch("skive.rip_tv.rename_tv", side_effect=fake_rename), \
            patch("skive.chapter_split.probe_chapters", side_effect=fake_probe), \
            patch("skive.chapter_split.run_boundary_table",
                  side_effect=KeyboardInterrupt), \
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
    assert "info  split: interrupted" in capsys.readouterr().err


def test_episode_target_singles_doubles_and_seasons():
    """The shared target helper names singles, doubles and specials."""
    stage = Path("/stage")
    assert episode_target(stage, "Show (2000)", 1, 3, 3) == stage / "Show (2000)_S01E03.mkv"
    assert episode_target(stage, "Show (2000)", 1, 1, 2) == stage / "Show (2000)_S01E01-E02.mkv"
    assert episode_target(stage, "Show (2000)", 0, 5, 5) == stage / "Show (2000)_S00E05.mkv"


def test_full_scan_rip_selection_skips_playall_and_sub_floor(tmp_path, monkeypatch):
    """The rip selection excludes every prefilled skip: the play-all (t2,
    3550s within 120s of t0+t1) and the sub-floor making-of (t3, 300s) — both
    visible in the table, neither ripped unless the operator rescues them."""
    titles = [TitleInfo(0, 1800, 1, "dan", "", False),
              TitleInfo(1, 1700, 1, "dan", "", False),
              TitleInfo(2, 3550, 1, "dan", "", False),
              TitleInfo(3, 300, 1, "dan", "", False)]
    episodes = [(1, "ep1", 1800), (2, "ep2", 1700), (3, "ep3", 3550)]

    _rename, console, rc = _range_flow(tmp_path, monkeypatch, titles, episodes)

    assert console.rip_titles.call_args.args[0] == [0, 1]


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
        keep_all = False

    prompt = MagicMock()
    prompt.ask.side_effect = KeyboardInterrupt

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", _gk_titles())), \
            patch("skive.rip_common.ensure_share"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            _prompts(prompt):
        with pytest.raises(SystemExit) as e:
            rip_tv(FakeArgs())

    assert e.value.code == 130
    assert (show_dir / "Show (2000)_S01E01.mkv").exists()
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert lines[-1]["phase"] == "total" and lines[-1]["status"] == "interrupted"
    err = capsys.readouterr().err
    assert "fail  interrupted — partial deleted, kept 1 titles" in err
    assert err.count("fail  interrupted") == 2  # event plus interrupted result line


def test_table_interrupt_drops_inflight_partial(tmp_path, monkeypatch):
    """Ctrl+C at the selection table (after a previous disc's rips): the exit
    is 130 and the junk the table's run would have re-ripped is left clean."""
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
        keep_all = False

    prompt = MagicMock()
    # season Enter, then Ctrl+C inside the table (the app's INTERRUPT result)
    prompt.ask.side_effect = [""]
    from skive.selection import INTERRUPT
    monkeypatch.setattr("skive.rip_tv.run_episode_table", lambda *a: INTERRUPT)

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", _gk_titles())), \
            patch("skive.rip_common.ensure_share"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            _prompts(prompt), \
            patch("skive.rip_tv.fetch_season", return_value=[(e, f"ep{e}", 3600) for e in range(1, 5)]):
        with pytest.raises(SystemExit) as e:
            rip_tv(FakeArgs())

    assert e.value.code == 130
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert lines[-1]["status"] == "interrupted"


# -- Post-rip validation record (issue 06, map.md decision 6) ------------------

def _tv_preamble_for(tmp_path):
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
                   keep="", disc=0, label="SHOW_S2")
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
    keep_all = False


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
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", _gk_titles())), \
            patch("skive.rip_common.ensure_share"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            patch("skive.rip_common.RipLog") as mock_log, \
            _prompts(prompt), \
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
            patch("skive.rip_common.scan_disc", return_value=("DVD-ROM", "", _gk_titles())), \
            patch("skive.rip_common.ensure_share"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            _prompts(prompt), \
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
    assert "fail  interrupted — partial deleted, kept 0 titles" in err
    assert err.count("fail  interrupted") == 2  # event plus interrupted result line


# --- Run view wiring (slice 07: suspend + per-episode run summary) ----------


def _view_flow(tmp_path, monkeypatch, answers, episodes=None, titles=None,
               rename=None, rip_rc=0, rip=None, staged=()):
    """Drive rip_tv over a full-scan disc with the REAL console (capsys
    captures what a user sees) and a seam run view set by the caller.
    staged names files already in the show folder before the run.
    Returns the SystemExit code."""
    from skive.tmdb import TMDBPick

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True)
    for name in staged:
        (show_dir / name).write_bytes(b"old")
    episodes = episodes or [(1, "ep1", 3932), (2, "ep2", 3897), (3, "ep3", 3653)]
    durs = {"Show (2000)_S01E01.mkv": 3930, "Show (2000)_S01E02.mkv": 3890,
            "Show (2000)_S01E03.mkv": 3650}

    def fake_rename(_display, _dir, _season, _assignment):
        for name in durs:
            (show_dir / name).write_bytes(b"x" * 1024)
        return {show_dir / name: num for num, name in enumerate(durs)}

    def fake_run(cmd, **k):
        return Mock(stdout=f"{durs.get(Path(cmd[-1]).name, 600.0)}\n")

    real_post = post_rip_validation
    prompt = MagicMock()
    prompt.ask.side_effect = answers

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True
        keep_all = False

    with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
            patch("skive.rip_common.scan_disc",
                  return_value=("DVD-ROM", "", titles or _gk_titles())), \
            patch("skive.rip_common.ensure_share"), \
            patch("skive.rip_common.purge_junk"), \
            patch("skive.rip_common.require_online"), \
            patch("skive.rip_common.search_loop", return_value=TMDBPick(
                title="Show", year="2000", original_language="en", type="tv", id="5")), \
            _prompts(prompt), \
            patch("skive.rip_tv.fetch_season", return_value=episodes), \
            patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent-test.xml")), \
            patch("skive.rip_tv.rip_titles", return_value=rip_rc, side_effect=rip), \
            patch("skive.rip_tv.rename_tv", side_effect=rename or fake_rename), \
            patch("skive.rip_tv.split_combined"), \
            patch("skive.rip_tv.subtitle_note", return_value="subs=dan"), \
            patch("skive.rip_tv.post_rip_validation",
                  lambda pre, *a, **k: real_post(pre, *a, run=fake_run)):
        with pytest.raises(SystemExit) as e:
            rip_tv(FakeArgs())
        return e.value.code


def test_rip_tv_prompts_print_under_a_seam_view(tmp_path, monkeypatch, capsys):
    """The season prompt and the accepted table's echo print to stdout
    (scrollback) while a seam view is set — every interactive surface
    suspends the view — and the run completes."""
    from skive import ui

    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        code = _view_flow(tmp_path, monkeypatch, ["", "", ""])
    finally:
        ui.clear_view()
    assert code == 0
    err = capsys.readouterr().err
    assert "Show (2000) — season 01" in err  # the accepted table's echo
    assert err.count("action") == 1


def test_rip_tv_run_summary_rows_and_count_after_close(tmp_path, monkeypatch, capsys):
    """The run summary: per staged episode rows (SnnEnn × duration × size ×
    result) plus the ok/failed count, printed after the view closed."""
    from skive import ui

    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    seen = []
    real = ui.run_summary
    monkeypatch.setattr(ui, "run_summary",
                        lambda *a, **k: seen.append(view._closed) or real(*a, **k))
    try:
        code = _view_flow(tmp_path, monkeypatch, ["", "", ""])
    finally:
        ui.clear_view()
    assert code == 0
    assert seen == [True]  # closed before the summary printed
    err = capsys.readouterr().err
    for col in ("Episode", "duration", "size", "result"):
        assert col in err
    assert any("S01E01" in l and "1:05:30" in l and "ok" in l
               for l in err.splitlines())
    assert "ok    3 ok · " in err


def test_rip_tv_failed_rip_with_no_rows_ends_on_a_failure_line(tmp_path, monkeypatch, capsys):
    """makemkvcon fails and no episode staged: each attempted title's
    missing output is a failed row, and the result line reports the failure
    the exit code carries."""
    code = _view_flow(tmp_path, monkeypatch, ["", "", ""],
                      rename=lambda *a: None, rip_rc=2)
    assert code == 2
    last = capsys.readouterr().err.splitlines()[-1]
    assert last.startswith("fail  3 failed · 0 ok · ") and "see " in last


def test_rip_tv_interrupt_fail_line_survives_a_closed_tty_view(tmp_path, monkeypatch, capsys):
    """Ctrl+C mid-rip (the inner arm): the view closes before
    interrupted_exit's ui.fail — the fail line reaches stderr once, exit 130."""
    from skive import ui
    from skive.tmdb import TMDBPick

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)" / "d0"

    def interrupt(*a, **k):
        show_dir.mkdir(parents=True, exist_ok=True)
        k["on_title_start"](0)  # a title's frame is open when Ctrl+C lands
        raise KeyboardInterrupt

    prompt = MagicMock()
    prompt.ask.side_effect = ["", "", ""]

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True
        keep_all = False

    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
                patch("skive.rip_common.scan_disc",
                      return_value=("DVD-ROM", "", _gk_titles())), \
                patch("skive.rip_common.ensure_share"), \
                patch("skive.rip_common.purge_junk"), \
                patch("skive.rip_common.require_online"), \
                patch("skive.rip_common.search_loop", return_value=TMDBPick(
                    title="Show", year="2000", original_language="en", type="tv", id="5")), \
                _prompts(prompt), \
                patch("skive.rip_tv.fetch_season",
                      return_value=[(1, "ep1", 3932), (2, "ep2", 3897), (3, "ep3", 3653)]), \
                patch("skive.rip_tv.inject_profile", return_value=Path("/tmp/absent.xml")), \
                patch("skive.rip_tv.rip_titles", side_effect=interrupt):
            with pytest.raises(SystemExit) as e:
                rip_tv(FakeArgs())
    finally:
        ui.clear_view()

    assert e.value.code == 130
    assert view._closed
    err = capsys.readouterr().err
    assert "fail  interrupted" in err
    assert err.count("fail  interrupted") == 2  # event plus interrupted result line
    # the opener box: title in the border, scope lines inside, before the frame
    assert (err.index("\u256d\u2500 skive rip tv \u2500") < err.index("\u2502 Show (2000) \u00b7 season 1 ")
            < err.index("\u2502 disc 0 \u00b7 ") < err.index("\u256d\u2500 1/"))
    # the open frame closes as interrupted before the summary
    assert err.index("\u2570\u2500 \u2717 interrupted ") < err.index("fail  interrupted")


def test_rip_tv_prompt_interrupt_closes_the_view(tmp_path, monkeypatch, capsys):
    """Ctrl+C at the season prompt (the outer arm): the view still closes
    before the interrupt exit, 130, one fail line."""
    import json
    from skive import ui
    from skive.tmdb import TMDBPick

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    (tmp_path / "rip" / "tv" / "Show (2000)").mkdir(parents=True)
    prompt = MagicMock()
    prompt.ask.side_effect = KeyboardInterrupt

    @dataclass
    class FakeArgs:
        titles = "all"
        extras = False
        tmdbid = None
        verified = True
        keep_all = False

    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch("skive.rip_common.sniff_disc", return_value=("DVD-ROM", "", "")), \
                patch("skive.rip_common.scan_disc",
                      return_value=("DVD-ROM", "", _gk_titles())), \
                patch("skive.rip_common.ensure_share"), \
                patch("skive.rip_common.purge_junk"), \
                patch("skive.rip_common.require_online"), \
                patch("skive.rip_common.search_loop", return_value=TMDBPick(
                    title="Show", year="2000", original_language="en", type="tv", id="5")), \
                _prompts(prompt):
            with pytest.raises(SystemExit) as e:
                rip_tv(FakeArgs())
    finally:
        ui.clear_view()

    assert e.value.code == 130
    assert view._closed
    err = capsys.readouterr().err
    assert err.count("fail  interrupted") == 2  # event plus interrupted result line


# --- rip tv on the new look (console-polish slice 07) -----------------------


def test_rip_tv_opener_after_map_and_headings_as_titles_start(tmp_path, monkeypatch, capsys):
    """The run opener prints after the accepted map, before the rip; each
    title's item heading prints when that title starts (between rip
    events), named by its episode; rip_titles gets the footer names and the
    drive."""
    seen = {}

    def rip(*a, **k):
        seen.update(k)
        for number in (0, 1, 2):
            k["on_title_start"](number)
            print(f"ripping {number}", file=sys.stderr)
        return 0

    code = _view_flow(tmp_path, monkeypatch, ["", "", ""], rip=rip)
    assert code == 0
    err = capsys.readouterr().err
    assert seen["names"] == ["Show (2000) S01E01", "Show (2000) S01E02",
                             "Show (2000) S01E03"]
    assert "device" in seen
    opener = err.index("info  skive rip tv · Show (2000) · season 1 · disc 0")
    assert err.rindex("action") < opener  # after the accepted table's echo
    h1 = err.index("info  1/3  Show (2000) S01E01")
    h2 = err.index("info  2/3  Show (2000) S01E02")
    assert opener < h1 < err.index("ripping 0") < h2 < err.index("ripping 1")
    assert "info  3/3  Show (2000) S01E03" in err


def test_rip_tv_summary_marks_only_the_failed_title(tmp_path, monkeypatch, capsys):
    """A selected-title rip reports each title's own rc: only the episode
    whose title failed reads failed; the run still ends on a failure line
    with the run log location."""
    def rip(*a, **k):
        k["outcomes"].update({0: 0, 1: 2, 2: 0})
        return 2

    # the play-all (t3) prefills skip: the per-title rip path
    code = _view_flow(tmp_path, monkeypatch, ["", "", ""], rip=rip)
    assert code == 2
    err = capsys.readouterr().err
    rows = {l.split()[0]: l for l in err.splitlines() if l.strip().startswith("S01E0")
            and "KiB" in l}
    assert rows["S01E01"].rstrip().endswith("ok")
    assert rows["S01E02"].rstrip().endswith("failed")
    assert rows["S01E03"].rstrip().endswith("ok")
    last = err.splitlines()[-1]
    assert last.startswith("fail  1 failed · 2 ok · ") and "see " in last


def test_rip_tv_summary_includes_this_runs_extras_not_old_files(tmp_path, monkeypatch, capsys):
    """Summary rows are this run's items: its episodes and extras, never a
    file that predates the run."""
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"

    def rename(_display, _dir, _season, _assignment):
        for name in ("Show (2000)_S01E01.mkv", "Show (2000)_S01E02.mkv",
                     "Show (2000)_S01E03.mkv", "Show (2000) - Extra 01.mkv"):
            (show_dir / name).write_bytes(b"x" * 1024)

    code = _view_flow(tmp_path, monkeypatch, ["", "", ""], rename=rename)
    assert code == 0
    err = capsys.readouterr().err
    assert any("Show (2000) - Extra 01" in l and "ok" in l for l in err.splitlines())
    assert "ok    4 ok · " in err


def test_rip_tv_summary_counts_a_ripped_sub_floor_title_ok(tmp_path, monkeypatch, capsys):
    """Full scan: a sub-TV-floor making-of (t3, 300s) rescued as an extra in
    the table rips and the real rename files it as an Extra. It is a
    successful item, not a failed one: the result line matches exit 0."""
    from skive.rip_tv import rename_tv as real_rename_tv
    titles = [TitleInfo(0, 3932, 1, "dan", "", False),
              TitleInfo(1, 3897, 1, "dan", "", False),
              TitleInfo(2, 3653, 1, "dan", "", False),
              TitleInfo(3, 300, 1, "dan", "", False)]
    ripped = []

    def rip(selection, _profile, _minlength, stage, **k):
        ripped.append(selection)
        stage.mkdir(parents=True, exist_ok=True)
        for t in range(4):
            (stage / f"B1_t{t:02d}.mkv").write_bytes(b"x" * 1024)
        return 0

    def table(display, emap, list_staging, clear_staging):
        accepted = dict(emap.assign)
        accepted[3] = (0, 0)  # `e` at the sub-floor row
        return accepted

    monkeypatch.setattr("skive.rip_tv.run_episode_table", table)
    code = _view_flow(tmp_path, monkeypatch, [""], titles=titles,
                      rip=rip, rename=real_rename_tv)
    assert code == 0
    assert ripped == [None]  # nothing skipped: all titles, the rescued one included
    err = capsys.readouterr().err
    assert any("Show (2000) - Extra 01" in l and l.rstrip().endswith("ok")
               for l in err.splitlines())
    assert "failed" not in err
    assert err.splitlines()[-1].startswith("ok    4 ok · ")


def test_rip_tv_heading_follows_the_started_title_not_the_count(tmp_path, monkeypatch, capsys):
    """A selected-title rip: title 1 fails before saving starts, so only
    titles 0 and 2 call back — title 2's heading carries its own episode
    and position, never the failed title's label."""
    def rip(*a, **k):
        k["on_title_start"](0)
        k["on_title_start"](2)
        k["outcomes"].update({0: 0, 1: 2, 2: 0})
        return 2

    code = _view_flow(tmp_path, monkeypatch, ["", "", ""], rip=rip)
    assert code == 2
    err = capsys.readouterr().err
    assert "info  1/3  Show (2000) S01E01" in err
    assert "info  3/3  Show (2000) S01E03" in err
    assert "2/3  Show (2000) S01E02" not in err


def test_rip_tv_summary_keeps_a_cleared_path_restaged_by_the_run(tmp_path, monkeypatch, capsys):
    """c + y clears a staged episode; this run re-stages the same path, so
    the summary lists it — a cleared file no longer predates the run."""
    def table(display, emap, list_staging, clear_staging):
        clear_staging(list_staging())  # c, then the explicit y
        return {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: None}

    monkeypatch.setattr("skive.rip_tv.run_episode_table", table)
    code = _view_flow(tmp_path, monkeypatch, [""],
                      staged=["Show (2000)_S01E01.mkv"])
    assert code == 0
    err = capsys.readouterr().err
    assert any(l.strip().startswith("S01E01") and l.rstrip().endswith("ok")
               for l in err.splitlines())
    assert "ok    3 ok · " in err


def test_summary_rows_lists_attempted_titles_with_missing_output_failed(tmp_path):
    """An attempted title that left no file still gets a failed row — an
    episode by its code, an extra as `extra`."""
    from skive.rip_tv import _summary_rows
    (tmp_path / "Show_S01E01.mkv").write_bytes(b"x")
    probed = [{"ep": 1, "file": "Show_S01E01.mkv", "t": 0, "dur_s": 60}]
    rows = _summary_rows(tmp_path, "Show", 1, probed, set(), 2, {0: 0, 1: 2, 2: 2},
                         [0, 1, 2], {0: (1, 1), 1: (2, 3), 2: (0, 0)}, {})
    assert [(r[0], r[3]) for r in rows] == [("S01E01", "ok"), ("S01E02-E03", "failed"),
                                            ("extra", "failed")]


def test_summary_rows_grades_each_extra_by_its_own_title(tmp_path):
    """A failed episode title does not fail the extras: each renamed extra
    reads its own title's rc."""
    from skive.rip_tv import _summary_rows
    ok_extra = tmp_path / "Show - Extra 01.mkv"
    bad_extra = tmp_path / "Show - Extra 02.mkv"
    ok_extra.write_bytes(b"x")
    bad_extra.write_bytes(b"x")
    rows = _summary_rows(tmp_path, "Show", 1, [], set(), 2, {0: 2, 1: 0, 2: 2},
                         [1, 2], {1: (0, 0), 2: (0, 0)}, {ok_extra: 1, bad_extra: 2})
    assert [(r[0], r[3]) for r in rows] == [("Show - Extra 01", "ok"),
                                            ("Show - Extra 02", "failed")]


def test_summary_rows_keep_a_multi_episode_files_full_range(tmp_path):
    """A retained double (or a declined chapter split) reads `S01E01-E03`,
    not just its first episode."""
    from skive.rip_tv import _summary_rows
    (tmp_path / "Show_S01E01-E03.mkv").write_bytes(b"x")
    probed = [{"ep": 1, "file": "Show_S01E01-E03.mkv", "t": 0, "dur_s": 60}]
    rows = _summary_rows(tmp_path, "Show", 1, probed, set(), 0, {}, [0], {0: (1, 3)}, {})
    assert [(r[0], r[3]) for r in rows] == [("S01E01-E03", "ok")]


def test_rip_tv_interrupt_summary_marks_inflight_title_interrupted(tmp_path, monkeypatch, capsys):
    """Ctrl+C while a title rips: the summary lists that title interrupted
    and the result line says the partial was deleted; exit 130."""
    def rip(*a, **k):
        k["on_title_start"](0)
        raise KeyboardInterrupt

    code = _view_flow(tmp_path, monkeypatch, ["", "", ""], rip=rip)
    assert code == 130
    err = capsys.readouterr().err
    assert any("Show (2000) S01E01" in l and l.rstrip().endswith("interrupted")
               for l in err.splitlines())
    assert err.splitlines()[-1].startswith("fail  interrupted · 0 ok · ")
    assert err.splitlines()[-1].endswith("— partial deleted")


def test_rip_tv_audio_gate_trips_and_keeps_staged(tmp_path, monkeypatch, capsys):
    """A silent episode fails the run loudly — note names the file and the
    -K remedy — and the staged file stays put (ADR 0019/0022)."""
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    show_dir.mkdir(parents=True)
    ep = show_dir / "Show (2000)_S01E01.mkv"
    ep.touch()
    gate = Mock(return_value=[ep])
    monkeypatch.setattr("skive.rip_tv.silent_staged", gate)

    _, _, rc = _range_flow(tmp_path, monkeypatch, _gk_titles(), [(1, "ep1", 3932)])

    assert rc == 1
    assert ep.exists()
    err = capsys.readouterr().err
    assert f"{ep.name}: no audio stream" in err
    assert "--keep-all" in err


def test_rip_tv_audio_gate_healthy_is_silent(tmp_path, monkeypatch, capsys):
    """Episodes with audio pass with zero new output; rc stays 0."""
    gate = Mock(return_value=[])
    monkeypatch.setattr("skive.rip_tv.silent_staged", gate)

    _, _, rc = _range_flow(tmp_path, monkeypatch, _gk_titles(), [(1, "ep1", 3932)])

    assert rc == 0
    assert "no audio stream" not in capsys.readouterr().err


def test_rip_tv_subtitle_shortfall_warns_and_stays_green(tmp_path, monkeypatch, capsys):
    """A scan-promised keep-set language missing from a staged episode warns
    (stderr + "subtitle" phase note in the run log) and never fails the run."""
    from skive import ui

    warning = "Show (2000)_S01E01.mkv: scan promised dan, staged none of it"
    # A scan row whose subtitle track promises dan — the shortfall mapping
    # must carry each renamed episode with that promise.
    titles = [TitleInfo(0, 3932, 1, "dan", "dan", False),
              TitleInfo(1, 3897, 1, "dan", "dan", False),
              TitleInfo(2, 3653, 1, "dan", "dan", False),
              TitleInfo(3, 11482, 1, "dan", "", False)]
    captured = {}

    def fake_shortfall(files_promises, run=None):
        captured.update(files_promises)
        return [warning]

    monkeypatch.setattr("skive.rip_tv.subtitle_shortfall", fake_shortfall)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        code = _view_flow(tmp_path, monkeypatch, ["", "", ""], titles=titles)
    finally:
        ui.clear_view()
    assert code == 0  # warn, never fail (ADR 0022)
    show_dir = tmp_path / "rip" / "tv" / "Show (2000)"
    expected = {show_dir / f"Show (2000)_S01E0{n}.mkv": {"dan"} for n in (1, 2, 3)}
    assert captured == expected, f"shortfall mapping {captured} != {expected}"
    assert warning in capsys.readouterr().err
    lines = (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()
    assert any('"subtitle"' in l and "scan promised dan" in l for l in lines)


def test_rip_tv_subtitle_shortfall_quiet_when_nothing_promised(tmp_path, monkeypatch, capsys):
    """No keep-set subtitles promised — zero shortfall output, rc 0."""
    from skive import ui

    monkeypatch.setattr("skive.rip_tv.subtitle_shortfall", lambda files_promises, run=None: [])
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        code = _view_flow(tmp_path, monkeypatch, ["", "", ""])
    finally:
        ui.clear_view()
    assert code == 0
    err = capsys.readouterr().err
    assert "staged none of it" not in err
    assert "warn " not in err
