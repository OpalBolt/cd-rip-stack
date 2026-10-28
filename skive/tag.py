"""Run beets over staged music sources and record filing outcomes."""

import errno
import os
import selectors
import subprocess
import sys
import pty
from pathlib import Path

from skive.common import RipLog, ensure_mounted, kill_child_after_grace

SOURCES = ("cd", "other", "bandcamp")
AUDIO_EXTENSIONS = frozenset({
    ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aiff", ".alac", ".wma",
})


def _is_empty_dir(path: Path) -> bool:
    """Return True if path doesn't exist or contains no entries."""
    if not path.is_dir():
        return True
    try:
        next(path.iterdir())
        return False
    except StopIteration:
        return True


def _album_folders(source_dir: Path) -> list[Path]:
    """List the album directories present immediately below a source."""
    if not source_dir.is_dir():
        return []
    return sorted((entry for entry in source_dir.iterdir() if entry.is_dir()),
                  key=lambda entry: entry.name)


def _has_audio(folder: Path) -> bool:
    """Whether *folder* still contains a supported audio file."""
    if not folder.is_dir():
        return False
    return any(path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
               for path in folder.rglob("*"))


def _log_album_outcomes(log: RipLog, source: str, source_dir: Path,
                        folders: list[Path]) -> bool:
    """List the source again and record filed/left for prior album folders."""
    after = {folder.name: folder for folder in _album_folders(source_dir)}
    any_left = False
    for folder in folders:
        remaining = after.get(folder.name)
        status = "filed" if remaining is None or not _has_audio(remaining) else "left"
        if status == "left":
            any_left = True
        # RipLog's name field identifies the album for this note.
        log.name = folder.name
        log.note(source, status)
    return any_left


def _record_output(log: RipLog, output, tool: str = "beets") -> None:
    """Write output returned by an injected runner to the raw log."""
    if not output:
        return
    if isinstance(output, bytes):
        output = output.decode(errors="replace")
    for line in str(output).splitlines():
        log.raw(tool, line.rstrip("\r"))


def _run_beets_pty(cmd: list[str], log: RipLog) -> int:
    """Run beets on a pseudo-terminal, relaying its session to the console.

    A pty is required rather than pipes because beets prompts based on whether
    it has a terminal.  The runner is deliberately separate so unit tests can
    inject a fake beets process without requiring a terminal or beets itself.
    """
    master, slave = pty.openpty()
    process = None
    selector = selectors.DefaultSelector()
    stdin_fd = None
    try:
        process = subprocess.Popen(
            cmd, stdin=slave, stdout=slave, stderr=slave, close_fds=True,
        )
        os.close(slave)
        slave = None

        try:
            stdin_fd = sys.stdin.fileno()
            if os.isatty(stdin_fd):
                selector.register(stdin_fd, selectors.EVENT_READ, "stdin")
        except (AttributeError, OSError):
            stdin_fd = None
        selector.register(master, selectors.EVENT_READ, "master")

        pending = b""
        while True:
            events = selector.select(timeout=0.1)
            for key, _ in events:
                if key.data == "stdin":
                    try:
                        data = os.read(stdin_fd, 4096)
                    except OSError:
                        data = b""
                    if data:
                        os.write(master, data)
                    else:
                        selector.unregister(stdin_fd)
                else:
                    try:
                        data = os.read(master, 4096)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            data = b""
                        else:
                            raise
                    if data:
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                        pending += data
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            log.raw("beets", line.decode(errors="replace").rstrip("\r"))
                    else:
                        try:
                            selector.unregister(master)
                        except KeyError:
                            pass
            if process.poll() is not None and not selector.get_map().get(master):
                break

        if pending:
            log.raw("beets", pending.decode(errors="replace").rstrip("\r"))
        return process.wait()
    except KeyboardInterrupt:
        if process is not None:
            kill_child_after_grace(process)
        raise
    finally:
        selector.close()
        if slave is not None:
            os.close(slave)
        os.close(master)


def _run_beets(cmd: list[str], log: RipLog, run=None) -> int:
    """Run beets, using an injected non-pty runner for tests."""
    if run is None:
        return _run_beets_pty(cmd, log)
    result = run(cmd)
    _record_output(log, getattr(result, "stdout", None))
    _record_output(log, getattr(result, "stderr", None))
    if isinstance(result, int):
        return result
    return result.returncode if hasattr(result, "returncode") else 0


def tag(args, run=None) -> None:
    """File selected music sources and record each album's outcome.

    ``run`` is an injectable subprocess seam for tests.  The normal path uses
    a pty so beets retains its interactive prompts while its output is logged.
    """
    nfs_music = os.environ.get("NFS_MUSIC", "")
    music_mnt = os.environ.get("MUSIC_MOUNT", "/mnt/music")
    ensure_mounted(music_mnt, nfs_music)

    config_path = Path(__file__).parent.parent / "config" / "beets-config.yaml"
    if not config_path.exists():
        print(f"skive: beets config not found at {config_path}", file=sys.stderr)
        sys.exit(1)

    sources = [args.source] if getattr(args, "source", None) else list(SOURCES)
    rip_dir = Path(music_mnt) / "rip"
    active_sources = [source for source in sources
                      if not _is_empty_dir(rip_dir / source)]

    if not active_sources:
        print("skive: nothing to import", file=sys.stderr)
        # An empty but mounted share is still a tag run and gets a run marker.
        # The is_dir guard keeps the command's empty-source behavior useful in
        # setup/tests where the configured mountpoint does not exist.
        if Path(music_mnt).is_dir():
            empty_log = RipLog("skive tag", "beets", root=music_mnt)
            empty_log.start()
            empty_log.finish(0)
        return

    log = RipLog("skive tag", "beets", root=music_mnt)
    log.start()
    rc = 0
    any_left = False
    try:
        for source in active_sources:
            source_dir = rip_dir / source
            before = _album_folders(source_dir)
            log.phase(source)
            cmd = [sys.executable, "-m", "beets", "-c", str(config_path), "import"]
            if source == "bandcamp":
                cmd.append("-A")
            cmd.append(str(source_dir))
            try:
                source_rc = _run_beets(cmd, log, run=run)
            except KeyboardInterrupt:
                any_left = _log_album_outcomes(log, source, source_dir, before) or any_left
                raise

            any_left = _log_album_outcomes(log, source, source_dir, before) or any_left
            log.phase_end()
            if source_rc != 0:
                rc = source_rc
                break
    except KeyboardInterrupt:
        rc = 130
        log.interrupted()
        print("skive: interrupted", file=sys.stderr)
    finally:
        if any_left:
            log.keep_raw()
        log.finish(rc)

    if rc != 0:
        sys.exit(rc)
