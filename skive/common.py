"""Common helpers: title-spec parser, NFS mount-on-demand, JSONL run/raw logs,
the shared interrupt (Ctrl+C) machinery."""

import json
import os
import re
import signal
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from skive import ui

# Sentinel for bare -T/--titles (flag with no value) -> open the interactive
# title menu. Lives here so main.py and makemkv.py can both import it without
# a cycle.
MENU = object()

# Console verbosity: 0 = skive's own lines only, 1 = -v adds tool messages
# worth babysitting, 2 = -vv raw passthrough. Module-level so the print sites
# in makemkv.py/rip_music.py read it without threading a parameter through
# every rip call site; main.py sets it once per run. The run and raw logs are
# always written regardless of level.
VERBOSITY = 0


def set_verbosity(level: int) -> None:
    """Store the console verbosity, clamped to 0/1/2 (absent/-v/-vv)."""
    global VERBOSITY
    VERBOSITY = max(0, min(int(level), 2))


# -- Interrupt (Ctrl+C), CONTEXT.md glossary "Interrupt": the first SIGINT
# becomes a clean exception path (child signalled, then killed after a grace
# window, partial deleted, run log says interrupted, exit 130); a second SIGINT
# restores the default disposition and re-raises, so the process dies at once
# with no cleanup and no traceback. windows/transcode.py carries its own copy
# (standalone file, Windows kill semantics).

# Seconds a signalled child gets to exit before it is killed.
INTERRUPT_GRACE_S = 5


class Interrupted(KeyboardInterrupt):
    """The first Ctrl+C, raised by the installed SIGINT handler.

    Subclasses KeyboardInterrupt so existing `except KeyboardInterrupt` sites
    (sync_sd, the rip commands' prompt paths) keep catching it."""


_sigint_count = 0


def _sigint_handler(signum, frame) -> None:
    """First Ctrl+C -> Interrupted; second Ctrl+C -> instant default death."""
    global _sigint_count
    _sigint_count += 1
    if _sigint_count > 1:
        _exit_immediately()
        return
    raise Interrupted()


def _exit_immediately() -> None:
    """Second Ctrl+C: default disposition + re-raise — no cleanup, no
    traceback; the next run's junk purge is the safety net. os._exit(130) is
    the belt-and-braces for platforms where raise_signal returns."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.raise_signal(signal.SIGINT)
    os._exit(130)


def install_interrupt_handler() -> None:
    """Install the interrupt handler and reset the second-SIGINT counter.

    Called once per invocation from main(); the rip commands' contract runs on
    the Interrupted exception, so tests may drive it directly without signals."""
    global _sigint_count
    _sigint_count = 0
    signal.signal(signal.SIGINT, _sigint_handler)


def kill_child_after_grace(proc: subprocess.Popen,
                           grace: float = INTERRUPT_GRACE_S) -> None:
    """Interrupt a running tool child: SIGINT it, wait up to grace seconds,
    then kill and reap (CONTEXT.md: Interrupt). A child that already exited —
    or that exits inside the grace window — is only reaped."""
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _append_jsonl(path: Path, obj: dict) -> None:
    """Append one object as a JSONL line.

    Open-append-close per line — the append/flush discipline the parallel-drive
    text rip.log already used — so concurrent runs (and the Windows box over
    NFS) interleave whole lines instead of clobbering each other.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def sanitize(title: str) -> str:
    """Strip Windows/SMB-hostile characters from a title used as a filename.

    ':' and friends are legal on Linux, but SMB surfaces them as 8.3 short
    names (P3TGB9~G.MKV) that transcode then skips as junk. '/' becomes '-'
    because it is a path separator on every OS. windows/transcode.py's copy
    DELETES '/' instead of mapping it — benign, because skive mints every
    staged name first, so a '/' already arrived as '-' before transcode ever
    parses one; the two never disagree about a name on disk.
    """
    return re.sub(r'[:*?"<>|]', "", title.replace("/", "-"))


class RipLog:
    """Writes the two JSONL logs (CONTEXT.md: Run log, Raw log) and the
    per-run scan dump (CONTEXT.md: Scan dump).

    Run log  <root>/logs/rip.jsonl — one
    {ts, run, script, software, name, phase, status, elapsed_s} object per
    line, appended by both machines over NFS. status carries "started",
    "ok", "FAIL(rc)", "interrupted", or note text.
    Raw log  <root>/logs/raw/<run>.jsonl — one {ts, run, tool, line} object
    per tool output line; finish(0) deletes it, failure or interrupt keeps it.
    Scan dump  <root>/logs/scan/<run>_<label>.txt — one full scan's verbatim
    output; never deleted (scan_dump).

    This class only holds what plain file appends do not: the run/phase clock
    readings, the phase count, and the total-on-exit rule.
    """

    def __init__(self, script, software, name="", root=None, now=None):
        self._now = now or datetime.now
        self.name = name  # mutable: callers assign it mid-run (e.g. the title)
        # short UUID minted per invocation; names this run's raw log
        self.run = uuid.uuid4().hex[:8]
        mount = Path(root) if root is not None else Path(
            os.environ.get("VIDEOS_MOUNT", "/mnt/videos"))
        self._run_log = mount / "logs" / "rip.jsonl"
        self._raw_log = mount / "logs" / "raw" / f"{self.run}.jsonl"
        self._script = script
        self._software = software
        self._start = None
        self._phases = 0
        self._phase_name = None
        self._phase_start = None
        self._interrupted = False
        self._keep_raw = False

    def _emit(self, now, phase, status, elapsed_s):
        _append_jsonl(self._run_log, {
            "ts": now.isoformat(timespec="seconds"),
            "run": self.run,
            "script": self._script,
            "software": self._software,
            "name": self.name,
            "phase": phase,
            "status": status,
            "elapsed_s": elapsed_s,
        })

    def raw(self, tool, line):
        """One verbatim tool output line into this run's raw log."""
        _append_jsonl(self._raw_log, {
            "ts": self._now().isoformat(timespec="seconds"),
            "run": self.run,
            "tool": tool,
            "line": line,
        })

    def scan_dump(self, label: str, out: str) -> None:
        """Persist one full scan's verbatim output to <root>/logs/scan/.

        Never deleted — unlike the raw log, the dump is written before anyone
        knows a run will go wrong, so a table questioned later (often when the
        disc is already out of the drive) still replays via
        MAKEMKV_INFO="cat <dump>". A re-scan of the same disc overwrites it.
        """
        scan_dir = self._raw_log.parent.parent / "scan"
        scan_dir.mkdir(parents=True, exist_ok=True)
        name = f"{self.run}_{sanitize(label)}.txt" if label else f"{self.run}_disc.txt"
        (scan_dir / name).write_text(out, encoding="utf-8")

    def start(self):
        """Log the immediate 'started' marker line and open the raw log."""
        self._raw_log.parent.mkdir(parents=True, exist_ok=True)
        self._raw_log.touch()
        now = self._now()
        self._start = now
        self._emit(now, "-", "started", 0)

    def phase(self, name):
        """Begin a named phase."""
        self._phase_name = name
        self._phase_start = self._now()

    def phase_end(self, status="ok") -> int:
        """Log the just-finished phase; status may carry the phase's numbers
        (transcode parts log throughput, e.g. "5744 MB at 64 MB/s"). Returns
        the phase's elapsed seconds. A phase already closed (or never opened)
        logs nothing — a later fail_open_phase() on a closed phase is a no-op."""
        if self._phase_name is None:
            return 0
        name = self._phase_name
        now = self._now()
        elapsed = int((now - self._phase_start).total_seconds())
        self._phases += 1
        self._phase_name = None
        self._emit(now, name, status, elapsed)
        return elapsed

    def fail_open_phase(self, status="FAIL"):
        """Close a phase left open by an exception (transcode's FATAL and
        interrupt arms): emit its elapsed under the given status (FAIL, or
        "interrupted" on the Ctrl+C path), then clear it. Does not count as a
        successful phase — the run is failing and a total line follows."""
        if self._phase_name is None:
            return
        name = self._phase_name
        now = self._now()
        self._phase_name = None
        self._emit(now, name, status,
                   int((now - self._phase_start).total_seconds()))

    def note(self, phase, text, echo=True):
        """Log an untimed note line (elapsed_s 0); tees to stderr unless the
        caller prints its own screen line (echo=False)."""
        self._emit(self._now(), phase, text, 0)
        if echo:
            ui.info(f"{phase}: {text}")

    def interrupted(self):
        """Log the run's closing line with status=interrupted (elapsed_s from
        start, phase "total" — the usual total shape). Keeps the raw log: only
        finish(0) deletes it. A later finish() stays silent — the interrupt
        line IS the total — so interrupt exits inside a try/finally never
        double-log (CONTEXT.md: Interrupt)."""
        if self._interrupted:
            return
        self._interrupted = True
        now = self._now()
        self._emit(now, "total", "interrupted",
                   int((now - self._start).total_seconds()))

    def keep_raw(self) -> None:
        """Keep this run's raw log even when the caller exits cleanly.

        Some callers have a successful tool exit but still need the transcript
        for an outcome that needs investigation (for example, albums left in
        staging).  The default remains the historical clean-run deletion.
        """
        self._keep_raw = True

    def finish(self, rc):
        """Log the total line, then drop the raw log on a clean exit unless
        the caller explicitly retained it.

        The total line is skipped when rc==0 and exactly one phase succeeded —
        that lone phase line already records the run — and after interrupted(),
        which already wrote this run's total. The raw log is deleted on rc==0
        and kept on failure or interrupt.
        """
        if self._interrupted:
            return
        # a lone successful phase already logged its line — the total
        # would duplicate it. skip it.
        if rc != 0 or self._phases != 1:
            now = self._now()
            status = "ok" if rc == 0 else f"FAIL({rc})"
            self._emit(now, "total", status,
                       int((now - self._start).total_seconds()))
        if rc == 0 and not self._keep_raw:
            self._raw_log.unlink(missing_ok=True)


def ensure_mounted(mnt: str, export: str) -> None:
    """Mount an NFS export on demand, never unmount.

    Args:
        mnt: Mountpoint path (e.g., /mnt/videos)
        export: NFS export (e.g., host:/path)

    Raises:
        SystemExit: If mount fails or export is empty
    """
    # Check if already mounted by parsing /proc/mounts
    for line in Path("/proc/mounts").read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == mnt:
            return  # Already mounted

    if not export:
        ui.fail(f"{mnt} not mounted and no export given (set NFS_MUSIC/NFS_VIDEOS?)")
        sys.exit(1)

    Path(mnt).mkdir(parents=True, exist_ok=True)
    if subprocess.run(["sudo", "mount", "-t", "nfs", export, mnt]).returncode != 0:
        ui.fail(f"mount {export} -> {mnt} failed (export unreachable, or no sudo / nfs client on the host?)")
        sys.exit(1)


def parse_titles(spec: str) -> list[int] | None:
    """Parse a title specification.

    Returns None for 'all' (or empty), otherwise a sorted list of ints.

    Grammar: all | N | N-M | comma list mixing both (0,5-7)

    Args:
        spec: Title specification string

    Raises:
        ValueError: If spec is invalid (disc:N, garbage, etc.)
    """
    spec = spec.strip()
    if not spec or spec == "all":
        return None

    if spec.startswith("disc:"):
        raise ValueError(f"disc:N does not belong in a title spec; use --disc N instead of '{spec}'")

    titles: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            # Range N-M
            try:
                start, end = part.split("-", 1)
                start_int = int(start.strip())
                end_int = int(end.strip())
            except ValueError as e:
                raise ValueError(f"invalid range '{part}': {e}")
            if start_int > end_int:
                raise ValueError(f"invalid range {part}: start > end")
            titles.update(range(start_int, end_int + 1))
        else:
            # Single N
            try:
                titles.add(int(part))
            except ValueError:
                raise ValueError(f"invalid title number '{part}'; use digits only")

    return sorted(titles)