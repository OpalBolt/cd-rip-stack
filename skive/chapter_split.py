"""Chapter split (ADR 0009) for play-all-only discs: the chapter probe
(chapter starts plus container duration from one ffprobe call on the combined
file), the boundary suggestion that grades cumulative chapter starts against
summed TMDB runtimes, the boundary table and its confirm prompt loop, the
lossless per-episode cut, and the phase orchestration that joins them —
declines, interrupts, and failures all keep the combined file."""

import json
import re
import subprocess
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from skive.tv_match import audit_cell

console = Console()


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


# --- The boundary table, prompt loop, cut, and phase (this slice) ----------


def _hms(secs: float) -> str:
    """Seconds as H:MM:SS — the confirm table's duration shape."""
    total = int(round(secs))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def spans_from_starts(start_chs: list[int], n_chapters: int) -> list[tuple[int, int]]:
    """Inclusive 1-based chapter spans from each episode's start chapter.

    The next episode's start ends a span; the last episode runs to the file's
    last chapter, so only interior boundaries are ever in question.

    Args:
        start_chs: Each episode's start chapter, 1-based, in lo..hi order
        n_chapters: The file's chapter count

    Returns:
        One (start chapter, end chapter) span per episode
    """
    return [(start_chs[i], start_chs[i + 1] - 1 if i + 1 < len(start_chs)
             else n_chapters) for i in range(len(start_chs))]


def boundary_flags(start_chs: list[int], chapter_starts: list[float],
                   ep_runtimes: dict[int, int], lo: int, hi: int) -> set[int]:
    """The episodes whose start boundary grades outside the window.

    The same grading suggest_boundaries applies to its own suggestions, run
    over whatever layout stands now — a hand-fixed boundary is re-graded and
    re-flagged when it drifts past ±BOUNDARY_WINDOW of its summed-runtime
    target. A doubtful row never looks confirmed.

    Args:
        start_chs: Each episode's start chapter, 1-based, in lo..hi order
        chapter_starts: Chapter start times in seconds, disc order
        ep_runtimes: episode_number -> TMDB runtime seconds (0 = unknown)
        lo: First episode number of the range
        hi: Last episode number of the range

    Returns:
        Episode numbers whose start boundary sits outside the window
    """
    flagged: set[int] = set()
    for i in range(1, len(start_chs)):
        e = lo + i
        target = sum(ep_runtimes[x] for x in range(lo, e))
        if abs(chapter_starts[start_chs[i] - 1] - target) > BOUNDARY_WINDOW:
            flagged.add(e)
    return flagged


def boundary_note(flagged: set[int]) -> str | None:
    """The note line printed above the boundary table, naming the flagged
    episodes; None when every boundary sits inside the window.

    Args:
        flagged: Episode numbers whose start boundary is outside the window

    Returns:
        The one-line note, or None
    """
    if not flagged:
        return None
    eps = ", ".join(f"E{e:02d}" for e in sorted(flagged))
    return (f"boundary suggestion outside ±{BOUNDARY_WINDOW}s of the summed "
            f"runtimes: {eps} — check before splitting")


def boundary_table(season: int, lo: int, spans: list[tuple[int, int]],
                   chapter_starts: list[float], duration_s: float,
                   ep_runtimes: dict[int, int]) -> Table:
    """The boundary table: one row per episode — code, inclusive 1-based
    chapter span, start timestamp, span duration, and a ratio-audit cell
    grading the span against that episode's TMDB runtime in the band.

    Args:
        season: Season number the disc belongs to
        lo: First episode number of the range
        spans: One (start chapter, end chapter) span per episode, 1-based
        chapter_starts: Chapter start times in seconds, disc order
        duration_s: Container duration in seconds
        ep_runtimes: episode_number -> TMDB runtime seconds (0 = unknown)

    Returns:
        The rich Table, ready to print
    """
    table = Table()
    table.add_column("episode", style="cyan", width=8)
    table.add_column("chapters", style="white", width=9)
    table.add_column("start", style="white", width=9)
    table.add_column("duration", style="white", width=9)
    table.add_column("ratio", width=6)
    for i, (start_ch, end_ch) in enumerate(spans):
        e = lo + i
        start_s = chapter_starts[start_ch - 1]
        end_s = (chapter_starts[end_ch] if end_ch < len(chapter_starts)
                 else duration_s)
        span_s = end_s - start_s
        audit = audit_cell(int(round(span_s)), e, e, ep_runtimes)
        audit_str = Text(audit[0], style=audit[1]) if audit else ""
        table.add_row(f"S{season:02d}E{e:02d}", f"{start_ch}-{end_ch}",
                      _hms(start_s), _hms(span_s), audit_str)
    return table


def parse_fix(ans: str) -> tuple[int, int] | None:
    """Parse a boundary fix row `e=ch` — episode equals start chapter,
    chapters 1-based like VLC. None for anything else.

    Args:
        ans: Raw answer from the boundary prompt

    Returns:
        (episode, start chapter), or None when unparseable
    """
    m = re.fullmatch(r"(\d+)\s*=\s*(\d+)", ans.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def fix_rejection(fix: tuple[int, int], start_chs: list[int], n_chapters: int,
                  lo: int, hi: int) -> str | None:
    """Why a boundary fix row cannot be applied; None when it can.

    The episode must sit inside the range but not first (E-lo is pinned to
    chapter 1); the chapter must exist; and the boundaries must strictly
    increase so every episode keeps at least one chapter.

    Args:
        fix: (episode, start chapter) from parse_fix
        start_chs: Each episode's current start chapter, 1-based
        n_chapters: The file's chapter count
        lo: First episode number of the range
        hi: Last episode number of the range

    Returns:
        The rejection reason, or None when the row applies
    """
    e, ch = fix
    if e < lo or e > hi:
        return f"episode {e} is outside {lo}-{hi}"
    if e == lo:
        return f"E{lo:02d} is pinned to chapter 1 — only interior boundaries move"
    if not 1 <= ch <= n_chapters:
        return f"no chapter {ch} — the file has {n_chapters}"
    i = e - lo
    if ch <= start_chs[i - 1]:
        return (f"boundaries must increase: E{e - 1:02d} starts at chapter "
                f"{start_chs[i - 1]}, so E{e:02d} needs a later one")
    if i < len(start_chs) - 1 and ch >= start_chs[i + 1]:
        return (f"chapter {ch} leaves E{e:02d} no chapters — "
                f"E{e + 1:02d} starts at chapter {start_chs[i + 1]}")
    return None


def boundary_loop(season: int, lo: int, hi: int, spans: list[tuple[int, int]],
                  chapter_starts: list[float], duration_s: float,
                  ep_runtimes: dict[int, int]) -> list[tuple[int, int]] | None:
    """The boundary prompt loop: table, then Enter / `e=ch` / q.

    Enter returns the spans on screen for the cut; `e=ch` moves episode e's
    start to chapter ch — a rejected row prints why and changes nothing, an
    accepted one re-renders the table with the re-graded flags; q returns
    None (the combined file stays). Ctrl+C propagates to the phase.

    Args:
        season: Season number the disc belongs to
        lo: First episode number of the range
        hi: Last episode number of the range
        spans: The suggested (start chapter, end chapter) spans
        chapter_starts: Chapter start times in seconds, disc order
        duration_s: Container duration in seconds
        ep_runtimes: episode_number -> TMDB runtime seconds (0 = unknown)

    Returns:
        The confirmed spans, or None when declined
    """
    n = len(chapter_starts)
    start_chs = [s for s, _ in spans]
    while True:
        note = boundary_note(boundary_flags(start_chs, chapter_starts,
                                            ep_runtimes, lo, hi))
        if note:
            console.print(note)
        console.print(boundary_table(season, lo, spans_from_starts(start_chs, n),
                                     chapter_starts, duration_s, ep_runtimes))
        console.print("split? (Enter=cut at these boundaries, fixes like 2=14, "
                      "q=keep combined): ", end="")
        ans = (Prompt.ask("") or "").strip()
        if not ans:
            return spans_from_starts(start_chs, n)
        if ans.lower() == "q":
            return None
        fix = parse_fix(ans)
        if fix is None:
            console.print(f"cannot read fix row {ans!r} — want e=ch like 2=14")
            continue
        why = fix_rejection(fix, start_chs, n, lo, hi)
        if why:
            console.print(why)
            continue
        start_chs[fix[0] - lo] = fix[1]


def split_combined(combined: Path, targets: dict[int, Path], season: int,
                   lo: int, hi: int, ep_runtimes: dict[int, int], log,
                   run=subprocess.run) -> None:
    """The chapter split phase for one play-all-only rip (ADR 0009): probe
    the combined file, suggest boundaries, confirm them at the table, then
    cut per-episode files with input seek, span duration, all streams, stream
    copy only — and delete the combined file once every piece exists.

    One `split` run-log note per outcome: skipped with its reason (unreadable
    probe, missing runtime, fewer chapters than episodes, an existing target,
    a failed cut), declined (q), interrupted (Ctrl+C anywhere in the phase —
    contained here so the rip's generic interrupt exit never fires after the
    rip completed), or succeeded with the piece count. Every decline,
    interrupt, and failure keeps the combined file: the split never loses
    bytes, and the fallback is the pre-feature output shape.

    Args:
        combined: The renamed combined `_SNNENN-ENNN.mkv` file
        targets: episode_number -> per-episode target path (never clobbered;
            any existing target aborts the whole split before the first cut)
        season: Season number the disc belongs to
        lo: First episode number of the range
        hi: Last episode number of the range
        ep_runtimes: episode_number -> TMDB runtime seconds (0 = unknown)
        log: The run's RipLog
        run: subprocess.run callable for monkeypatching (probe and ffmpeg)
    """
    cut: list[Path] = []

    def sweep() -> None:
        """Delete this split's partial pieces — never anything pre-existing
        (the never-clobber check guarantees every target was absent)."""
        for piece in cut:
            piece.unlink(missing_ok=True)

    try:
        probed = probe_chapters(combined, run=run)
        if probed is None:
            log.note("split", "skipped: chapter probe unreadable — combined kept")
            return
        chapter_starts, duration_s = probed
        suggested = suggest_boundaries(chapter_starts, duration_s, ep_runtimes,
                                       lo, hi)
        if suggested[0] is None:
            log.note("split", f"skipped: {suggested[1]} — combined kept")
            return
        console.print(f"chapter split — {combined.name}")
        confirmed = boundary_loop(season, lo, hi, suggested[0], chapter_starts,
                                  duration_s, ep_runtimes)
        if confirmed is None:
            log.note("split",
                     "skipped: declined at the boundary table — combined kept")
            return
        existing = next((targets[e] for e in range(lo, hi + 1)
                         if targets[e].exists()), None)
        if existing is not None:
            log.note("split",
                     f"skipped: {existing.name} already exists — combined kept")
            return
        for i, (start_ch, end_ch) in enumerate(confirmed):
            e = lo + i
            target = targets[e]
            start_s = chapter_starts[start_ch - 1]
            end_s = (chapter_starts[end_ch] if end_ch < len(chapter_starts)
                     else duration_s)
            cmd = ["ffmpeg", "-v", "error", "-ss", f"{start_s:.3f}",
                   "-i", str(combined), "-t", f"{end_s - start_s:.3f}",
                   "-map", "0", "-c", "copy", str(target)]
            try:
                result = run(cmd, capture_output=True, text=True)
            except (OSError, subprocess.TimeoutExpired):
                result = None
            if result is None or result.returncode != 0 or not target.exists():
                sweep()
                log.note("split", f"skipped: ffmpeg failed on "
                         f"S{season:02d}E{e:02d} — partials deleted, "
                         "combined kept")
                return
            cut.append(target)
        combined.unlink()
        log.note("split", f"{hi - lo + 1} episodes cut — combined deleted")
    except KeyboardInterrupt:
        if len(cut) == hi - lo + 1 and not combined.exists():
            # The unlink already happened: the split completed, only its note
            # was pending — sweeping here would lose the only copy of the rip.
            log.note("split", f"{hi - lo + 1} episodes cut — combined deleted")
            return
        sweep()
        log.note("split", "interrupted — partials deleted, combined kept")
