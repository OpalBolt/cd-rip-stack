"""Chapter split (ADR 0009) for play-all-only discs: the chapter probe
(chapter starts plus container duration from one ffprobe call on the combined
file), the boundary suggestion that grades cumulative chapter starts against
summed TMDB runtimes, the boundary confirmation rebuilt on the selection
table (ADR 0020, the component's fourth consumer), the lossless per-episode
cut, and the phase orchestration that joins them — declines, interrupts, and
failures all keep the combined file."""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text
from textual.widgets import Static

from skive import ui
from skive.selection import INTERRUPT, QUIT, _Table, Binding

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


# --- The boundary table (the selection component's fourth consumer), the
# --- cut, and the phase (this slice) -----------------------------------------


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
    """The note line printed in the status line below the boundary table,
    naming the flagged episodes; None when every boundary sits inside the
    window.

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


def boundary_row_cells(season: int, lo: int, i: int, start_chs: list[int],
                       chapter_starts: list[float], duration_s: float,
                       ep_runtimes: dict[int, int], notes: str,
                       blocked: bool) -> tuple:
    """One boundary row's four cells: episode code, span duration (graded
    against that episode's TMDB runtime), the start chapter as the editable
    action, and the notes cell (a block reason or a window flag warning).
    A blocked row renders red whole, its reason named in the notes cell."""
    e = lo + i
    start_s = chapter_starts[start_chs[i] - 1]
    end_s = (chapter_starts[start_chs[i + 1] - 1] if i + 1 < len(start_chs)
             else duration_s)
    cells = (Text(f"S{season:02d}E{e:02d}", style="bold"),
             Text(_hms(end_s - start_s)),
             Text(str(start_chs[i]), style="bold green"),
             Text(notes))
    if blocked:
        for cell in cells:
            cell.stylize("red")
    return cells


def boundary_note_cell(flagged: set[int], e: int) -> str:
    """The row's warning when its start boundary grades outside the window."""
    if e in flagged:
        return f"outside ±{BOUNDARY_WINDOW}s of the summed runtimes"
    return ""


def print_accepted_boundaries(display: str, season: int, lo: int,
                              spans: list[tuple[int, int]],
                              chapter_starts: list[float], duration_s: float,
                              ep_runtimes: dict[int, int]) -> None:
    """Echo the accepted boundary table as plain lines into scrollback,
    before the cut — the print_accepted pattern the three main flows share.
    Built through ui.table, so it is pipe-safe and NO_COLOR-safe by
    construction."""
    start_chs = [s for s, _ in spans]
    hi = lo + len(spans) - 1
    flagged = boundary_flags(start_chs, chapter_starts, ep_runtimes, lo, hi)
    ui.console.print(f"\n{display}")
    table = ui.table(*BoundaryTable.COLUMNS)
    for i in range(len(spans)):
        e = lo + i
        table.add_row(*boundary_row_cells(season, lo, i, start_chs,
                                         chapter_starts, duration_s,
                                         ep_runtimes,
                                         boundary_note_cell(flagged, e),
                                         False))
    ui.console.print(table)


@dataclass
class _EpisodeRow:
    """One boundary-table row: the episode number the shared _Table keys
    rows and refreshes by."""
    number: int


class BoundaryTable(_Table):
    """The chapter-split boundary table (ADR 0009, the selection component's
    fourth consumer): one row per episode — episode, runtime, chapter start
    — prefilled from `suggest_boundaries`' graded layout.

    Keys: ↑/↓ or j/k move; digits type a chapter number at the cursor and
    Enter sets that row's start (fix_rejection's rules apply — a rejected
    value names the row and changes nothing, an accepted one re-grades);
    Enter accepts and returns the spans for the cut, refusing while any row
    is red; `q` declines (the combined file stays); Ctrl+C exits with
    INTERRUPT (Textual restores the terminal, the phase's handler sweeps).
    A doubtful boundary only warns, never blocks — the ratio-audit rule.
    """

    COLUMNS = ("episode", "runtime", "chapter start", "notes")

    BINDINGS = [
        Binding("backspace", "backspace", show=False),
        *[Binding(d, f"type('{d}')", show=False) for d in "0123456789"],
    ]

    HINT = ("↑/↓ j/k move · digits type a chapter start, Enter sets · "
            "Enter accept · q keep combined")

    def __init__(self, display: str, season: int, lo: int, hi: int,
                 start_chs: list[int], chapter_starts: list[float],
                 duration_s: float, ep_runtimes: dict[int, int]):
        rows = [_EpisodeRow(lo + i) for i in range(hi - lo + 1)]
        super().__init__(display, rows)
        self.season = season
        self.lo = lo
        self.hi = hi
        self.start_chs = list(start_chs)  # each episode's start chapter, 1-based
        self.chapter_starts = chapter_starts
        self.duration_s = duration_s
        self.ep_runtimes = ep_runtimes
        self.pending = ""
        self.message = ""
        self._blocks = self._row_blocks()

    def _row_blocks(self) -> dict[int, str]:
        """Episode -> its blocking reason (Enter refuses while any exists):
        the fix_rejection rules over the layout that stands now. E-lo never
        blocks — it is pinned to chapter 1 by construction, and the pinned
        rule names edits, not the row itself."""
        n = len(self.chapter_starts)
        out: dict[int, str] = {}
        for i in range(1, len(self.start_chs)):
            e = self.lo + i
            why = fix_rejection((e, self.start_chs[i]), self.start_chs, n,
                                self.lo, self.hi)
            if why:
                out[e] = why
        return out

    def _flagged(self) -> set[int]:
        return boundary_flags(self.start_chs, self.chapter_starts,
                              self.ep_runtimes, self.lo, self.hi)

    def _cells(self, t: _EpisodeRow) -> tuple:
        e = t.number
        notes = self._blocks.get(e)
        if notes is None:
            notes = boundary_note_cell(self._flagged(), e)
        return boundary_row_cells(self.season, self.lo, e - self.lo,
                                  self.start_chs, self.chapter_starts,
                                  self.duration_s, self.ep_runtimes,
                                  notes, e in self._blocks)

    def _status_widgets(self) -> list:
        return [Static(self._status_text(), id="status")]

    def _status_text(self) -> Text:
        lines = []
        if self._blocks:
            rows = ", ".join(f"E{e:02d}" for e in sorted(self._blocks))
            lines.append(Text(f"✗ blocked: fix the red rows ({rows}) — "
                              "Enter refused", style="bold red"))
        flagged = self._flagged() - set(self._blocks)
        note = boundary_note(flagged)
        if note:
            lines.append(Text(f"! {note} — Enter still accepts",
                              style="bold yellow"))
        if self.pending:
            lines.append(Text(f"chapter ▸ {self.pending}▏  (Enter sets it)"))
        if self.message:
            lines.append(Text(self.message, style="bold"))
        return Text("\n").join(lines)

    def _regrade(self, message: str = "") -> None:
        """Re-derive blocks and flags after any edit and repaint every row
        and the status lines."""
        self._blocks = self._row_blocks()
        self.message = message
        for t in self.titles:
            self._refresh_row(t.number)
        self.query_one("#status", Static).update(self._status_text())

    def _cursor_episode(self) -> int:
        return self._cursor_title().number

    def action_type(self, ch: str) -> None:
        self.pending += ch
        self._regrade()

    def action_backspace(self) -> None:
        self.pending = self.pending[:-1]
        self._regrade()

    def _commit(self) -> bool:
        """Apply the typed chapter number to the cursor row. True when there
        was something typed."""
        if not self.pending:
            return False
        typed, self.pending = self.pending, ""
        e = self._cursor_episode()
        if not typed.isdigit():
            self._regrade(f"✗ '{typed}' is not a chapter number")
            return True
        ch = int(typed)
        why = fix_rejection((e, ch), self.start_chs, len(self.chapter_starts),
                            self.lo, self.hi)
        if why:
            self._regrade(f"✗ E{e:02d}: {why}")
        else:
            self.start_chs[e - self.lo] = ch
            self._regrade()
        return True

    def action_accept(self) -> None:
        if self._commit():
            return  # Enter set the typed value; a second Enter accepts
        if self._blocks:
            self._regrade("✗ Enter refused — the red rows must be fixed first")
        else:
            self.exit(spans_from_starts(self.start_chs,
                                        len(self.chapter_starts)))


def run_boundary_table(display: str, season: int, lo: int, hi: int,
                       spans: list[tuple[int, int]],
                       chapter_starts: list[float], duration_s: float,
                       ep_runtimes: dict[int, int],
                       ) -> list[tuple[int, int]] | str:
    """Run the boundary table over the suggested spans and return the
    confirmed spans, QUIT (the operator declined — the combined file stays)
    or INTERRUPT (the caller re-raises KeyboardInterrupt once Textual has
    restored the terminal, so split_combined's handler sweeps and keeps the
    combined file)."""
    return BoundaryTable(display, season, lo, hi, [s for s, _ in spans],
                         chapter_starts, duration_s, ep_runtimes).run()


def fix_rejection(fix: tuple[int, int], start_chs: list[int], n_chapters: int,
                  lo: int, hi: int) -> str | None:
    """Why a chapter number cannot be episode e's start; None when it can.
    The table's blocking state: the same rules grade the layout that stands
    (red rows block Enter) and every typed value before it lands.

    Args:
        fix: (episode, start chapter) — the table's typed fix row
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
        ui.info(f"chapter split — {combined.name}")
        if not sys.stdin.isatty():
            # The split is post-rip inside the same run: a headless completion
            # must never hang on a terminal it does not have — decline, keep
            # the combined file.
            log.note("split", "skipped: no terminal — combined kept")
            return
        display = combined.name
        with ui.suspend():
            confirmed = run_boundary_table(display, season, lo, hi,
                                           suggested[0], chapter_starts,
                                           duration_s, ep_runtimes)
        if confirmed == INTERRUPT:
            raise KeyboardInterrupt  # Textual restored the terminal; sweep below
        if confirmed == QUIT:
            log.note("split",
                     "skipped: declined at the boundary table — combined kept")
            return
        print_accepted_boundaries(display, season, lo, confirmed,
                                  chapter_starts, duration_s, ep_runtimes)
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
