"""Event lines and the run view: everything skive prints about what it is
doing.

Event lines (ok/info/warn/fail), item headings and the run opener go to
stderr; stdout carries only data answers. On a terminal they print through
one shared rich console with a Theme of named styles, each line led by a
symbol (``✓ · ! ✗``); off a terminal they stay plain tagged lines
(``info  text``). Colour on/off is rich's own detection (NO_COLOR,
TERM=dumb, non-TTY). A module-level lock guarantees whole lines across
threads.

A long-running command (transcode, rip) opens a RunView (ADR 0014). On a
TTY the run is framed scrollback: a closed opener box, one right-open item
frame per item (header rule, `│` gutter, closing rule), and a closed live
box titled `now` redrawing beneath them; the run summary is a closed box
too. Every box and rule is min(console width, 100) wide. Off a TTY, under
NO_COLOR or TERM=dumb, every line is a plain tagged line with no box
characters. Verbosity gating stays at the call sites — this is the console
layer only.
"""

import contextlib
import os
import io
import re
import sys
import threading
from pathlib import Path

from rich.box import ROUNDED, SIMPLE_HEAD, Box
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

THEME = Theme({"ok": "green", "warn": "yellow", "fail": "red",
               "hot": "bold red", "dim": "dim", "item": "bold",
               "phase.copy": "blue", "phase.merge": "yellow",
               "phase.subs": "magenta", "phase.crop": "cyan",
               "phase.prefetch": "dim blue", "phase.encode": "green",
               "frame": "dim", "border": "blue", "title": "bold"})
WIDTH_CAP = 100  # every box, frame rule and table: min(console width, 100)
PLAIN_HEAD = Box(str(SIMPLE_HEAD).replace("\u2500", "-"), ascii=True)  # piped tables: no box characters
PHASE_WIDTH = len("prefetch")  # the phase column pads to its longest name

# The one shared console. stderr=True resolves sys.stderr at write time (a
# Live's redirect and pytest's capture both see it); no hand-written colour
# checks — rich decides from NO_COLOR, TERM and isatty.
console = Console(stderr=True, theme=THEME, highlight=False, emoji=False)

SYMBOLS = {"ok": "\u2713", "info": "\u00b7", "warn": "!", "fail": "\u2717"}
_SYMBOL_STYLE = {"ok": "ok", "info": "dim", "warn": "warn", "fail": "fail"}

_print_lock = threading.Lock()
_view = None  # the run's RunView while one is live (common.VERBOSITY precedent)
# TTY: an item frame is open, so lines print behind its `│` gutter; closed by
# clear_view and run_summary so a crashed run never leaks a gutter onward
_frame_open = False
_scope = ""  # the opener's scope line: the live box's text when it has no lane


def set_view(view) -> None:
    global _view
    _view = view


def clear_view() -> None:
    global _view
    _neutral_close()  # a run that died before run_summary never leaks its gutter
    _view = None


def _width() -> int:
    return min(console.width, WIDTH_CAP)


def _plain_env() -> bool:
    """NO_COLOR or TERM=dumb: plain tagged lines even on a terminal."""
    return bool(os.environ.get("NO_COLOR")) or os.environ.get("TERM") in ("dumb", "unknown")


def view():
    """The live RunView, or None (call sites keep their own gating)."""
    return _view


@contextlib.contextmanager
def suspend():
    """Suspend the registered run view while an interactive prompt or table
    prints normally (scrollback); redraw after. A no-op `with` when no view
    is registered (fallback, tests) or the view holds no Live."""
    if _view is None:
        yield
        return
    with _view.suspend():
        yield


def _styled() -> bool:
    """Lines render with symbols: a registered TTY view decides while it is
    open, the shared console's terminal detection otherwise."""
    if _view is not None and not _view._closed:
        return _view.tty
    return console.is_terminal and not _plain_env()


def _emit(text: Text) -> None:
    """One styled line into scrollback (above the footer while it is live)."""
    with _print_lock:
        console.print(text, soft_wrap=True)


def _plain(line: str) -> None:
    with _print_lock:
        print(line, file=sys.stderr)


def _gutter() -> Text:
    return Text.assemble(("\u2502 ", "frame")) if _frame_open else Text()


def _event_text(kind: str, text: str, phase: str | None = None,
                elapsed: str | None = None) -> Text:
    """`│ ✓ copy      3664 MB at 19 MB/s      3m06s`: gutter while a frame
    is open, symbol, the phase in its colour, detail, elapsed right-aligned
    at the frame width."""
    line = _gutter()
    line.append(SYMBOLS[kind], style=_SYMBOL_STYLE[kind])
    if phase:
        line.append(f" {phase:<{PHASE_WIDTH}} ", style=f"phase.{phase}")
    detail = Text(f" {text}")
    if elapsed:  # the detail gives way so the time stays right-aligned on one row
        detail.truncate(max(_width() - line.cell_len - len(elapsed) - 2, 2), overflow="ellipsis")
    line.append_text(detail)
    if elapsed:
        line.append(" " * max(_width() - line.cell_len - len(elapsed), 2))
        line.append(elapsed, style="dim")
    return line


def _plain_text(text: str, phase: str | None = None, elapsed: str | None = None) -> str:
    """Plain form of a phase line: the phase leads the detail, the elapsed
    trails it (`copy 3664 MB at 19 MB/s 3m06s`)."""
    return " ".join(x for x in (phase, text, elapsed) if x)


def _plain_event(kind: str, text: str) -> str:
    return f"{kind:<4}  {text}"


def _rule(line: Text, width: int) -> Text:
    """line truncated to fit, then padded with ` ───` to exactly width."""
    line.truncate(width - 2, overflow="ellipsis")
    line.append(" " + "\u2500" * (width - line.cell_len - 1))
    return line


def _neutral_close() -> None:
    """Close an open frame with a plain dim rule (no outcome known)."""
    global _frame_open
    if _frame_open and _styled():
        _emit(Text("\u2570" + "\u2500" * (_width() - 1), style="frame"))
    _frame_open = False


def mark(kind: str, text: str) -> Text:
    """`✓ text`: the kind's symbol in its style, for a table cell or an
    indented row outside the event stream (search states, validate checks)."""
    return Text.assemble((SYMBOLS[kind], _SYMBOL_STYLE[kind]), f" {text}")


def out(text: str) -> None:
    """A data answer on stdout (piped search hits, --json): the bare text,
    no symbol, no style."""
    with _print_lock:
        try:
            print(text)
            sys.stdout.flush()
        except BrokenPipeError:  # `| head`: stop quietly, no traceback at exit
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            raise SystemExit(0)


class RunView:
    """One instance per run. TTY mode: event, heading and tool lines print
    to scrollback through the shared console and a rich Live (transient, no
    alternate screen) redraws only the live box (`now`) beneath them.
    Fallback mode (stderr not a TTY, NO_COLOR, TERM=dumb, including pytest):
    no Live; every line is a plain tagged line. render() returns the live box
    as text without a terminal — the test seam."""

    BAR_WIDTH = 20

    def __init__(self, tty: bool | None = None, live: bool = True,
                 perf: bool = True):
        """tty=None detects stderr; live=False forces the TTY render path
        without ever constructing a Live (the test seam — a real Live must
        never exist under pytest). perf=False skips the perf sampler, so the
        footer never grows a perf line."""
        if tty is None:
            stream = sys.stderr
            try:
                tty = bool(stream is not None and stream.isatty()) and not _plain_env()
            except ValueError:  # closed stream
                tty = False
        self.tty = tty
        self._closed = False
        self._pos = ""  # lane 1's position (`2/4`): never truncates away
        self._label = Text()  # lane 1, left part: position · item · phase
        self._step = ""  # lane 1's step text (copying…, OCR track n)
        self._phase_len = 0  # the label's ` · phase` tail: never truncates
        self._pct: float | None = None
        self._eta: str | None = None
        self._perf = None  # (text, hot segment strings)
        # concurrent-activity lines, keyed; replaced whole (never mutated) so
        # the Live refresher thread can read it without the print lock
        self._bg: dict[str, str | tuple[str, float, str | None]] = {}
        self._replay: list[str] = []  # TTY-only warn/fail replay buffer
        self._live = None
        if self.tty and live:
            self._start_live()
            if perf:
                self._sampler = PerfSampler(self)
                self._sampler.start()

    def _start_live(self) -> None:
        from rich.live import Live
        # the Live renders this view (__rich__) at each refresh, so setters
        # only change state; no update call, no lock shared with the refresher
        self._live = Live(self, console=console, refresh_per_second=4,
                          transient=True)
        self._live.start()

    def __rich_console__(self, console, options):
        width = min(options.max_width, WIDTH_CAP)
        yield Panel(self._footer(width - 4), box=ROUNDED, width=width,
                    title=Text("now", style="title"), title_align="left",
                    border_style="border")

    def _footer(self, width: int) -> Group:
        """The live box's lanes at `width` inner columns: the item lane
        (position, item · phase step, bar, percent, ETA), one lane per
        background activity, the perf lane. Narrow widths drop the perf lane,
        then the background lanes, then shrink the bar to 10, then truncate
        the name with `…` (the `· phase` stays, the bar gives way first); position, percent and ETA never drop. With no lane
        left the box shows the opener's scope, so it is never empty."""
        lanes: list = []
        lane1 = self._item_lane(width)
        if lane1 is not None:
            lanes.append(lane1)
        bg, perf = self._bg, self._perf
        if bg and width >= 50:
            for k in sorted(bg):
                entry = bg[k]
                if isinstance(entry, tuple):
                    name, pct, rate = entry
                    bar = self.BAR_WIDTH if width >= 70 else 10
                    row = Table.grid(padding=(0, 2, 0, 0))
                    row.add_column(no_wrap=True, overflow="ellipsis")
                    row.add_column(no_wrap=True, width=bar)
                    row.add_column(no_wrap=True, width=5)
                    if rate:
                        row.add_column(no_wrap=True, width=len(rate))
                    cells: list = [Text(f"{k} {name}", style="dim"),
                                   ProgressBar(total=100, completed=pct, width=bar),
                                   Text(f"{pct:.0f}%", style="dim")]
                    if rate:
                        cells.append(Text(rate, style="dim"))
                    row.add_row(*cells)
                    lanes.append(row)
                else:
                    ready = " \u2713 ready"
                    if str(entry).endswith(ready):
                        title = Text(f"{k} ")
                        name = Text(str(entry)[:-len(ready)], style="dim")
                        name.truncate(max(width - title.cell_len - len(ready), 1),
                                      overflow="ellipsis")
                        title.append_text(name)
                        title.append(ready, style="dim")
                        lanes.append(title)
                    else:
                        lanes.append(Text(f"{k} {entry}", style="dim", no_wrap=True,
                                          overflow="ellipsis"))
        if perf and width >= 60:
            text, hot = perf
            line = Text(no_wrap=True, overflow="ellipsis")
            for i, seg in enumerate(text.split(" \u00b7 ")):
                if i:
                    line.append(" \u00b7 ", style="dim")
                line.append(seg, style="hot" if seg in hot else "dim")
            lanes.append(line)
        if not lanes:
            lanes.append(Text(_scope or "skive", style="dim", no_wrap=True,
                              overflow="ellipsis"))
        return Group(*lanes)

    def _item_lane(self, width: int) -> Table | None:
        label, pct, eta = self._label, self._pct, self._eta
        if not label and pct is None and not eta:
            return None
        label = label.copy()
        phase = Text()
        if self._phase_len:
            label, phase = label.divide([len(label.plain) - self._phase_len])
        step = Text(f" {self._step}" if self._step else "", style="dim")
        right = [s for s in (None if pct is None else f"{pct:.0f}%",
                             f"ETA {eta}" if eta else None) if s]
        fixed = sum(len(s) + 2 for s in right)
        bar = 0 if pct is None else self.BAR_WIDTH if width >= 44 else 10
        room = width - fixed - (bar + 2 if bar else 0)
        keep = len(self._pos) + 2 + phase.cell_len  # the position, `…` and the phase
        if bar and room < keep:  # still too narrow: the bar gives way
            bar = max(bar - (keep - room), 0)
            room = width - fixed - (bar + 2 if bar else 0)
        # the name gives way to the phase and the step; the step to the phase
        label.truncate(max(room - phase.cell_len - step.cell_len, len(self._pos) + 2, 1),
                       overflow="ellipsis")
        left = room - label.cell_len - phase.cell_len
        if left < 3:  # no room for even ` x…`
            step = Text()
        else:
            step.truncate(left, overflow="ellipsis")
        label.append_text(phase)
        label.append_text(step)
        label.truncate(max(room, 1), overflow="ellipsis")
        row = Table.grid(padding=(0, 2, 0, 0))
        row.add_column(no_wrap=True, width=label.cell_len)
        cells: list = [label]
        if bar:
            row.add_column(no_wrap=True, width=bar)
            cells.append(ProgressBar(total=100, completed=pct, width=bar))
        for s in right:
            row.add_column(no_wrap=True, width=len(s))
            cells.append(Text(s))
        row.add_row(*cells)
        return row

    def footer(self, pos: str, name: str, phase: str = "") -> None:
        """The item lane's left part: `2/4 Name · phase`; a new phase
        starts without a bar or step until its first update."""
        label = Text(f"{pos} " if pos else "")
        label.append(name, style="item")
        if phase:
            label.append(f" \u00b7 {phase}")
        self._pos, self._label, self._step = pos, label, ""
        self._phase_len = len(phase) + 3 if phase else 0
        self._pct = self._eta = None

    def status(self, *lines: str) -> None:
        """The item lane's left part as free text, parts joined by ` · `."""
        self._pos, self._step, self._phase_len = "", "", 0
        self._label = Text(" \u00b7 ".join(str(x) for x in lines))

    def step(self, text: str) -> None:
        """The current step (copying…, OCR track n) beside the phase in the
        item lane; the next footer() clears it."""
        self._step = str(text)

    def progress(self, pct, eta) -> None:
        """Progress for the footer bar; a None arg omits that field."""
        self._pct = None if pct is None else float(pct)
        self._eta = eta

    def perf(self, text: str, hot=set()) -> None:
        """The perf lane (transcode only). No-op in fallback;
        an empty text drops it."""
        if not self.tty:
            return
        self._perf = (text, set(hot)) if text else None

    def bg(self, key: str, text: str, pct: float | None = None,
           rate: str | None = None) -> None:
        """One background-activity entry per concurrent activity, keyed; it
        updates in place. A percentage adds its own bar and optional rate.
        No-op in fallback mode (plain prints remain)."""
        if not self.tty:
            return
        value: str | tuple[str, float, str | None] = (
            str(text) if pct is None else (str(text), float(pct), rate))
        self._bg = {**self._bg, key: value}

    def bg_clear(self, key: str) -> None:
        """Remove a background entry and its lane."""
        if not self.tty:
            return
        self._bg = {k: v for k, v in self._bg.items() if k != key}

    def tool(self, name: str, line: str) -> None:
        """A tool line (makemkv/handbrake/ffmpeg/mkvmerge style): dimmed and
        tool-prefixed in scrollback on a TTY (in the frame gutter),
        ``{name}  {line}`` plain."""
        if self.tty:
            text = _gutter()
            text.append(f"{name}  {line}", style="dim")
            _emit(text)
        else:
            _plain(f"{name}  {line}")

    def event(self, kind: str, text: str, phase: str | None = None,
              elapsed: str | None = None) -> None:
        """An ok/info/warn/fail event line; ui._print routes here while the
        view is open."""
        if self.tty:
            if kind in ("warn", "fail"):  # replayed into the summary
                self._replay.append(f"{kind}  {_plain_text(text, phase, elapsed)}")
            _emit(_event_text(kind, text, phase, elapsed))
        else:
            _plain(_plain_event(kind, _plain_text(text, phase, elapsed)))

    def suspend(self):
        """Pause the Live footer while a prompt prints normally (scrollback);
        redraw after. A no-op `with` when no Live runs (fallback, seam)."""
        @contextlib.contextmanager
        def _ctx():
            if self._live is None or self._closed:
                yield
                return
            self._live.stop()
            self._live = None
            try:
                yield
            finally:
                self._start_live()
        return _ctx()

    def close(self) -> None:
        """Stop the Live footer; idempotent, safe from finally."""
        sampler = getattr(self, "_sampler", None)
        if sampler is not None:
            sampler.stop()
        self._closed = True  # unconditional: ui._print stops routing to a closed view
        if self._live is not None:
            self._live.stop()
            self._live = None

    def replay_lines(self) -> list[str]:
        """Every warn/fail line rendered this run, in order (TTY only; the
        fallback already has them in the scroll)."""
        return list(self._replay)

    def render(self, width: int = 80) -> str:
        """The live box as plain text at `width` columns, no terminal
        (deterministic; colour-free)."""
        out = io.StringIO()
        Console(file=out, width=width, theme=THEME, color_system=None,
                highlight=False).print(self)
        return out.getvalue()


def _box(body, title: Text | None = None, subtitle: Text | None = None) -> Panel:
    """The one closed-box look: rounded, blue border, bold title in the top
    border, at the shared width."""
    return Panel(body, box=ROUNDED, width=_width(), title=title, title_align="left",
                 subtitle=subtitle, border_style="border")


def opener(title: str, *lines: str) -> None:
    """The run opener: a closed box with the title in its border and the
    run's scope lines inside (a lone title is the box's one line). Plain: one
    info line, the parts joined by ` · `."""
    global _scope
    _neutral_close()
    _scope = lines[0] if lines else title
    if _styled():
        box = (_box(Text("\n".join(lines)), Text(title, style="title")) if lines
               else _box(Text(title)))
        with _print_lock:
            console.print(box)
    else:
        _plain(_plain_event("info", " \u00b7 ".join((title, *lines))))


def unindent() -> None:
    """Events for an item with no heading must not hang in the last frame."""
    _neutral_close()


def item(pos: int, total: int, name: str) -> None:
    """The item frame's header rule `╭─ 2/4  Name ───`; on a TTY later lines
    print behind its `│` gutter until item_end (or the next item, which
    closes the old frame neutrally first)."""
    global _frame_open
    if _styled():
        _neutral_close()
        head = Text("\u256d\u2500 ", style="frame")
        head.append(f"{pos}/{total}", style="dim")
        head.append("  ")
        head.append(name, style="item")
        _emit(_rule(head, _width()))
        _frame_open = True
    else:
        _plain(_plain_event("info", f"{pos}/{total}  {name}"))


def item_end(kind: str, text: str, elapsed: str | None = None) -> None:
    """The item frame's closing rule `╰─ ✓ done 7m56s ───`, coloured by kind,
    the elapsed bold. Plain mode has no frames: nothing prints."""
    global _frame_open
    if _styled():
        line = Text(f"\u2570\u2500 {SYMBOLS[kind]} {text}", style=_SYMBOL_STYLE[kind])
        if elapsed:
            line.append(f" {elapsed}", style="item")
        _emit(_rule(line, _width()))
    _frame_open = False


_HMS_RX = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(\d+)s$")


def _secs(value: str) -> int | None:
    """Reverse of transcode._hms (kept here: ui must not import transcode)."""
    m = _HMS_RX.match(value.removesuffix(" ⇢"))
    return sum(int(g) * mult for g, mult in zip(m.groups(), (3600, 60, 1)) if g) if m else None


def _hms(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def _span(secs: int) -> str:
    """Run length for the result line: `2h41m`, else the _hms form."""
    if secs < 3600:
        return _hms(secs)
    h, rest = divmod(secs, 3600)
    return f"{h}h{rest // 60:02d}m"


def _result_cell(word: str, styled: bool) -> Text | str:
    """A result-column cell: `✓ ok` / `✗ failed` / `✗ interrupted` on a
    TTY, the bare word plain."""
    word = str(word).lower()
    if word not in ("ok", "failed", "interrupted"):
        return word
    if not styled:
        return word
    kind = "ok" if word == "ok" else "fail"
    return Text.assemble((SYMBOLS[kind], _SYMBOL_STYLE[kind]), f" {word}")


def _result_text(n_ok: int, n_failed: int, elapsed: int | None = None,
                interrupt_note: str | None = None,
                see: str | None = None, failed: bool = False) -> tuple[str, str]:
    """The run's closing result line as (kind, text): counts, run length,
    then `— <interrupt_note>` after an interrupt or `— <see>` after a
    failure. failed marks a run that failed with no failed row (a fatal
    error outside any item, a rip that failed before its rows existed)."""
    failed = failed or bool(n_failed)
    parts = ["interrupted"] if interrupt_note is not None else []
    if n_failed:
        parts.append(f"{n_failed} failed")
    elif failed:
        parts.append("failed")
    parts.append(f"{n_ok} ok")
    if elapsed is not None:
        parts.append(_span(elapsed))
    text = " \u00b7 ".join(parts)
    if interrupt_note is not None:
        text += f" \u2014 {interrupt_note}"
    elif failed and see:
        text += f" \u2014 {see}"
    return ("fail" if interrupt_note is not None or failed else "ok"), text


def run_summary(rows, n_ok: int, n_failed: int, keep_banner: str | None = None,
                replay=(), columns=None, log=None,
                interrupt_note: str | None = None, failed: bool = False) -> None:
    """End-of-run summary on stderr (after the run view closed): warn/fail
    lines replayed (TTY only), then on a TTY one closed `summary` box (columns
    `-` for every row dropped, the result line as its bottom title) with the
    --keep banner under it; plain: the table, the banner, the result line.
    The banner prints only when keep_banner is set. columns=None
    keeps the transcode shape (per-phase cells summed into a total; the row's
    third field is ok/failed/interrupted); a columns tuple renders each row
    as ready cells (the rip shape). log (the run's RipLog) adds the run
    length and, after a failure, where to look (the run log, plus the raw
    log when finish() retained it); interrupt_note marks an interrupted run
    (`✗ interrupted · 2 ok · 1h12m — partial deleted`); failed carries the
    run's own outcome so a failure with no failed row still ends `✗`."""
    styled = _styled()
    elapsed = see = None
    if log is not None:
        try:
            if log._start is not None:
                elapsed = int((log._now() - log._start).total_seconds())
        except (AttributeError, TypeError):
            # Test seams may provide a lightweight log mock without clock data.
            elapsed = None
        run_log = getattr(log, "_run_log", None)
        if isinstance(run_log, (str, Path)):
            see = f"see {run_log} (run {log.run})"
            raw_log = getattr(log, "_raw_log", None)
            if raw_log is not None and raw_log.exists():
                see += f", raw {raw_log}"
    kind, result = _result_text(n_ok, n_failed, elapsed, interrupt_note, see, failed)
    _neutral_close()  # a crashed run's open frame never leaks its gutter
    if columns is None:  # the transcode shape: per-phase cells summed into a total
        columns = ("Item", "copy", "merge", "subs", "encode", "total", "result")
        body = []
        for label, parts, word in rows:
            cells: dict[str, str] = {}
            total = 0
            for part in parts:
                phase, _, value = part.partition(" ")
                cells[phase] = value
                s = _secs(value)
                if s is not None:
                    total += s
            body.append([label, *(cells.get(p, "-") for p in ("copy", "merge", "subs", "encode")),
                         _hms(total), word])
    else:
        body = [list(row) for row in rows]
    keep = range(len(columns))
    if styled and body:  # the box drops columns that are `-` for every row
        keep = [c for c in keep if any(str(row[c]) != "-" for row in body)]
    with _print_lock:
        for line in replay:
            rkind, _, text = line.partition("  ")
            if styled and rkind in SYMBOLS:
                console.print(Text.assemble((SYMBOLS[rkind], _SYMBOL_STYLE[rkind]),
                                            f" {text}"), soft_wrap=True)
            else:
                print(line, file=sys.stderr)
        # plain: PLAIN_HEAD's blank top and bottom edges space the table
        # from the replay above and the result line below; styled: the box
        # is the border, the table keeps only its header rule
        table = (Table(box=SIMPLE_HEAD, header_style="dim", show_edge=False,
                       expand=True, pad_edge=False) if styled
                 else Table(box=PLAIN_HEAD, header_style=""))  # plain: unstyled, even on a NO_COLOR terminal
        for c in keep:
            table.add_column(columns[c])
        for row in body:
            table.add_row(*(_result_cell(row[c], styled) if columns[c] == "result"
                            else str(row[c]) for c in keep))
        if not styled:
            console.print(table)
            if keep_banner is not None:
                console.print(keep_banner, soft_wrap=True)
            print(_plain_event(kind, result), file=sys.stderr)
            return
        line = Text.assemble((SYMBOLS[kind], _SYMBOL_STYLE[kind]), f" {result}",
                             style=_SYMBOL_STYLE[kind])
        fits = line.cell_len <= _width() - 6
        if not fits:  # the border keeps the counts; the full line prints below
            head = result.partition(" \u2014 ")[0]
            line = Text(f"{SYMBOLS[kind]} {head}", style=_SYMBOL_STYLE[kind])
        console.print(_box(table, Text("summary", style="title"), line))
        if not fits:
            console.print(Text(f"{SYMBOLS[kind]} {result}", style=_SYMBOL_STYLE[kind]),
                          soft_wrap=True)
        if keep_banner is not None:
            console.print(Text(keep_banner, style="warn"), soft_wrap=True)


def result_line(kind: str, *parts: str, elapsed: int | None = None,
                note: str | None = None) -> None:
    """A command's closing result line without a table: `✓ synced 4.1 GB ·
    2m10s` — parts joined by ` · `, the run length, then `— note` (the next
    step after a failure). kind is ok, warn or fail."""
    text = " \u00b7 ".join([*parts, *([_span(elapsed)] if elapsed is not None else [])])
    if note:
        text += f" \u2014 {note}"
    if _styled():
        _emit(_event_text(kind, text))
    else:
        _plain(_plain_event(kind, text))


def _print(kind: str, text: str, phase: str | None = None,
           elapsed: str | None = None) -> None:
    # the lock must not span the view dispatch: _emit takes it too, and a
    # plain Lock re-acquiring on the same thread deadlocks
    if _view is not None and _view.tty and not _view._closed:
        _view.event(kind, text, phase, elapsed)
    elif _styled():
        _emit(_event_text(kind, text, phase, elapsed))
    else:
        _plain(_plain_event(kind, _plain_text(text, phase, elapsed)))


def tool(name: str, text: str) -> None:
    """A passed-through tool line: the run view's dimmed tool line when a view
    is registered, ``{name}  {text}`` on stderr otherwise."""
    if _view is not None:
        _view.tool(name, text)
    else:
        _plain(f"{name}  {text}")


def ok(text: str, phase: str | None = None, elapsed: str | None = None) -> None:
    """An item or phase finished; phase/elapsed make it a phase line."""
    _print("ok", text, phase, elapsed)


def info(text: str, phase: str | None = None, elapsed: str | None = None) -> None:
    """Routine event, skip, or deferral."""
    _print("info", text, phase, elapsed)


def warn(text: str, phase: str | None = None, elapsed: str | None = None) -> None:
    """The loud note; the run continues."""
    _print("warn", text, phase, elapsed)


def fail(text: str, phase: str | None = None, elapsed: str | None = None) -> None:
    """Terminal failure."""
    _print("fail", text, phase, elapsed)


def step(text: str, verbose: bool = False) -> None:
    """A step line (copying…, OCR track n, wrote .srt): live-box text on a
    TTY run view, kept in scrollback too when the caller says verbose (-v);
    an info line otherwise."""
    if _view is not None and _view.tty and not _view._closed:
        _view.step(text)
        if not verbose:
            return
    info(text)


# -- prompts and tables: one shape everywhere ----------------------------------

def _read(question: Text) -> str:
    """Print the question line, read one answer on a `›` line. The footer is
    suspended while asking; stdin is read exactly as before (a piped stdin
    answers or raises EOFError, never a new wait)."""
    with suspend():
        with _print_lock:
            console.print(question, soft_wrap=True)
        answer = console.input("\u203a ").strip()
        if not sys.stdin.isatty():
            console.print()  # piped input is not echoed: end the marker line
        return answer


def ask(question: str, choices: str = "", default: str = "") -> str:
    """One prompt: `question [default] choices` with the choices dimmed, the
    answer read on a `›` line. Returns the stripped answer, or `default` on
    an empty one."""
    line = Text(question)
    if default:
        line.append(f" [{default}]")
    if choices:
        line.append(f" {choices}", style="dim")
    return _read(line) or default


def confirm(question: str, default: bool = False) -> bool:
    """A y/n prompt; an empty answer takes `default` (No unless the caller
    says otherwise, so a stray Enter never confirms a destructive step)."""
    answer = _read(Text(f"{question} {'[Y/n]' if default else '[y/N]'}")).lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def table(*columns: str, title: str | None = None) -> Table:
    """The one table look: SIMPLE_HEAD, dim header, no column colours
    (callers style only cells that mean something, e.g. a bold pick number)."""
    t = Table(box=SIMPLE_HEAD, header_style="dim", title=title)
    for col in columns:
        t.add_column(col)
    return t


# -- perf line (slice 05): CPU % via /proc/stat delta + hwmon temps ------------

_HWMON_KEYS = {"k10temp": "cpu", "amdgpu": "gpu"}
_hwmon_cache: dict = {}  # root -> {key: temp*_input path}; sensors do not move


def read_proc_stat(path="/proc/stat") -> tuple[int, int] | None:
    """The first cpu line as (idle, total) jiffies; None when unreadable."""
    try:
        with open(path) as f:
            for line in f:
                if line.startswith("cpu "):
                    fields = [int(x) for x in line.split()[1:]]
                    if len(fields) < 4:  # truncated line: idle field missing
                        return None
                    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
                    return idle, sum(fields)
    except (OSError, ValueError):
        return None
    return None


def read_hwmon(root="/sys/class/hwmon") -> dict[str, float]:
    """Readable package temps in °C: k10temp -> cpu, amdgpu -> gpu by name,
    any other readable temp fills a missing key (deterministic walk order).
    Unreadable sensors are simply absent — never warned."""
    out: dict[str, float] = {}
    if root in _hwmon_cache:
        paths = _hwmon_cache[root]
    else:
        paths = {}
        try:
            dirs = sorted(d for d in Path(root).iterdir()
                          if d.name.startswith("hwmon"))
        except OSError:
            return {}
        for d in dirs:
            try:
                name = (d / "name").read_text().strip()
            except OSError:
                continue
            for t in sorted(d.glob("temp*_input")):
                key = _HWMON_KEYS.get(name)
                if key is None or key in paths:
                    continue
                paths[key] = t
        for d in dirs:  # generics fill only slots no named sensor took
            try:
                name = (d / "name").read_text().strip()
            except OSError:
                continue
            if name in _HWMON_KEYS:
                continue
            for t in sorted(d.glob("temp*_input")):
                key = ("cpu" if "cpu" not in paths
                       else "gpu" if "gpu" not in paths else None)
                if key is None:
                    break
                paths[key] = t
        _hwmon_cache[root] = paths
    for key, t in paths.items():
        try:
            out[key] = int(t.read_text()) / 1000
        except (OSError, ValueError):
            continue
    return out


def perf_text(cpu_pct: float | None, temps: dict[str, float]) -> tuple[str, set]:
    """`CPU 62% · cpu 78°C · gpu 61°C` from whatever is readable; the second
    value is the set of hot (>= 95 °C) segment strings to render red."""
    parts: list[str] = []
    hot: set[str] = set()
    if cpu_pct is not None:
        parts.append(f"CPU {cpu_pct:.0f}%")
    for key in ("cpu", "gpu"):
        temp = temps.get(key)
        if temp is not None:
            seg = f"{key} {temp:.0f}\N{DEGREE SIGN}C"
            parts.append(seg)
            if temp >= 95:
                hot.add(seg)
    return " \N{MIDDLE DOT} ".join(parts), hot


class PerfSampler:
    """Daemon thread feeding view.perf every ~2 s. Never touches the Live —
    only view state; the Live refresher redraws the footer."""

    TICK = 2.0

    def __init__(self, view: "RunView", stat_path="/proc/stat",
                 hwmon_root="/sys/class/hwmon"):
        self._view = view
        self._stat_path = stat_path
        self._hwmon_root = hwmon_root
        self._prev = None  # (idle, total) of the previous tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.TICK):
            self.tick()

    def tick(self) -> None:
        stat = read_proc_stat(self._stat_path)
        pct = None
        if stat is not None and self._prev is not None:
            idle, total = stat
            pidle, ptotal = self._prev
            if total != ptotal:  # no delta on the first tick → temps only
                pct = max(0.0, 100.0 * (1 - (idle - pidle) / (total - ptotal)))
        if stat is not None:
            self._prev = stat
        text, hot = perf_text(pct, read_hwmon(self._hwmon_root))
        self._view.perf(text, hot)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.TICK + 1)
            self._thread = None
