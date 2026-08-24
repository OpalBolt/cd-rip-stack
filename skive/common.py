"""Common helpers: title-spec parser, NFS mount-on-demand, rip.log via loguru."""

import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from loguru import logger

# The 8-column rip.log contract, expressed as a loguru format string. loguru
# lays out the columns (double-space separated); callers only supply values.
_FMT = ("{extra[start]}  {extra[end]}  {extra[script]}  {extra[software]}  "
        "{extra[name]}  {extra[phase]}  {extra[elapsed]}  {extra[status]}")

# One logger, two sinks: INFO lines go to the per-instance rip.log file (added
# by RipLog), WARNING lines (notes) also tee to stderr. The stderr sink is a
# callable so sys.stderr is resolved at write time — capsys and other
# redirects still capture the tee.
logger.remove()
logger.add(lambda m: sys.stderr.write(str(m)), format=_FMT, level="WARNING",
           filter=lambda r: "riplog" in r["extra"])


def _elapsed(seconds: int) -> str:
    """The m:ss column, e.g. 270 -> '4m30'."""
    return f"{seconds // 60}m{seconds % 60:02d}"


class RipLog:
    """Writes the 8-column rip.log.

    loguru owns the layout (_FMT) and the routing (INFO -> file, WARNING ->
    file + stderr). This class only holds what loguru does not: the run/phase
    clock readings, the phase count, and the total-on-exit rule.
    """

    def __init__(self, script, software, name="", path="rip.log", now=None):
        self._now = now or datetime.now
        self.name = name  # mutable: callers assign it mid-run (e.g. the title)
        self._tag = uuid.uuid4().hex
        self._log = logger.bind(riplog=self._tag, script=script, software=software)
        # per-instance file sink; the tag keeps parallel instances from crossing
        logger.add(str(path), format=_FMT, encoding="utf-8", level="INFO",
                   filter=lambda r: r["extra"].get("riplog") == self._tag)
        self._start = self._start_ts = None
        self._phases = 0
        self._phase_name = None
        self._phase_start = self._phase_start_ts = None

    def _emit(self, start, end, phase, elapsed, status):
        self._log.bind(name=self.name, start=start, end=end, phase=phase,
                       elapsed=elapsed, status=status).info("")

    def start(self):
        """Log the immediate 'started' marker line."""
        self._start = self._now()
        self._start_ts = self._start.strftime("%F %T")
        self._emit(self._start_ts, "-", "-", "0m00", "started")

    def phase(self, name):
        """Begin a named phase."""
        self._phase_name = name
        self._phase_start = self._now()
        self._phase_start_ts = self._phase_start.strftime("%F %T")

    def phase_end(self):
        """Log the just-finished phase."""
        end = self._now()
        self._phases += 1
        self._emit(self._phase_start_ts, end.strftime("%F %T"), self._phase_name,
                   _elapsed(int((end - self._phase_start).total_seconds())), "ok")

    def note(self, phase, text):
        """Log an untimed note line (end and m:ss are '-'); tees to stderr."""
        self._log.bind(name=self.name, start=self._now().strftime("%F %T"),
                       end="-", phase=phase, elapsed="-", status=text).warning("")

    def finish(self, rc):
        """Log the total line, skipped when rc==0 and exactly one phase succeeded."""
        # a lone successful phase already logged start..end..status — the total
        # would duplicate it. skip it.
        if rc == 0 and self._phases == 1:
            return
        end = self._now()
        status = "ok" if rc == 0 else f"FAIL({rc})"
        self._emit(self._start_ts, end.strftime("%F %T"), "total",
                   _elapsed(int((end - self._start).total_seconds())), status)


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