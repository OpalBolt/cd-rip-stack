"""Chapter split math: the chapter probe (chapter starts plus container
duration from one ffprobe call on the combined file) and the boundary
suggestion that grades cumulative chapter starts against summed TMDB
runtimes."""

import json
import subprocess
from pathlib import Path


BOUNDARY_WINDOW = 120
"""Seconds a suggested boundary may sit from its summed-runtime target and
still read unflagged; chapters ~7.8 min apart cannot overlap two windows."""


def probe_chapters(path: Path, run=subprocess.run) -> tuple[list[float], float] | None:
    """The file's chapter starts (seconds) plus container duration, from one
    ffprobe call; None when any of it is unreadable.

    One call serves both values (`format=duration:chapter=start_time`, the
    house `-v error` flag shape). A file with no chapters reads as an empty
    start list — readable, so the suggestion declines on the count; the
    probe never guesses. Bad exit, junk output, a missing duration, or a
    chapter whose start won't parse all return None: the caller skips the
    split with a note.

    Args:
        path: The media file to probe
        run: subprocess.run callable for monkeypatching

    Returns:
        (chapter_starts, duration_s) in seconds, or None when unreadable
    """
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "format=duration:chapter=start_time", "-of", "json", str(path)]
    try:
        result = run(cmd, capture_output=True, text=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        doc = json.loads(result.stdout)
        duration = float(doc["format"]["duration"])
        starts = [float(ch["start_time"]) for ch in doc.get("chapters", [])]
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return (starts, duration)


def suggest_boundaries(chapter_starts: list[float], duration_s: float,
                       ep_runtimes: dict[int, int], lo: int, hi: int,
                       ) -> tuple[list[tuple[int, int]] | None, str | None]:
    """The suggested boundary layout — a chapter span per episode plus the
    doubtful-boundary flags — or (None, decline reason) with no layout.

    Each interior boundary's target is the summed TMDB runtimes of the
    episodes before it; its suggested chapter is the start nearest that
    target (ties to the earliest chapter). E-lo pins to chapter 1 and the
    last episode runs to the file end, so only interior boundaries are ever
    in question. A boundary whose nearest start sits outside
    ±BOUNDARY_WINDOW seconds of its target flags the episode whose start it
    is — the note above the table names it; it is never silently accepted.
    Durations suggest, the human assigns (ADR 0001).

    Args:
        chapter_starts: Chapter start times in seconds, disc order
            (1-based chapter = index + 1)
        duration_s: Container duration in seconds
        ep_runtimes: episode_number -> TMDB runtime seconds (0 = unknown)
        lo: First episode number of the range
        hi: Last episode number of the range

    Returns:
        ((chapter spans in lo..hi order, 1-based inclusive), flagged episode
        numbers) — or (None, reason) when any episode in the range lacks a
        runtime, the file has fewer chapters than episodes, or the duration
        is unreadable
    """
    missing = next((e for e in range(lo, hi + 1) if ep_runtimes.get(e, 0) <= 0), None)
    if missing is not None:
        return (None, f"no TMDB runtime for E{missing:02d}")
    if len(chapter_starts) < hi - lo + 1:
        return (None, f"{len(chapter_starts)} chapters but {hi - lo + 1} episodes")
    if duration_s <= 0:
        return (None, "container duration unreadable")

    start_chs = [1]  # each episode's start chapter; E-lo pinned to 1
    flagged: set[int] = set()
    for e in range(lo, hi):
        target = sum(ep_runtimes[x] for x in range(lo, e + 1))
        ch = min(range(len(chapter_starts)),
                 key=lambda k: abs(chapter_starts[k] - target)) + 1
        if abs(chapter_starts[ch - 1] - target) > BOUNDARY_WINDOW:
            flagged.add(e + 1)
        start_chs.append(ch)

    spans: list[tuple[int, int]] = []
    for i, start_ch in enumerate(start_chs):
        end = start_chs[i + 1] - 1 if i + 1 < len(start_chs) else len(chapter_starts)
        spans.append((start_ch, end))
    return (spans, flagged)
