"""Beets import on the music rip directory."""

import os
import sys
from pathlib import Path

from skive.common import ensure_mounted


def tag(args) -> None:
    """Run beets import on /mnt/music/rip/, moving into the library.

    Args:
        args: argparse.Namespace (unused)

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

    rip_dir = Path(music_mnt) / "rip"
    if not rip_dir.exists():
        print("skive: nothing to import (run 'skive rip music' first)", file=sys.stderr)
        sys.exit(0)

    # Run beets from this venv
    rc = os.execvp(sys.executable, [sys.executable, "-m", "beets", "-c", str(config_path), "import", str(rip_dir)])
    # execvp never returns