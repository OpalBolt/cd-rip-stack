"""Tests for chapter-split pieces: the one-call chapter probe and the
boundary suggestion (nearest start, ±120s window flag, declines)."""

import json
from pathlib import Path
from types import SimpleNamespace

from skive.chapter_split import probe_chapters, suggest_boundaries


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
