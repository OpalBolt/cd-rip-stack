"""Common helpers: title-spec parser, NFS mount-on-demand, JSONL run/raw logs."""

import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

# Sentinel for bare -T/--titles (flag with no value) -> open the interactive
# title menu. Lives here so main.py and makemkv.py can both import it without
# a cycle.
MENU = object()


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
    because it is a path separator on every OS. Matches windows/transcode.py.
    """
    return re.sub(r'[:*?"<>|]', "", title.replace("/", "-"))


class RipLog:
    """Writes the two JSONL logs (CONTEXT.md: Run log, Raw log).

    Run log  <root>/logs/rip.jsonl — one
    {ts, run, script, software, name, phase, status, elapsed_s} object per
    line, appended by both machines over NFS. status carries "started",
    "ok", "FAIL(rc)", "interrupted", or note text.
    Raw log  <root>/logs/raw/<run>.jsonl — one {ts, run, tool, line} object
    per tool output line; finish(0) deletes it, failure or interrupt keeps it.

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

    def start(self):
        """Log the immediate 'started' marker line."""
        now = self._now()
        self._start = now
        self._emit(now, "-", "started", 0)

    def phase(self, name):
        """Begin a named phase."""
        self._phase_name = name
        self._phase_start = self._now()

    def phase_end(self):
        """Log the just-finished phase."""
        now = self._now()
        self._phases += 1
        self._emit(now, self._phase_name, "ok",
                   int((now - self._phase_start).total_seconds()))

    def note(self, phase, text):
        """Log an untimed note line (elapsed_s 0); tees to stderr."""
        self._emit(self._now(), phase, text, 0)
        print(f"skive: {phase}: {text}", file=sys.stderr)

    def finish(self, rc):
        """Log the total line, then drop the raw log on a clean exit.

        The total line is skipped when rc==0 and exactly one phase succeeded —
        that lone phase line already records the run. The raw log is deleted on
        rc==0 and kept on failure or interrupt (later slices record
        status=interrupted).
        """
        # a lone successful phase already logged its line — the total
        # would duplicate it. skip it.
        if rc != 0 or self._phases != 1:
            now = self._now()
            status = "ok" if rc == 0 else f"FAIL({rc})"
            self._emit(now, "total", status,
                       int((now - self._start).total_seconds()))
        if rc == 0:
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
        print(f"ensure_mounted: {mnt} not mounted and no export given (set NFS_MUSIC/NFS_VIDEOS?)", file=sys.stderr)
        sys.exit(1)

    Path(mnt).mkdir(parents=True, exist_ok=True)
    if subprocess.run(["sudo", "mount", "-t", "nfs", export, mnt]).returncode != 0:
        print(f"ensure_mounted: mount {export} -> {mnt} failed (export unreachable, or no sudo / nfs client on the host?)", file=sys.stderr)
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