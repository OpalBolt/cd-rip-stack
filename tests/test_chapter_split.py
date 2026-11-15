"""Tests for chapter-split pieces: the one-call chapter probe and the
boundary suggestion (nearest start, ±120s window flag, declines); plus the
boundary table, prompt loop, cut, and phase orchestration (slice 03)."""

import asyncio
import json
from pathlib import Path
from io import StringIO
from types import SimpleNamespace

from rich.console import Console
from unittest.mock import Mock


import skive.chapter_split as chapter_split
from skive.chapter_split import (
    boundary_flags, boundary_note, print_accepted_boundaries, probe_chapters,
    split_combined, spans_from_starts, suggest_boundaries, BoundaryTable,
)
from skive.selection import QUIT


# Downton S4 D1 shape: one combined title, 21 chapters ~7.8 min apart, three
# ~54-minute episodes; PAL-short so boundaries sit slightly under the sums.
DOWNTON_STARTS = [k * 468 for k in range(21)]
DOWNTON_DURATION = 9410.0
DOWNTON_RUNTIMES = {1: 3240, 2: 3240, 3: 3240}


def ffprobe_json(starts, duration):
    """Realistic `-show_entries format=duration:chapter=start_time -of json`
    output: string times at 6dp, duration under format."""
    return json.dumps({
        "chapters": [{"id": i, "time_base": "1/1000", "start": int(s * 1000),
                      "start_time": f"{s:.6f}"}
                     for i, s in enumerate(starts)],
        "format": {"filename": "disc.mkv", "duration": f"{duration:.6f}"},
    })


# The probe: chapter starts plus container duration in one injectable call
def test_probe_chapters_one_call_parses_both_values():
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0,
                               stdout=ffprobe_json([0.0, 3276.0, 6552.0], 9410.0),
                               stderr="")

    assert probe_chapters(Path("/x/Show_S01E01-E03.mkv"), run=run) \
        == ([0.0, 3276.0, 6552.0], 9410.0)
    cmd, kwargs = calls[0]
    assert cmd == ["ffprobe", "-v", "error", "-show_entries",
                   "format=duration:chapter=start_time", "-of", "json",
                   "/x/Show_S01E01-E03.mkv"]
    assert kwargs == {"capture_output": True, "text": True}


def test_probe_chapters_bad_exit_returns_none():
    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="No such file")

    assert probe_chapters(Path("/x/Show.mkv"), run=run) is None


def test_probe_chapters_junk_output_returns_none():
    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="not json", stderr="")

    assert probe_chapters(Path("/x/Show.mkv"), run=run) is None


def test_probe_chapters_missing_duration_returns_none():
    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps({"chapters": []}),
                               stderr="")

    assert probe_chapters(Path("/x/Show.mkv"), run=run) is None


def test_probe_chapters_unparseable_chapter_start_returns_none():
    def run(cmd, **kwargs):
        out = json.dumps({"chapters": [{"start_time": "N/A"}],
                          "format": {"duration": "9410.000000"}})
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    assert probe_chapters(Path("/x/Show.mkv"), run=run) is None


def test_probe_chapters_no_chapter_list_reads_empty_starts():
    """A file with no chapters omits the key entirely: readable, zero starts —
    the suggestion declines on the count, the probe never guesses."""
    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=0,
                               stdout=json.dumps({"format": {"duration": "9410.000000"}}),
                               stderr="")

    assert probe_chapters(Path("/x/Show.mkv"), run=run) == ([], 9410.0)


def test_probe_chapters_dead_ffprobe_returns_none():
    def run(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    assert probe_chapters(Path("/x/Show.mkv"), run=run) is None


# The suggestion math: nearest start, window flag, first/last pinned
def test_suggest_downton_shape_unique_in_window():
    """Sums 3240/6480 land nearest chapters 8 (3276, 36s) and 15 (6552, 72s):
    unique, in-window, E1 pinned to chapter 1, E3 running to chapter 21."""
    spans, flagged = suggest_boundaries(DOWNTON_STARTS, DOWNTON_DURATION,
                                        DOWNTON_RUNTIMES, 1, 3)
    assert spans == [(1, 7), (8, 14), (15, 21)]
    assert flagged == set()


def test_suggest_flags_outside_window_boundary():
    """Chapter 15 drifts to 6900: the nearest start to 6480 becomes chapter 14
    (396s off) — episode 3 flagged for the note, never silently accepted."""
    starts = list(DOWNTON_STARTS)
    starts[14] = 6900.0
    spans, flagged = suggest_boundaries(starts, DOWNTON_DURATION,
                                        DOWNTON_RUNTIMES, 1, 3)
    assert spans == [(1, 7), (8, 13), (14, 21)]
    assert flagged == {3}


def test_suggest_window_edge_is_inside():
    """120.0s off sits exactly on the window's edge: inside, not flagged."""
    starts = [0.0, 1020.0, 2000.0, 3000.0]
    spans, flagged = suggest_boundaries(starts, 3000.0,
                                        {1: 900, 2: 900, 3: 900}, 1, 3)
    # target1=900 -> chapter 2 (120s, inside); target2=1800 -> chapter 3
    # (200s, outside): episode 3 flags.
    assert spans == [(1, 1), (2, 2), (3, 4)]
    assert flagged == {3}


def test_suggest_nearest_tie_picks_earliest_chapter():
    """800 and 1000 both sit 100s from target 900: the earliest chapter wins."""
    starts = [0.0, 800.0, 1000.0, 1800.0]
    spans, flagged = suggest_boundaries(starts, 1800.0,
                                        {1: 900, 2: 900, 3: 900}, 1, 3)
    assert spans == [(1, 1), (2, 3), (4, 4)]
    assert flagged == set()


# Declines: no runtime, fewer chapters than episodes, unreadable duration
def test_suggest_declines_fewer_chapters():
    spans, reason = suggest_boundaries([0.0, 468.0], 9410.0,
                                       DOWNTON_RUNTIMES, 1, 3)
    assert spans is None
    assert reason is not None and "chapters" in reason and "episodes" in reason
    assert "2" in reason and "3" in reason


def test_suggest_declines_zero_or_missing_runtime():
    spans, reason = suggest_boundaries(DOWNTON_STARTS, DOWNTON_DURATION,
                                       {1: 3240, 2: 3240, 3: 0}, 1, 3)
    assert spans is None and reason is not None and "E03" in reason

    spans, reason = suggest_boundaries(DOWNTON_STARTS, DOWNTON_DURATION,
                                       {1: 3240}, 1, 3)
    assert spans is None and reason is not None and "E02" in reason


def test_suggest_declines_unreadable_duration():
    spans, reason = suggest_boundaries(DOWNTON_STARTS, 0.0,
                                       DOWNTON_RUNTIMES, 1, 3)
    assert spans is None and reason is not None


# The seam slice 03 walks: probe the combined file, suggest from its values
def test_probe_feeds_suggestion():
    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=0,
                               stdout=ffprobe_json(DOWNTON_STARTS, DOWNTON_DURATION),
                               stderr="")

    starts, duration = probe_chapters(Path("/x/Show_S01E01-E03.mkv"), run=run)
    spans, flagged = suggest_boundaries(starts, duration, DOWNTON_RUNTIMES, 1, 3)
    assert spans == [(1, 7), (8, 14), (15, 21)]
    assert flagged == set()


# --- The boundary table (the selection component's fourth consumer) ----------

SUGGESTED = [(1, 7), (8, 14), (15, 21)]


def _pilot(keys, check, start_chs=None, size=(80, 24)):
    """Run the boundary table headless, press `keys`, and run `check(app)`
    inside the live context — the widget tree is pruned once the app exits."""
    app = BoundaryTable("Show_S01E01-E03.mkv", 1, 1, 3,
                        list(start_chs or [1, 8, 15]), DOWNTON_STARTS,
                        DOWNTON_DURATION, DOWNTON_RUNTIMES)

    async def go():
        async with app.run_test(size=size) as pilot:
            if keys:
                await pilot.press(*keys)
                await pilot.pause()
            check(app)
    asyncio.run(go())


def test_boundary_table_prefill_renders_graded_rows():
    """Graded prefill: one row per episode, the suggested start chapter as
    the action cell, the span duration in the runtime cell, no notes."""
    def check(app):
        assert [r.number for r in app.titles] == [1, 2, 3]
        assert app._cells(app.titles[0])[2].plain == "1"
        assert app._cells(app.titles[1])[2].plain == "8"
        assert app._cells(app.titles[1])[1].plain == "0:54:36"
        assert app._cells(app.titles[2])[3].plain == ""
        assert app._blocks == {}
    _pilot([], check)


def test_boundary_table_correction_moves_start_and_reregrades():
    """Typing `14` at the E02 row and Enter moves its start to chapter 14;
    the layout re-grades, a second Enter accepts the corrected spans."""
    def check(app):
        assert app.return_value == [(1, 13), (14, 14), (15, 21)]
    _pilot(["j", "1", "4", "enter", "enter"], check)


def test_boundary_table_correction_reregrades_flags_after_fix():
    """Moving E03's start past the window flags it: a yellow warning names
    the episode and Enter still accepts (the ratio-audit rule)."""
    def check(app):
        assert app.return_value == [(1, 7), (8, 19), (20, 21)]
        assert "outside ±120s" in app._cells(app.titles[2])[3].plain
        assert "E03" in app._status_text().plain
        assert "boundary suggestion outside ±120s" in app._status_text().plain
    _pilot(["j", "j", "2", "0", "enter", "enter"], check)


def test_boundary_table_pinned_first_episode_blocks():
    """E-lo is pinned to chapter 1: a typed chapter at its row is rejected,
    the row is named, and nothing changes."""
    def check(app):
        assert app.return_value is None
        assert app.start_chs == [1, 8, 15]
        assert "pinned" in app.message
    _pilot(["5", "enter"], check)


def test_boundary_table_missing_chapter_blocks_and_names_row():
    def check(app):
        assert app.return_value is None
        assert app.start_chs == [1, 8, 15]
        assert "no chapter 99" in app.message
        assert "E02" in app.message
    _pilot(["j", "9", "9", "enter"], check)


def test_boundary_table_nonincreasing_and_starving_rows_are_named():
    """A start behind the previous episode's, and one that starves the next
    episode, are both refused — the row named, nothing changed."""
    def check(app):
        assert app.return_value is None
        assert app.start_chs == [1, 8, 15]
        assert "✗ E02" in app.message and "must increase" in app.message
    _pilot(["j", "1", "enter"], check)

    def check_starve(app):
        assert app.start_chs == [1, 8, 15]
        assert "✗ E02: chapter 15 leaves E02 no chapters" in app.message
    _pilot(["j", "1", "5", "enter"], check_starve)


def test_boundary_table_blocked_layout_renders_red_and_refuses():
    """A standing layout that breaks the rules (here: prefill duplicates
    chapter 8) renders the row red, names it, and refuses Enter; the fix
    clears the block and a further Enter accepts."""
    def check_blocked(app):
        assert app.return_value is None
        assert 3 in app._blocks
        assert "must increase" in app._blocks[3]
        assert any("red" in sp.style
                   for c in app._cells(app.titles[2]) for sp in c.spans)
        assert "E03" in app._status_text().plain
    _pilot(["enter"], check_blocked, start_chs=[1, 8, 8])

    def check_fixed(app):
        assert app.return_value == [(1, 7), (8, 14), (15, 21)]
    _pilot(["j", "j", "1", "5", "enter", "enter"], check_fixed,
           start_chs=[1, 8, 8])


def test_boundary_table_rejected_edit_then_accept():
    """A rejected typed value costs a message; the untouched layout accepts
    on the next Enter."""
    def check(app):
        assert app.return_value == SUGGESTED
    _pilot(["j", "1", "enter", "backspace", "enter", "enter"], check)


def test_boundary_table_accept_returns_suggested_spans():
    def check(app):
        assert app.return_value == SUGGESTED
    _pilot(["enter"], check)


def test_boundary_table_q_declines():
    def check(app):
        assert app.return_value == QUIT
    _pilot(["q"], check)


def test_boundary_table_arrows_move_the_cursor():
    def check(app):
        assert app.query_one("DataTable").cursor_row == 2
    _pilot(["j", "j"], check)

    def check_up(app):
        assert app.query_one("DataTable").cursor_row == 0
    _pilot(["j", "k"], check_up)


def test_boundary_note_names_flagged_episodes():
    assert boundary_note(set()) is None
    note = boundary_note({2, 3})
    assert note is not None and "E02" in note and "E03" in note
    assert "120" in note


def test_boundary_flags_reregrade_a_fixed_layout():
    """The flag grading runs over whatever layout stands now: the suggested
    Downton spans sit inside the window; dragging E03's start to chapter 20
    (8892s, 2412s past its 6480s target) flags it."""
    assert boundary_flags([1, 8, 15], DOWNTON_STARTS, DOWNTON_RUNTIMES, 1, 3) == set()
    assert boundary_flags([1, 8, 20], DOWNTON_STARTS, DOWNTON_RUNTIMES, 1, 3) == {3}


def test_parse_grammar_gone_and_spans_from_starts_shapes():
    """The `e=ch` grammar is gone; the span shape it fed survives."""
    assert not hasattr(chapter_split, "parse_fix")
    assert not hasattr(chapter_split, "boundary_loop")
    assert spans_from_starts([1, 8, 15], 21) == [(1, 7), (8, 14), (15, 21)]
    assert spans_from_starts([1, 5], 6) == [(1, 4), (5, 6)]


# The split phase: probe → table → never-clobber → cut → delete


def _confirm(monkeypatch, outcome=SUGGESTED):
    """Script the boundary table seam: `outcome` is the confirmed spans, QUIT
    (declined) or a callable/exception raised at the table. Also fakes a
    terminal (pytest's stdin is not one), so the phase reaches the table."""
    monkeypatch.setattr(chapter_split.sys.stdin, "isatty", lambda: True)
    calls = []

    def table(display, season, lo, hi, spans, starts, duration, runtimes):
        calls.append(display)
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome()
        return outcome

    monkeypatch.setattr(chapter_split, "run_boundary_table", table)
    return calls


def _split_paths(tmp_path):
    """A Show (2000) combined file plus the three per-episode targets."""
    combined = tmp_path / "Show (2000)_S01E01-E03.mkv"
    combined.touch()
    targets = {e: tmp_path / f"Show (2000)_S01E{e:02d}.mkv" for e in (1, 2, 3)}
    return combined, targets


def _split_run(starts, duration, fail_on=None, interrupt_on=None, touch=True):
    """Fake run for the whole phase: ffprobe serves the chapter JSON; ffmpeg
    touches its target, or fails, or raises KeyboardInterrupt mid-cut when the
    target names the given episode code."""
    def run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0,
                                   stdout=ffprobe_json(starts, duration),
                                   stderr="")
        target = Path(cmd[-1])
        if fail_on and fail_on in target.name:
            return SimpleNamespace(returncode=1, stdout="", stderr="ffmpeg died")
        if interrupt_on and interrupt_on in target.name:
            raise KeyboardInterrupt()
        if touch:
            target.touch()
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


def _split_notes(log):
    return [c.args[1] for c in log.note.call_args_list if c.args[0] == "split"]


def test_split_combined_accept_cuts_all_pieces_and_deletes_combined(
        tmp_path, monkeypatch):
    """Accept: one ffmpeg per episode — input seek to the chapter start, `-t`
    over the span, all streams, stream copy only — every piece present, the
    accepted table echoed, the combined file deleted, one success note."""
    calls = _confirm(monkeypatch)
    combined, targets = _split_paths(tmp_path)
    log = Mock()
    cmds = []

    def run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0,
                                   stdout=ffprobe_json(DOWNTON_STARTS,
                                                       DOWNTON_DURATION),
                                   stderr="")
        cmds.append(cmd)
        Path(cmd[-1]).touch()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log, run=run)

    assert all(t.exists() for t in targets.values())
    assert not combined.exists()
    assert _split_notes(log) == ["3 episodes cut — combined deleted"]
    assert len(cmds) == 3
    assert cmds[0] == ["ffmpeg", "-v", "error", "-ss", "0.000",
                       "-i", str(combined), "-t", "3276.000",
                       "-map", "0", "-c", "copy", str(targets[1])]
    assert cmds[1][4] == "3276.000" and cmds[1][8] == "3276.000"
    assert cmds[1][-1] == str(targets[2])
    assert cmds[2][4] == "6552.000" and cmds[2][8] == "2858.000"
    assert cmds[2][-1] == str(targets[3])
    assert calls == [combined.name]      # the table ran once over the file's name


def test_split_combined_accepted_table_echoed_as_plain_lines(
        tmp_path, monkeypatch):
    """The accepted boundary table echoes as plain lines into scrollback
    before the cut — the print_accepted pattern (pipe-safe by construction)."""
    real = chapter_split.ui.console
    con = Console(record=True, width=120, file=StringIO())
    chapter_split.ui.console = con
    try:
        print_accepted_boundaries("Show_S01E01-E03.mkv", 1, 1, SUGGESTED,
                                  DOWNTON_STARTS, DOWNTON_DURATION,
                                  DOWNTON_RUNTIMES)
        text = con.export_text()
    finally:
        chapter_split.ui.console = real
    assert "S01E01" in text and "S01E03" in text
    assert "8" in text and "0:54:36" in text

    # Prints normally under a suspended seam run view (ADR 0012: interactive
    # surfaces never print under the Live region).
    view = chapter_split.ui.RunView(tty=True, live=False)
    chapter_split.ui.set_view(view)
    try:
        print_accepted_boundaries("Show_S01E01-E03.mkv", 1, 1, SUGGESTED,
                                  DOWNTON_STARTS, DOWNTON_DURATION,
                                  DOWNTON_RUNTIMES)
    finally:
        chapter_split.ui.clear_view()


def test_split_combined_unreadable_probe_declines_without_table(
        tmp_path, monkeypatch):
    calls = _confirm(monkeypatch)
    combined, targets = _split_paths(tmp_path)
    log = Mock()

    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="no such file")

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log, run=run)

    assert combined.exists()
    assert calls == []                   # no table, no interaction
    notes = _split_notes(log)
    assert len(notes) == 1 and "probe unreadable" in notes[0]


def test_split_combined_missing_runtime_declines(tmp_path, monkeypatch):
    calls = _confirm(monkeypatch)
    combined, targets = _split_paths(tmp_path)
    log = Mock()
    runtimes = {1: 3240, 2: 3240, 3: 0}

    split_combined(combined, targets, 1, 1, 3, runtimes, log,
                   run=_split_run(DOWNTON_STARTS, DOWNTON_DURATION))

    assert combined.exists()
    assert calls == []
    notes = _split_notes(log)
    assert len(notes) == 1 and "no TMDB runtime for E03" in notes[0]


def test_split_combined_fewer_chapters_declines(tmp_path, monkeypatch):
    calls = _confirm(monkeypatch)
    combined, targets = _split_paths(tmp_path)
    log = Mock()

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log,
                   run=_split_run([0.0, 468.0], 9410.0))

    assert combined.exists()
    assert calls == []
    notes = _split_notes(log)
    assert len(notes) == 1 and "2 chapters but 3 episodes" in notes[0]


def test_split_combined_q_declines_and_keeps_combined(tmp_path, monkeypatch):
    """A declined table: the combined file stays, loud and named."""
    calls = _confirm(monkeypatch, QUIT)
    combined, targets = _split_paths(tmp_path)
    log = Mock()

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log,
                   run=_split_run(DOWNTON_STARTS, DOWNTON_DURATION))

    assert combined.exists()
    assert not any(t.exists() for t in targets.values())
    notes = _split_notes(log)
    assert len(notes) == 1
    assert "skipped" in notes[0] and "declined" in notes[0]
    assert calls == [combined.name]


def test_split_combined_no_terminal_declines_and_keeps_combined(
        tmp_path, monkeypatch):
    """A headless completion never hangs: without a terminal the split
    declines like a declined table — combined kept, one named note."""
    calls = _confirm(monkeypatch)
    monkeypatch.setattr(chapter_split.sys.stdin, "isatty", lambda: False)
    combined, targets = _split_paths(tmp_path)
    log = Mock()

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log,
                   run=_split_run(DOWNTON_STARTS, DOWNTON_DURATION))

    assert combined.exists()
    assert calls == []                   # no table was ever launched
    notes = _split_notes(log)
    assert notes == ["skipped: no terminal — combined kept"]


def test_split_combined_interrupt_at_table_keeps_combined_and_continues(
        tmp_path, monkeypatch):
    """Ctrl+C at the boundary table: Textual restores the terminal, the
    phase's contained handler fires — nothing cut, combined kept, one
    interrupted note, and the phase returns so the run goes on."""
    _confirm(monkeypatch, KeyboardInterrupt())
    combined, targets = _split_paths(tmp_path)
    log = Mock()

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log,
                   run=_split_run(DOWNTON_STARTS, DOWNTON_DURATION))

    assert combined.exists()
    notes = _split_notes(log)
    assert len(notes) == 1 and "interrupted" in notes[0]


def test_split_combined_interrupt_midcut_sweeps_partials(tmp_path, monkeypatch):
    """Ctrl+C during the second cut: the first episode's partial is swept,
    the combined file kept, one interrupted note."""
    _confirm(monkeypatch)
    combined, targets = _split_paths(tmp_path)
    log = Mock()

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log,
                   run=_split_run(DOWNTON_STARTS, DOWNTON_DURATION,
                                  interrupt_on="E02"))

    assert not targets[1].exists()       # cut, then swept
    assert not targets[2].exists()       # never landed
    assert not targets[3].exists()       # never attempted
    assert combined.exists()
    notes = _split_notes(log)
    assert len(notes) == 1 and "interrupted" in notes[0]


def test_split_combined_never_clobber_aborts_before_first_cut(
        tmp_path, monkeypatch):
    """Any existing per-episode target aborts the whole split before the
    first ffmpeg: staging is never clobbered, combined kept."""
    calls = _confirm(monkeypatch)
    combined, targets = _split_paths(tmp_path)
    targets[2].touch()                   # an episode an earlier disc staged
    log = Mock()
    ffmpeg = []

    def run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0,
                                   stdout=ffprobe_json(DOWNTON_STARTS,
                                                       DOWNTON_DURATION),
                                   stderr="")
        ffmpeg.append(cmd)
        Path(cmd[-1]).touch()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log, run=run)

    assert ffmpeg == []                  # not one cut
    assert not targets[1].exists()
    assert combined.exists()
    notes = _split_notes(log)
    assert len(notes) == 1
    assert "S01E02.mkv already exists" in notes[0]
    assert calls == [combined.name]      # confirmed first, then refused


def test_split_combined_ffmpeg_failure_sweeps_partials(tmp_path, monkeypatch):
    """A failing cut sweeps the pieces already cut, never touches the rest,
    keeps the combined file, and notes the failure."""
    _confirm(monkeypatch)
    combined, targets = _split_paths(tmp_path)
    log = Mock()

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log,
                   run=_split_run(DOWNTON_STARTS, DOWNTON_DURATION,
                                  fail_on="E02"))

    assert not targets[1].exists()       # cut, then swept
    assert not targets[2].exists()
    assert not targets[3].exists()       # never attempted
    assert combined.exists()
    notes = _split_notes(log)
    assert len(notes) == 1
    assert "ffmpeg failed on S01E02" in notes[0]


def test_split_combined_piece_that_did_not_land_keeps_combined(
        tmp_path, monkeypatch):
    """A run that claims success without producing the file is no success:
    the pieces-present guard keeps the combined file and notes the failure."""
    _confirm(monkeypatch)
    combined, targets = _split_paths(tmp_path)
    log = Mock()

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log,
                   run=_split_run(DOWNTON_STARTS, DOWNTON_DURATION, touch=False))

    assert combined.exists()
    notes = _split_notes(log)
    assert len(notes) == 1 and "ffmpeg failed on S01E01" in notes[0]


def test_split_combined_missing_ffmpeg_sweeps_partials(tmp_path, monkeypatch):
    """A cut that cannot even launch (missing/unexecutable ffmpeg raises
    OSError from the run seam) is the same failure as a bad exit: partials
    swept, combined kept, the ffmpeg-failed note, and the phase returns."""
    _confirm(monkeypatch)
    combined, targets = _split_paths(tmp_path)
    log = Mock()

    def run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0,
                                   stdout=ffprobe_json(DOWNTON_STARTS,
                                                       DOWNTON_DURATION),
                                   stderr="")
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log, run=run)

    assert not targets[1].exists()       # cut, then swept
    assert not targets[2].exists()       # never landed
    assert not targets[3].exists()       # never attempted
    assert combined.exists()
    notes = _split_notes(log)
    assert len(notes) == 1
    assert "ffmpeg failed on S01E01" in notes[0]


def test_split_combined_interrupt_after_unlink_keeps_pieces(
        tmp_path, monkeypatch):
    """Ctrl+C landing between the combined file's unlink and the success note
    must not sweep: every piece is cut and the combined file is already gone,
    so the split completed — success note, pieces kept, phase returns."""
    _confirm(monkeypatch)
    combined, targets = _split_paths(tmp_path)
    log = Mock()
    real_unlink = Path.unlink

    def interrupt_after_unlink(self, missing_ok=False):
        real_unlink(self)
        if self == combined:
            raise KeyboardInterrupt()    # the signal arrives post-unlink

    monkeypatch.setattr(Path, "unlink", interrupt_after_unlink)

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log,
                   run=_split_run(DOWNTON_STARTS, DOWNTON_DURATION))

    assert all(t.exists() for t in targets.values())
    assert not combined.exists()
    notes = _split_notes(log)
    assert notes == ["3 episodes cut — combined deleted"]
