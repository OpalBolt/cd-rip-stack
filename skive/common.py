"""Common helpers ported from the bash helpers."""

import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from loguru import logger

# Drop loguru's default stderr sink: 8-col rip.log lines belong only in their
# per-instance file sink. Notes tee to stderr through the shared WARNING sink
# (callable sink resolves sys.stderr at write time, so capsys/redirects work).
logger.remove()
logger.add(lambda m: sys.stderr.write(m), format="{message}",
           filter=lambda r: r["level"].name == "WARNING")


class RipLog:
    """8-column rip.log: start end script software name phase m:ss status

    Emitted through loguru: each instance gets its own tagged file sink
    (format="{message}" keeps the bytes identical), notes also tee to stderr.
    """

    def __init__(self, script: str, software: str, name: str = "", path: str = "rip.log", now=None):
        self.script = script
        self.software = software
        self.name = name
        self.path = Path(path)
        self._now = now or datetime.now
        self._tag = uuid.uuid4().hex
        self._log = logger.bind(riplog=self._tag)
        # per-instance file sink; the tag filter keeps parallel instances apart
        logger.add(
            self.path,
            format="{message}",
            encoding="utf-8",
            delay=True,
            filter=lambda record: record["extra"].get("riplog") == self._tag,
        )
        self._start = None
        self._start_ts = None
        self._phases = 0
        self._phase_start = None
        self._phase_name = None
        self._phase_start_ts = None

    def _emit(self, start_ts: str, end_ts: str, phase: str, minutes: int, seconds: int, status: str) -> None:
        line = f"{start_ts}  {end_ts}  {self.script}  {self.software}  {self.name}  {phase}  {minutes}m{seconds:02d}  {status}"
        self._log.info(line)

    def start(self) -> None:
        """Log the immediate 'started' marker line."""
        self._start = self._now()
        self._start_ts = self._start.strftime("%F %T")
        self._emit(self._start_ts, "-", "-", 0, 0, "started")

    def phase(self, name: str) -> None:
        """Begin a named phase."""
        self._phase_name = name
        self._phase_start = self._now()
        self._phase_start_ts = self._phase_start.strftime("%F %T")

    def phase_end(self) -> None:
        """Log the just-finished phase."""
        end_ts = self._now()
        elapsed = int((end_ts - self._phase_start).total_seconds())
        self._phases += 1
        self._emit(self._phase_start_ts, end_ts.strftime("%F %T"), self._phase_name,
                  elapsed // 60, elapsed % 60, "ok")

    def note(self, phase: str, text: str) -> None:
        """Log an untimed note line (end and m:ss are '-'); tees to stderr."""
        ts = self._now().strftime("%F %T")
        line = f"{ts}  -  {self.script}  {self.software}  {self.name}  {phase}  -  {text}"
        self._log.warning(line)

    def finish(self, rc: int) -> None:
        """Log the total line, skipped when rc==0 and exactly one phase succeeded."""
        # one phase that succeeded already logged start..end..status — the total would
        # duplicate it. skip it.
        if rc == 0 and self._phases == 1:
            return
        end_ts = self._now()
        elapsed = int((end_ts - self._start).total_seconds())
        status = "ok" if rc == 0 else f"FAIL({rc})"
        self._emit(self._start_ts, end_ts.strftime("%F %T"), "total",
                  elapsed // 60, elapsed % 60, status)


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
        raise ValueError(f"disc:N is not supported; use the default disc:0 instead of '{spec}'")

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
                if start_int > end_int:
                    raise ValueError(f"invalid range {part}: start > end")
                titles.update(range(start_int, end_int + 1))
            except ValueError as e:
                raise ValueError(f"invalid range '{part}': {e}")
        else:
            # Single N
            try:
                titles.add(int(part))
            except ValueError:
                raise ValueError(f"invalid title number '{part}'; use digits only")

    return sorted(titles)