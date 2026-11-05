"""Ripped search (CONTEXT.md): the on-disk answer to "have I already ripped
this?" — a name walk over every share root, no database, plus the exact-name
lookups the rip preflight consumes."""

import difflib
import io
import os
import re
from contextlib import redirect_stderr
from pathlib import Path
from typing import NamedTuple

from rich.console import Console

from skive.common import ensure_mounted
from skive import ui
from skive.tag import SOURCES

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


class AlbumHit(NamedTuple):
    name: str
    kind: str
    states: list[str]


def _title_names(videos_mnt: Path, kinds: tuple[str, ...] = ("movies", "tv")) -> dict[str, str]:
    """casefold(name) -> name for every title folder on disk."""
    names: dict[str, str] = {}
    for _state, rel in STATES:
        for kind in kinds:
            base = videos_mnt / rel / kind
            if base.is_dir():
                for child in base.iterdir():
                    if child.is_dir():
                        names.setdefault(child.name.casefold(), child.name)
    return names


def find_hits(query: str, videos_mnt: Path,
              kinds: tuple[str, ...] = ("movies", "tv")) -> list[Hit]:
    """Every on-disk title whose folder name contains the query
    (case-insensitive), merged across states, sorted by name."""
    low = query.casefold()
    found: dict[tuple[str, str], dict[str, Path]] = {}
    for state, rel in STATES:
        for kind in kinds:
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


def suggestions(query: str, videos_mnt: Path,
                kinds: tuple[str, ...] = ("movies", "tv")) -> list[str]:
    """Near-miss title names for a zero-hit query — difflib, three at most."""
    names = _title_names(videos_mnt, kinds)
    return [names[m] for m in difflib.get_close_matches(
        query.casefold(), list(names), n=3, cutoff=0.6)]


def find_music_hits(query: str, music_mnt: Path) -> list[AlbumHit]:
    """Find music albums in source staging folders and the filed library.

    Staged cyanrip folders use the ``Artist - Album`` name produced by the
    ripper; library folders carry artist and album as separate path levels.
    """
    low = query.casefold()
    found: dict[str, set[str]] = {}
    rip = music_mnt / "rip"
    for source in SOURCES:
        base = rip / source
        if not base.is_dir():
            continue
        for folder in base.iterdir():
            if not folder.is_dir():
                continue
            if " - " in folder.name:
                artist, album = folder.name.split(" - ", 1)
                display = f"{artist} / {album}"
            else:
                artist, album, display = "", folder.name, folder.name
            if low in artist.casefold() or low in album.casefold():
                found.setdefault(display, set()).add(f"staging ({source})")

    library = music_mnt / "library"
    if library.is_dir():
        for artist_dir in library.iterdir():
            if not artist_dir.is_dir():
                continue
            for album_dir in artist_dir.iterdir():
                if album_dir.is_dir() and (low in artist_dir.name.casefold()
                                           or low in album_dir.name.casefold()):
                    display = f"{artist_dir.name} / {album_dir.name}"
                    found.setdefault(display, set()).add("library")
    state_order = {"staging (cd)": 0, "staging (other)": 1,
                   "staging (bandcamp)": 2, "library": 3}
    return [AlbumHit(name, "album", sorted(states, key=state_order.__getitem__))
            for name, states in sorted(found.items(), key=lambda item: item[0].casefold())]


def music_suggestions(query: str, music_mnt: Path) -> list[str]:
    """Near-miss music album names from the two music roots."""
    names = {hit.name.casefold(): hit.name for hit in find_music_hits("", music_mnt)}
    return [names[m] for m in difflib.get_close_matches(
        query.casefold(), list(names), n=3, cutoff=0.6)]


def _mount_or_note(mount: str, export: str, label: str) -> bool:
    """Mount a scoped share, converting mount failures into one note line."""
    # ensure_mounted prints its own diagnostics before raising SystemExit. Keep
    # the search contract to one note by capturing that implementation detail.
    try:
        with redirect_stderr(io.StringIO()):
            ensure_mounted(mount, export)
    except (SystemExit, OSError, RuntimeError):
        ui.warn(f"cannot mount {label} share; skipping")
        return False
    return True


def run_search(args) -> None:
    """The scoped search entry: mount selected shares, walk, report — exit 0."""
    scope = args.scope
    video_kinds = {"movie": ("movies",), "tv": ("tv",),
                   "video": ("movies", "tv")}
    videos_mnt = os.environ.get("VIDEOS_MOUNT", "/mnt/videos")
    music_mnt = os.environ.get("MUSIC_MOUNT", "/mnt/music")
    hits: list[Hit | AlbumHit] = []
    near: list[str] = []
    searched_any = False
    if scope in video_kinds or scope == "all":
        kinds = video_kinds.get(scope, ("movies", "tv"))
        if _mount_or_note(videos_mnt, os.environ.get("NFS_VIDEOS", ""), "video"):
            searched_any = True
            hits.extend(find_hits(args.query, Path(videos_mnt), kinds))
            near.extend(suggestions(args.query, Path(videos_mnt), kinds))
    if scope in ("music", "all"):
        if _mount_or_note(music_mnt, os.environ.get("NFS_MUSIC", ""), "music"):
            searched_any = True
            hits.extend(find_music_hits(args.query, Path(music_mnt)))
            near.extend(music_suggestions(args.query, Path(music_mnt)))
    if not hits:
        if not searched_any:
            return
        unique_near = list(dict.fromkeys(near))
        console.print("no hits" + (f" — did you mean: {', '.join(unique_near)}?"
                                    if unique_near else ""))
        return
    for hit in hits:
        console.print(f"[bold]{hit.name}[/bold] — {hit.kind}")
        if isinstance(hit, AlbumHit):
            for state in hit.states:
                console.print(f"  {state}")
        else:
            for state, seasons in hit.states:
                label = (f"{state}   {fmt_seasons(seasons)}"
                         if hit.kind == "tv" and seasons else state)
                console.print(f"  {label}")
