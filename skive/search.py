"""Ripped search (CONTEXT.md): the on-disk answer to "have I already ripped
this?" — a name walk over every share root, no database, plus the exact-name
lookups the rip preflight consumes."""

import difflib
import os
import re
from pathlib import Path
from typing import NamedTuple

from rich.console import Console

from skive.common import ensure_mounted

console = Console()

STATES: tuple[tuple[str, str], ...] = (
    ("staging", "rip"),
    ("library", "library"),
    ("lossless", "lossless"),
    ("issues", "issues"),
)
"""(state, share dir) per CONTEXT.md, in report order."""

_SEASON_FILE = re.compile(r"_S(\d+)E\d")
_SEASON_DIR = re.compile(r"Season (\d+)")


def fmt_seasons(seasons: set[int]) -> str:
    """Season numbers as display text: sorted, zero spelled 'Specials'."""
    return ", ".join("Specials" if s == 0 else f"S{s:02d}"
                     for s in sorted(seasons))


def seasons_in(folder: Path) -> set[int]:
    """The season numbers one show folder carries: Season NN/Specials
    subfolders (library) and _S(\\d+)E episode filenames (everywhere)."""
    seasons: set[int] = set()
    for p in folder.rglob("*"):
        if p.is_dir():
            if p.name == "Specials":
                seasons.add(0)
            elif (m := _SEASON_DIR.fullmatch(p.name)):
                seasons.add(int(m.group(1)))
        elif p.suffix.lower() == ".mkv" and (m := _SEASON_FILE.search(p.stem)):
            seasons.add(int(m.group(1)))
    return seasons


def folder_states(display: str, videos_mnt: Path, kind: str) -> list[tuple[str, Path]]:
    """Every state whose <kind> dir holds an exact-name title folder.

    Args:
        display: Sanitized title name (Name (Year))
        videos_mnt: The mounted videos share root
        kind: "movies" or "tv"

    Returns:
        (state, folder) pairs in STATES order
    """
    low = display.casefold()
    rows: list[tuple[str, Path]] = []
    for state, rel in STATES:
        base = videos_mnt / rel / kind
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and child.name.casefold() == low:
                rows.append((state, child))
                break
    return rows


class Hit(NamedTuple):
    name: str
    kind: str  # "movie" | "tv"
    states: list[tuple[str, set[int]]]  # (state, seasons); seasons empty for movies


def _title_names(videos_mnt: Path) -> dict[str, str]:
    """casefold(name) -> name for every title folder on disk."""
    names: dict[str, str] = {}
    for _state, rel in STATES:
        for kind in ("movies", "tv"):
            base = videos_mnt / rel / kind
            if base.is_dir():
                for child in base.iterdir():
                    if child.is_dir():
                        names.setdefault(child.name.casefold(), child.name)
    return names


def find_hits(query: str, videos_mnt: Path) -> list[Hit]:
    """Every on-disk title whose folder name contains the query
    (case-insensitive), merged across states, sorted by name."""
    low = query.casefold()
    found: dict[tuple[str, str], dict[str, Path]] = {}
    for state, rel in STATES:
        for kind in ("movies", "tv"):
            base = videos_mnt / rel / kind
            if not base.is_dir():
                continue
            for child in base.iterdir():
                if child.is_dir() and low in child.name.casefold():
                    found.setdefault((child.name, kind), {})[state] = child
    return [Hit(name, "movie" if kind == "movies" else kind,
                [(state, seasons_in(f) if kind == "tv" else set())
                 for state, f in folders.items()])
            for (name, kind), folders in sorted(found.items())]


def suggestions(query: str, videos_mnt: Path) -> list[str]:
    """Near-miss title names for a zero-hit query — difflib, three at most."""
    names = _title_names(videos_mnt)
    return [names[m] for m in difflib.get_close_matches(
        query.casefold(), list(names), n=3, cutoff=0.6)]


def run_search(args) -> None:
    """The `skive search QUERY` entry: mount, walk, report — exit 0 always."""
    videos_mnt = os.environ.get("VIDEOS_MOUNT", "/mnt/videos")
    ensure_mounted(videos_mnt, os.environ.get("NFS_VIDEOS", ""))
    hits = find_hits(args.query, Path(videos_mnt))
    if not hits:
        near = suggestions(args.query, Path(videos_mnt))
        console.print("no hits" + (f" — did you mean: {', '.join(near)}?" if near else ""))
        return
    for hit in hits:
        console.print(f"[bold]{hit.name}[/bold] — {hit.kind}")
        for state, seasons in hit.states:
            label = f"{state}   {fmt_seasons(seasons)}" if hit.kind == "tv" and seasons else state
            console.print(f"  {label}")
