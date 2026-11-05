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

import re
import sys
import threading

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


class RunView:
    """One instance per run. TTY mode: rich Live with two stacked panels
    (status top, log tail bottom) on stderr. Fallback mode (stderr not a
    TTY, including pytest): no rich objects; every method prints plain
    tagged lines. render() returns the current panel content as text
    without a terminal — the test seam."""

    LOG_TAIL = 8

    def __init__(self):
        stream = sys.stderr
        try:
            self.tty = bool(stream is not None and stream.isatty())
        except ValueError:  # closed stream
            self.tty = False
        self._closed = False
        self._status: list[str] = []
        self._progress: str | None = None
        self._log: list[str] = []
        self._replay: list[str] = []  # TTY-only warn/fail replay buffer
        if self.tty:
            from rich.console import Console
            from rich.live import Live

            self._console = Console(file=sys.stderr)
            self._live = Live(self._panels(), console=self._console,
                              refresh_per_second=4, transient=True)
            self._live.start()

    def _panels(self):
        from rich.layout import Layout
        from rich.panel import Panel
        from rich.text import Text

        status_lines = list(self._status)
        if self._progress:
            status_lines.append(self._progress)
        status = Text("\n".join(status_lines))
        log = Text("\n".join(self._log))
        layout = Layout()
        layout.split_column(
            Layout(Panel(status, title="status"),
                   size=max(len(status_lines), 1) + 2),
            Layout(Panel(log, title="log")),
        )
        return layout

    def _refresh(self) -> None:
        if self.tty and not self._closed:
            self._live.update(self._panels())

    def status(self, *lines: str) -> None:
        """Set the status panel's lines; refresh."""
        self._status = [str(x) for x in lines]
        self._refresh()

    def progress(self, pct, eta) -> None:
        """Encode progress; a None arg omits that field."""
        parts = []
        if pct is not None:
            parts.append(f"{pct}%")
        if eta is not None:
            parts.append(f"ETA {eta}")
        self._progress = "  ".join(parts) if parts else None
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
        self._log.append(line)
        del self._log[:-self.LOG_TAIL]
        if self.tty:
            self._refresh()
        else:
            print(line, file=sys.stderr)

    def close(self) -> None:
        """Stop the Live region; idempotent, safe from finally."""
        if self.tty and not self._closed:
            self._closed = True
            self._live.stop()

    def replay_lines(self) -> list[str]:
        """Every warn/fail line rendered this run, in order (TTY only; the
        fallback already has them in the scroll)."""
        return list(self._replay)

    def render(self) -> str:
        """Current panel content as text, no terminal (deterministic)."""
        if self.tty:
            import io

            from rich.console import Console
            console = Console(record=True, file=io.StringIO(), width=80)
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


def run_summary(rows, n_ok: int, n_failed: int, keep_banner: str | None,
                replay=()) -> None:
    """End-of-run summary on stderr (after the run view closed): warn/fail
    lines replayed (TTY only), one table of per-item phase timings, the
    ok/failed count, and the --keep banner only when keep_banner is set."""
    with _print_lock:
        for line in replay:
            print(line, file=sys.stderr)
        from rich.console import Console
        from rich.table import Table
        console = Console(file=sys.stderr)
        table = Table(box=None)  # plain ASCII under pytest's non-TTY stderr
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
    with _print_lock:
        if _view is not None and _view.tty:
            _view.event(kind, text)
        else:
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
