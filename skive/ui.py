"""Event lines and the run view: everything skive prints about what it is
doing.

Event lines (ok/info/warn/fail), item headings and the run opener go to
stderr; stdout carries only data answers. On a terminal they print through
one shared rich console with a Theme of named styles, each line led by a
symbol (``✓ · ! ✗``); off a terminal they stay plain tagged lines
(``info  text``). Colour on/off is rich's own detection (NO_COLOR,
TERM=dumb, non-TTY). A module-level lock guarantees whole lines across
threads.

A long-running command (transcode, rip) opens a RunView (ADR 0013). On a
TTY every line still prints to scrollback; only a borderless footer of at
most two lines redraws beneath it. Off a TTY the view holds no rich Live
and every line is plain. Verbosity gating stays at the call sites — this is
the console layer only.
"""

import contextlib
import io
import re
import sys
import threading
from pathlib import Path

from rich.console import Console, Group
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

THEME = Theme({"ok": "green", "warn": "yellow", "fail": "red",
               "hot": "bold red", "dim": "dim", "item": "bold"})

# The one shared console. stderr=True resolves sys.stderr at write time (a
# Live's redirect and pytest's capture both see it); no hand-written colour
# checks — rich decides from NO_COLOR, TERM and isatty.
console = Console(stderr=True, theme=THEME, highlight=False, emoji=False)

SYMBOLS = {"ok": "\u2713", "info": "\u00b7", "warn": "!", "fail": "\u2717"}
_SYMBOL_STYLE = {"ok": "ok", "info": "dim", "warn": "warn", "fail": "fail"}

_print_lock = threading.Lock()
_view = None  # the run's RunView while one is live (common.VERBOSITY precedent)
_indent = False  # TTY: lines indent two spaces beneath the last item heading


def set_view(view) -> None:
    global _view
    _view = view


def clear_view() -> None:
    global _view, _indent
    _view = None
    _indent = False


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
    return console.is_terminal


def _emit(text: Text) -> None:
    """One styled line into scrollback (above the footer while it is live)."""
    with _print_lock:
        console.print(text, soft_wrap=True)


def _plain(line: str) -> None:
    with _print_lock:
        print(line, file=sys.stderr)


def _event_text(kind: str, text: str) -> Text:
    line = Text("  " if _indent else "")
    line.append(SYMBOLS[kind], style=_SYMBOL_STYLE[kind])
    line.append(f" {text}")
    return line


def _plain_event(kind: str, text: str) -> str:
    return f"{kind:<4}  {text}"


class RunView:
    """One instance per run. TTY mode: event, heading and tool lines print
    to scrollback through the shared console and a rich Live (transient, no
    alternate screen) redraws only the footer beneath them. Fallback mode
    (stderr not a TTY, including pytest): no Live; every line is a plain
    tagged line. render() returns the footer as text without a terminal —
    the test seam."""

    BAR_WIDTH = 20

    def __init__(self, tty: bool | None = None, live: bool = True):
        """tty=None detects stderr; live=False forces the TTY render path
        without ever constructing a Live (the test seam — a real Live must
        never exist under pytest)."""
        if tty is None:
            stream = sys.stderr
            try:
                tty = bool(stream is not None and stream.isatty())
            except ValueError:  # closed stream
                tty = False
        self.tty = tty
        self._closed = False
        self._label = Text()  # footer line 1, left part: position · item · phase
        self._pct: float | None = None
        self._eta: str | None = None
        self._perf = None  # (text, hot segment strings)
        # concurrent-activity lines, keyed; replaced whole (never mutated) so
        # the Live refresher thread can read it without the print lock
        self._bg: dict[str, str] = {}
        self._replay: list[str] = []  # TTY-only warn/fail replay buffer
        self._live = None
        if self.tty and live:
            self._start_live()
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
        yield self._footer(options.max_width)

    def _footer(self, width: int) -> Group:
        """Footer line 1 (position · item · phase, bar, percent, ETA) and an
        optional line 2 (background activity, perf line). The left part
        truncates with `…` so bar, percent and ETA always fit."""
        lines = []
        label, pct, eta = self._label, self._pct, self._eta
        if label or pct is not None or eta:
            right: list = []
            if pct is not None:
                right.append((ProgressBar(total=100, completed=pct,
                                          width=self.BAR_WIDTH), self.BAR_WIDTH))
                right.append((Text(f"{pct:.0f}%"), len(f"{pct:.0f}%")))
            if eta:
                right.append((Text(f"ETA {eta}"), len(f"ETA {eta}")))
            room = max(width - sum(w + 2 for _, w in right), 1)
            label = label.copy()
            label.truncate(room, overflow="ellipsis")
            row = Table.grid(padding=(0, 2, 0, 0))
            row.add_column(no_wrap=True, width=label.cell_len)
            for _, w in right:
                row.add_column(no_wrap=True, width=w)
            row.add_row(label, *(r for r, _ in right))
            lines.append(row)
        bg, perf = self._bg, self._perf
        if bg or perf:
            line2 = Text("      ")
            line2.append("  ".join(f"{k} {bg[k]}" for k in sorted(bg)), style="dim")
            if perf:
                text, hot = perf
                if bg:
                    line2.append("      ")
                for i, seg in enumerate(text.split(" \u00b7 ")):
                    if i:
                        line2.append(" \u00b7 ", style="dim")
                    line2.append(seg, style="hot" if seg in hot else "dim")
            lines.append(line2)
        return Group(*lines)

    def footer(self, pos: str, name: str, phase: str = "") -> None:
        """Footer line 1's left part: ` 2/4  Name · phase`; a new phase
        starts without a bar until its first progress update."""
        label = Text(f" {pos}  " if pos else " ")
        label.append(name, style="item")
        if phase:
            label.append(f" \u00b7 {phase}")
        self._label = label
        self._pct = self._eta = None

    def status(self, *lines: str) -> None:
        """Footer line 1's left part as free text, parts joined by ` · `."""
        self._label = Text(" " + " \u00b7 ".join(str(x) for x in lines))

    def progress(self, pct, eta) -> None:
        """Progress for the footer bar; a None arg omits that field."""
        self._pct = None if pct is None else float(pct)
        self._eta = eta

    def perf(self, text: str, hot=set()) -> None:
        """Footer line 2's perf part (transcode only). No-op in fallback;
        an empty text drops it."""
        if not self.tty:
            return
        self._perf = (text, set(hot)) if text else None

    def bg(self, key: str, text: str) -> None:
        """One background-activity entry per concurrent activity, keyed; it
        updates in place. No-op in fallback mode (plain prints remain)."""
        if not self.tty:
            return
        self._bg = {**self._bg, key: str(text)}

    def bg_clear(self, key: str) -> None:
        """Remove a background entry; no line 2 when nothing remains."""
        if not self.tty:
            return
        self._bg = {k: v for k, v in self._bg.items() if k != key}

    def tool(self, name: str, line: str) -> None:
        """A tool line (makemkv/handbrake/ffmpeg/mkvmerge style): dimmed and
        tool-prefixed in scrollback on a TTY, ``{name}  {line}`` plain."""
        if self.tty:
            _emit(Text(("  " if _indent else "") + f"{name}  {line}", style="dim"))
        else:
            _plain(f"{name}  {line}")

    def event(self, kind: str, text: str) -> None:
        """An ok/info/warn/fail event line; ui._print routes here while the
        view is open."""
        if self.tty:
            if kind in ("warn", "fail"):
                self._replay.append(f"{kind}  {text}")  # replayed into the summary
            _emit(_event_text(kind, text))
        else:
            _plain(_plain_event(kind, text))

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
        """The footer as plain text at `width` columns, no terminal
        (deterministic; colour-free)."""
        out = io.StringIO()
        Console(file=out, width=width, theme=THEME, color_system=None,
                highlight=False).print(self)
        return out.getvalue()


def opener(text: str) -> None:
    """The run opener: one line stating the run's scope."""
    global _indent
    _indent = False
    if _styled():
        _emit(Text(text))
    else:
        _plain(_plain_event("info", text))


def unindent() -> None:
    """Events for an item with no heading must not hang under the last one."""
    global _indent
    _indent = False


def item(pos: int, total: int, name: str) -> None:
    """The item heading `▸ 2/4  Name`; on a TTY later lines indent beneath
    it until the next heading."""
    global _indent
    if _styled():
        _indent = False
        _emit(Text(f"\u25b8 {pos}/{total}  {name}", style="item"))
        _indent = True
    else:
        _plain(_plain_event("info", f"{pos}/{total}  {name}"))


_HMS_RX = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(\d+)s$")


def _secs(value: str) -> int | None:
    """Reverse of transcode._hms (kept here: ui must not import transcode)."""
    m = _HMS_RX.match(value)
    return sum(int(g) * mult for g, mult in zip(m.groups(), (3600, 60, 1)) if g) if m else None


def _hms(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def run_summary(rows, n_ok: int, n_failed: int, keep_banner: str | None = None,
                replay=(), columns=None) -> None:
    """End-of-run summary on stderr (after the run view closed): warn/fail
    lines replayed (TTY only), one table of rows, the ok/failed count, and
    the --keep banner only when keep_banner is set. columns=None keeps the
    transcode shape (per-phase cells summed into a total); a columns tuple
    renders each row as ready cells (the rip shape)."""
    with _print_lock:
        for line in replay:
            print(line, file=sys.stderr)
        from rich.console import Console
        from rich.table import Table
        console = Console(file=sys.stderr)
        table = Table(box=None)  # plain ASCII under pytest's non-TTY stderr
        if columns is not None:
            for col in columns:
                table.add_column(col)
            for row in rows:
                table.add_row(*(str(c) for c in row))
        else:
            for col in ("Item", "copy", "merge", "subs", "encode", "total", "result"):
                table.add_column(col)
            for label, parts, ok in rows:
                cells: dict[str, str] = {}
                total = 0
                for part in parts:
                    phase, _, value = part.partition(" ")
                    cells[phase] = value
                    s = _secs(value)
                    if s is not None:
                        total += s
                table.add_row(label,
                              *(cells.get(p, "-") for p in ("copy", "merge", "subs", "encode")),
                              _hms(total), "ok" if ok else "FAILED")
        console.print(table)
        console.print(f"{n_ok} ok, {n_failed} failed")
        if keep_banner is not None:
            console.print(keep_banner)


def _print(kind: str, text: str) -> None:
    # the lock must not span the view dispatch: _emit takes it too, and a
    # plain Lock re-acquiring on the same thread deadlocks
    if _view is not None and _view.tty and not _view._closed:
        _view.event(kind, text)
    elif console.is_terminal:
        _emit(_event_text(kind, text))
    else:
        _plain(_plain_event(kind, text))


def ok(text: str) -> None:
    """An item or step finished."""
    _print("ok", text)


def info(text: str) -> None:
    """Routine event, skip, or deferral."""
    _print("info", text)


def warn(text: str) -> None:
    """The loud note; the run continues."""
    _print("warn", text)


def fail(text: str) -> None:
    """Terminal failure."""
    _print("fail", text)


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
