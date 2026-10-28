"""Beets import on the music rip directory."""

import os
import subprocess
import sys
from pathlib import Path

from skive.common import ensure_mounted

SOURCES = ("cd", "other", "bandcamp")


def _is_empty_dir(path: Path) -> bool:
    """Return True if path doesn't exist or contains no entries."""
    if not path.is_dir():
        return True
    try:
        next(path.iterdir())
        return False
    except StopIteration:
        return True


def tag(args, run=subprocess.run) -> None:
    """Run beets import on /mnt/music/rip/<source>/, moving into the library.

    Args:
        args: argparse.Namespace with optional `source`
        run: callable for invoking child processes (subprocess.run by default)

    Raises:
        SystemExit: On any error
    """
    # Environment
    nfs_music = os.environ.get("NFS_MUSIC", "")
    music_mnt = os.environ.get("MUSIC_MOUNT", "/mnt/music")

    # Mount
    ensure_mounted(music_mnt, nfs_music)

    # Config path: skive/config/beets-config.yaml (works from repo and editable install)
    config_path = Path(__file__).parent.parent / "config" / "beets-config.yaml"
    if not config_path.exists():
        print(f"skive: beets config not found at {config_path}", file=sys.stderr)
        sys.exit(1)

    sources = [args.source] if getattr(args, "source", None) else list(SOURCES)

    rip_dir = Path(music_mnt) / "rip"
    active_sources = [s for s in sources if not _is_empty_dir(rip_dir / s)]

    if not active_sources:
        print("skive: nothing to import", file=sys.stderr)
        sys.exit(0)

    for source in active_sources:
        source_dir = rip_dir / source
        cmd = [sys.executable, "-m", "beets", "-c", str(config_path), "import"]
        if source == "bandcamp":
            cmd.append("-A")
        cmd.append(str(source_dir))

        res = run(cmd)
        rc = res.returncode if hasattr(res, "returncode") else (res if isinstance(res, int) else 0)
        if rc != 0:
            sys.exit(rc)