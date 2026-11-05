"""Event lines: everything skive prints about what it is doing.

info/warn/fail render one plain tagged line per call on stderr
(``info  text``); stdout carries only data answers. A module-level
lock guarantees whole lines across threads.
"""

import sys
import threading

_print_lock = threading.Lock()


def _print(kind: str, text: str) -> None:
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
