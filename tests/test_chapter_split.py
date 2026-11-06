"""Tests for chapter-split pieces: the one-call chapter probe and the
boundary suggestion (nearest start, ±120s window flag, declines); plus the
boundary table, prompt loop, cut, and phase orchestration (slice 03)."""

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from rich.console import Console

import skive.chapter_split as chapter_split
from skive.chapter_split import (
    boundary_loop, boundary_note, boundary_table, boundary_flags,
    parse_fix, probe_chapters, split_combined, spans_from_starts,
    suggest_boundaries,
)


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


# --- The boundary table, prompt loop, cut, and phase (slice 03) -------------


def _cells(table) -> list[tuple[str, str]]:
    """The (text, style) of every band-styled ratio cell a table renders."""
    con = Console(width=120, file=StringIO())
    return [(seg.text.strip(), str(seg.style)) for seg in con.render(table)
            if str(seg.style) in ("green", "yellow")]


def _stage(monkeypatch, answers):
    """Record console + scripted Prompt for the boundary UI."""
    con = Console(record=True, width=120)
    monkeypatch.setattr(chapter_split, "console", con)
    p = MagicMock()
    p.ask.side_effect = list(answers)
    monkeypatch.setattr(chapter_split, "Prompt", p)
    return con, p


def test_boundary_table_row_per_episode():
    """One row per episode: code, inclusive 1-based span, start timestamp,
    span duration (the last episode runs to the container duration)."""
    table = boundary_table(1, 1, [(1, 7), (8, 14), (15, 21)], DOWNTON_STARTS,
                           DOWNTON_DURATION, DOWNTON_RUNTIMES)
    con = Console(record=True, width=120)
    con.print(table)
    text = con.export_text()
    assert "S01E01" in text and "S01E02" in text and "S01E03" in text
    assert "1-7" in text and "8-14" in text and "15-21" in text
    assert "0:00:00" in text
    assert "0:54:36" in text          # span 3276s at chapter 8
    assert "1:49:12" in text          # chapter 15 sits at 6552s
    assert "0:47:38" in text          # last span: 9410 - 6552


def test_boundary_table_ratio_cells_styled_by_band():
    """The ratio cell grades span vs that episode's own TMDB runtime in the
    audit band: E01/E02 sit green (1.01), the PAL-short tail reads yellow."""
    table = boundary_table(1, 1, [(1, 7), (8, 14), (15, 21)], DOWNTON_STARTS,
                           DOWNTON_DURATION, DOWNTON_RUNTIMES)
    con = Console(record=True, width=120)
    con.print(table)
    text = con.export_text()      # note: exporting clears the record buffer
    assert "1.01" in text and "0.88" in text
    assert _cells(table) == [("1.01", "green"), ("1.01", "green"),
                             ("0.88", "yellow")]


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


def test_parse_fix_and_spans_from_starts_shapes():
    assert parse_fix("2=14") == (2, 14)
    assert parse_fix(" 2 = 14 ") == (2, 14)
    assert parse_fix("2") is None and parse_fix("e") is None
    assert parse_fix("1-2") is None
    assert spans_from_starts([1, 8, 15], 21) == [(1, 7), (8, 14), (15, 21)]
    assert spans_from_starts([1, 5], 6) == [(1, 4), (5, 6)]


# The boundary prompt loop
SUGGESTED = [(1, 7), (8, 14), (15, 21)]


def test_boundary_loop_enter_confirms_suggested_spans(monkeypatch):
    con, _p = _stage(monkeypatch, [""])
    spans = boundary_loop(1, 1, 3, SUGGESTED, DOWNTON_STARTS,
                          DOWNTON_DURATION, DOWNTON_RUNTIMES)
    assert spans == SUGGESTED
    assert "split?" in con.export_text()


def test_boundary_loop_q_declines(monkeypatch):
    _stage(monkeypatch, ["q"])
    assert boundary_loop(1, 1, 3, SUGGESTED, DOWNTON_STARTS,
                         DOWNTON_DURATION, DOWNTON_RUNTIMES) is None


def test_boundary_loop_fix_moves_start_and_rerenders(monkeypatch):
    """`2=14` moves E02's start to chapter 14 and re-renders the table."""
    con, _p = _stage(monkeypatch, ["2=14", ""])
    spans = boundary_loop(1, 1, 3, SUGGESTED, DOWNTON_STARTS,
                          DOWNTON_DURATION, DOWNTON_RUNTIMES)
    assert spans == [(1, 13), (14, 14), (15, 21)]
    text = con.export_text()
    assert text.count("split?") == 2        # fix, then re-render, then Enter
    assert text.count("14-14") == 1         # the fixed span on the re-render


def test_boundary_loop_reregrades_flags_after_fix(monkeypatch):
    """The note re-grades above each re-render: the suggested layout sits
    inside the window, the moved E03 boundary flags, the note names it."""
    con, _p = _stage(monkeypatch, ["3=20", ""])
    spans = boundary_loop(1, 1, 3, SUGGESTED, DOWNTON_STARTS,
                          DOWNTON_DURATION, DOWNTON_RUNTIMES)
    assert spans == [(1, 7), (8, 19), (20, 21)]
    text = con.export_text()
    first_prompt = text.index("split?")
    assert "boundary suggestion outside" not in text[:first_prompt]
    note_line = next(ln for ln in text[first_prompt:].splitlines()
                     if "boundary suggestion outside" in ln)
    assert "E03" in note_line


def test_boundary_loop_rejects_first_episode_row(monkeypatch):
    con, _p = _stage(monkeypatch, ["1=5", ""])
    spans = boundary_loop(1, 1, 3, SUGGESTED, DOWNTON_STARTS,
                          DOWNTON_DURATION, DOWNTON_RUNTIMES)
    assert spans == SUGGESTED
    assert "pinned" in con.export_text()


def test_boundary_loop_rejects_episode_outside_range(monkeypatch):
    con, _p = _stage(monkeypatch, ["4=5", ""])
    spans = boundary_loop(1, 1, 3, SUGGESTED, DOWNTON_STARTS,
                          DOWNTON_DURATION, DOWNTON_RUNTIMES)
    assert spans == SUGGESTED
    assert "outside 1-3" in con.export_text()


def test_boundary_loop_rejects_chapter_past_file_end(monkeypatch):
    con, _p = _stage(monkeypatch, ["2=99", ""])
    spans = boundary_loop(1, 1, 3, SUGGESTED, DOWNTON_STARTS,
                          DOWNTON_DURATION, DOWNTON_RUNTIMES)
    assert spans == SUGGESTED
    assert "no chapter 99" in con.export_text()


def test_boundary_loop_rejects_starving_boundaries(monkeypatch):
    """`3=1` backs E03 onto E02's start; `2=15` drops E02 onto E03's — both
    leave an episode chapterless, both print why and change nothing."""
    con, _p = _stage(monkeypatch, ["3=1", "2=15", ""])
    spans = boundary_loop(1, 1, 3, SUGGESTED, DOWNTON_STARTS,
                          DOWNTON_DURATION, DOWNTON_RUNTIMES)
    assert spans == SUGGESTED
    text = con.export_text()
    assert "must increase" in text
    assert "leaves E02 no chapters" in text


def test_boundary_loop_rejects_unreadable_row(monkeypatch):
    con, _p = _stage(monkeypatch, ["move it", ""])
    spans = boundary_loop(1, 1, 3, SUGGESTED, DOWNTON_STARTS,
                          DOWNTON_DURATION, DOWNTON_RUNTIMES)
    assert spans == SUGGESTED
    assert "cannot read fix row" in con.export_text()


# The split phase: probe → table+prompt → never-clobber → cut → delete


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


def test_split_combined_enter_cuts_all_pieces_and_deletes_combined(
        tmp_path, monkeypatch):
    """Enter: one ffmpeg per episode — input seek to the chapter start, `-t`
    over the span, all streams, stream copy only — every piece present, the
    combined file deleted, one success note with the piece count."""
    _stage(monkeypatch, [""])
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


def test_split_combined_unreadable_probe_declines_without_table(
        tmp_path, monkeypatch):
    _stage(monkeypatch, [])
    combined, targets = _split_paths(tmp_path)
    log = Mock()

    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="no such file")

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log, run=run)

    assert combined.exists()
    chapter_split.Prompt.ask.assert_not_called()   # no table, no prompt
    assert "split?" not in chapter_split.console.export_text()
    notes = _split_notes(log)
    assert len(notes) == 1 and "probe unreadable" in notes[0]


def test_split_combined_missing_runtime_declines(tmp_path, monkeypatch):
    _stage(monkeypatch, [])
    combined, targets = _split_paths(tmp_path)
    log = Mock()
    runtimes = {1: 3240, 2: 3240, 3: 0}

    split_combined(combined, targets, 1, 1, 3, runtimes, log,
                   run=_split_run(DOWNTON_STARTS, DOWNTON_DURATION))

    assert combined.exists()
    chapter_split.Prompt.ask.assert_not_called()
    notes = _split_notes(log)
    assert len(notes) == 1 and "no TMDB runtime for E03" in notes[0]


def test_split_combined_fewer_chapters_declines(tmp_path, monkeypatch):
    _stage(monkeypatch, [])
    combined, targets = _split_paths(tmp_path)
    log = Mock()

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log,
                   run=_split_run([0.0, 468.0], 9410.0))

    assert combined.exists()
    chapter_split.Prompt.ask.assert_not_called()
    notes = _split_notes(log)
    assert len(notes) == 1 and "2 chapters but 3 episodes" in notes[0]


def test_split_combined_q_declines_and_keeps_combined(tmp_path, monkeypatch):
    _stage(monkeypatch, ["q"])
    combined, targets = _split_paths(tmp_path)
    log = Mock()

    split_combined(combined, targets, 1, 1, 3, DOWNTON_RUNTIMES, log,
                   run=_split_run(DOWNTON_STARTS, DOWNTON_DURATION))

    assert combined.exists()
    assert not any(t.exists() for t in targets.values())
    notes = _split_notes(log)
    assert len(notes) == 1
    assert "skipped" in notes[0] and "declined" in notes[0]


def test_split_combined_interrupt_at_prompt_keeps_combined_and_continues(
        tmp_path, monkeypatch):
    """Ctrl+C at the boundary table: contained — nothing cut, combined kept,
    one interrupted note, and the phase returns so the run goes on."""
    _stage(monkeypatch, [])
    chapter_split.Prompt.ask.side_effect = KeyboardInterrupt
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
    _stage(monkeypatch, [""])
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
    _stage(monkeypatch, [""])
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


def test_split_combined_ffmpeg_failure_sweeps_partials(tmp_path, monkeypatch):
    """A failing cut sweeps the pieces already cut, never touches the rest,
    keeps the combined file, and notes the failure."""
    _stage(monkeypatch, [""])
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
    _stage(monkeypatch, [""])
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
    _stage(monkeypatch, [""])
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
    _stage(monkeypatch, [""])
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


def test_boundary_loop_prints_under_a_seam_view(monkeypatch):
    """The boundary table and fix prompt print normally while a seam run
    view is suspended (ADR 0012: interactive surfaces never print under the
    Live region), and the loop still answers."""
    from skive import ui

    con, _p = _stage(monkeypatch, [""])
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        spans = boundary_loop(1, 1, 3, SUGGESTED, DOWNTON_STARTS,
                              DOWNTON_DURATION, DOWNTON_RUNTIMES)
    finally:
        ui.clear_view()
    assert spans == SUGGESTED
    text = con.export_text()
    assert "split?" in text
    assert "S01E01" in text
