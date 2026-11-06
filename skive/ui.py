"""Event lines and the run view: everything skive prints about what it is
doing.

info/warn/fail render one plain tagged line per call on stderr
(``info  text``); stdout carries only data answers. A module-level
lock guarantees whole lines across threads.

When a long-running command (transcode, rip) opens a RunView on a TTY, the
event lines feed the run view's log panel instead of printing (ADR 0012:
rich, stacked panels, no alternate screen); when stderr is not a TTY the
same lines render as plain tagged lines and the view holds no rich objects
at all. Verbosity/gating logic stays at the call sites — this is the
console layer only.
"""

import contextlib
import re
import sys
import threading
from pathlib import Path

_print_lock = threading.Lock()
_view = None  # the run's RunView while one is live (common.VERBOSITY precedent)


def set_view(view) -> None:
    global _view
    _view = view


def clear_view() -> None:
    global _view
    _view = None


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


class RunView:
    """One instance per run. TTY mode: rich Live with stacked panels
    (status top, then a background panel only while concurrent activity
    runs, log tail bottom) on stderr. Fallback mode (stderr not a
    TTY, including pytest): no rich objects; every method prints plain
    tagged lines. render() returns the current panel content as text
    without a terminal — the test seam."""

    LOG_TAIL = 8

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
        self._status: list[str] = []
        self._progress: str | None = None
        self._perf = None  # (text, hot segment strings)
        self._bg: dict[str, str] = {}  # concurrent-activity lines, keyed
        self._log: list[str] = []
        self._replay: list[str] = []  # TTY-only warn/fail replay buffer
        self._live = None
        if self.tty and live:
            from rich.console import Console
            from rich.live import Live

            self._console = Console(file=sys.stderr)
            self._live = Live(self._panels(), console=self._console,
                              refresh_per_second=4, transient=True)
            self._live.start()
            self._sampler = PerfSampler(self)
            self._sampler.start()

    def _panels(self):
        from rich.layout import Layout
        from rich.panel import Panel
        from rich.text import Text

        status_lines = list(self._status)
        if self._progress:
            status_lines.append(self._progress)
        n_status = len(status_lines)
        status = Text("\n".join(status_lines))
        if self._perf:
            text, hot = self._perf
            perf = Text()
            for i, seg in enumerate(text.split(" \u00b7 ")):
                if i:
                    perf.append(" \u00b7 ")
                perf.append(seg, style="bold red" if seg in hot else None)
            status.append("\n")
            status.append(perf)
            n_status += 1
        log = Text("\n".join(self._log))
        rows = [Layout(Panel(status, title="status"),
                       size=max(n_status, 1) + 2)]
        if self._bg:  # the background panel exists only while something runs concurrently
            bg = Text("\n".join(f"{k}  {self._bg[k]}" for k in sorted(self._bg)))
            rows.append(Layout(Panel(bg, title="background"),
                               size=len(self._bg) + 2))
        rows.append(Layout(Panel(log, title="log")))
        layout = Layout()
        layout.split_column(*rows)
        return layout

    def _refresh(self) -> None:
        if self._live is not None and not self._closed:
            self._live.update(self._panels())

    def status(self, *lines: str) -> None:
        """Set the status panel's lines; refresh."""
        with _print_lock:
            self._status = [str(x) for x in lines]
            self._refresh()

    def progress(self, pct, eta) -> None:
        """Encode progress; a None arg omits that field."""
        parts = []
        if pct is not None:
            parts.append(f"{pct}%")
        if eta is not None:
            parts.append(f"ETA {eta}")
        with _print_lock:
            self._progress = "  ".join(parts) if parts else None
            self._refresh()

    def perf(self, text: str, hot=set()) -> None:
        """Status panel bottom line (transcode only). No-op in fallback;
        an empty text drops the line."""
        if not self.tty:
            return
        with _print_lock:
            self._perf = (text, set(hot)) if text else None
            self._refresh()

    def bg(self, key: str, text: str) -> None:
        """One background-panel line per concurrent activity, keyed; the
        line updates in place. No-op in fallback mode (plain prints remain)."""
        if not self.tty:
            return
        with _print_lock:
            self._bg[key] = str(text)
            self._refresh()

    def bg_clear(self, key: str) -> None:
        """Remove a background line; no panel when no keys remain."""
        if not self.tty:
            return
        with _print_lock:
            self._bg.pop(key, None)
            self._refresh()

    def tool(self, name: str, line: str) -> None:
        """A tool line (makemkv/handbrake/ffmpeg/mkvmerge style); the panel
        gets it tool-prefixed, fallback stderr gets ``{name}  {line}``."""
        self._event(f"{name}  {line}")

    def event(self, kind: str, text: str) -> None:
        """An info/warn/fail event line; ui._print routes here in TTY mode."""
        if self.tty and kind in ("warn", "fail"):
            self._replay.append(f"{kind}  {text}")  # the tail clear wipes these
        self._event(f"{kind}  {text}")

    def _event(self, line: str) -> None:
        with _print_lock:
            self._log.append(line)
            del self._log[:-self.LOG_TAIL]
            if self.tty:
                self._refresh()
            else:
                print(line, file=sys.stderr)

    def suspend(self):
        """Pause the Live region while a prompt prints normally (scrollback);
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
                from rich.live import Live
                self._live = Live(self._panels(), console=self._console,
                                  refresh_per_second=4, transient=True)
                self._live.start()
        return _ctx()

    def close(self) -> None:
        """Stop the Live region; idempotent, safe from finally."""
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

    def render(self) -> str:
        """Current panel content as text, no terminal (deterministic)."""
        if self.tty:
            import io

            from rich.console import Console
            console = Console(record=True, file=io.StringIO(), width=80,
                               height=max(len(self._status) + (1 if self._progress else 0)
                                          + (1 if self._perf else 0)
                                          + len(self._bg) + len(self._log), 1) + 8)
            console.print(self._panels())
            return console.export_text()
        lines = list(self._status)
        if self._progress:
            lines.append(self._progress)
        return "\n".join(lines + self._log)


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
    # the lock must not span the view dispatch: _event takes it too, and a
    # plain Lock re-acquiring on the same thread deadlocks
    if _view is not None and _view.tty and not _view._closed:
        _view.event(kind, text)
        return
    with _print_lock:
        print(f"{kind}  {text}", file=sys.stderr)


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
    only view state under _print_lock; the refresher redraws."""

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
