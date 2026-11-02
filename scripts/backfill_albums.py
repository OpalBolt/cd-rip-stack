#!/usr/bin/env python3
"""Reconstruct album validation records for an existing music library.

Usage: python scripts/backfill_albums.py [--apply] [--root PATH]

The default is a dry run: every record that would be written is printed.
``--apply`` writes records, but never replaces one that already exists.
``--root`` names the music mount (default: ``$MUSIC_MOUNT`` or
``/mnt/music``); albums are read from its ``library/`` directory.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from mutagen import File as mutagen_file

_repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo))

from skive.tag import AUDIO_EXTENSIONS
from skive.validation import VALIDATION_FILENAME, album_document, write_validation


_URL_RE = re.compile(r"https?://[^\s<>\"']+")
# Tests replace this callable; keeping it at module scope also keeps all file
# decoding behind one seam, so the script never needs a real audio fixture.
reader = mutagen_file


def _values(tags, *names) -> list:
    """Return tag values for the first matching key, normalizing easy tags."""
    if tags is None:
        return []
    for name in names:
        value = tags.get(name) if hasattr(tags, "get") else None
        if value not in (None, ""):
            return value if isinstance(value, (list, tuple)) else [value]
    return []


def _tag_values(audio, *names) -> list:
    tags = getattr(audio, "tags", None)
    values = _values(tags, *names)
    if values:
        return values
    # Small reader fakes often expose tags as a dict directly.
    return _values(audio if isinstance(audio, dict) else None, *names)


def _first(audio, *names):
    values = _tag_values(audio, *names)
    return values[0] if values else None


def _release(audio_files: list) -> str | None:
    """Use the MusicBrainz id, or a Bandcamp URL in comment/url tags."""
    for audio in audio_files:
        value = _first(audio, "musicbrainz_albumid", "mb_albumid",
                       "mb_release_id", "musicbrainz release id")
        if value:
            return str(value)
    for audio in audio_files:
        for field in ("comment", "comments", "url", "website"):
            for value in _tag_values(audio, field):
                match = _URL_RE.search(str(value))
                if match and "bandcamp.com" in urlparse(match.group(0)).netloc:
                    return match.group(0).rstrip(".,)")
    return None


def _number(value) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None


def _total(audio, total_name: str, number_name: str) -> int | None:
    direct = _first(audio, total_name)
    if direct is not None:
        return _number(direct)
    number = _first(audio, number_name)
    if number is not None and "/" in str(number):
        return _number(str(number).split("/", 1)[1])
    return None


def _track(path: Path, audio) -> dict:
    info = getattr(audio, "info", None)
    length = getattr(info, "length", None)
    try:
        duration = int(round(float(length))) if length is not None else None
    except (TypeError, ValueError):
        duration = None
    fmt = getattr(audio, "format", None)
    if not fmt:
        fmt = path.suffix.lstrip(".").upper() or None
    return {"file": path.name, "dur_s": duration, "format": fmt}


def build_album_document(folder: Path, reader=None) -> dict:
    """Build the reconstructed document for one album folder.

    ``reader`` is injectable for tests because tiny test files are not valid
    FLAC/MP3 files. A reader returns a mutagen-like object with ``tags`` and
    ``info.length``; unreadable files are omitted rather than invented.
    """
    read = reader or globals()["reader"]
    tracks = []
    audio_files = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name == VALIDATION_FILENAME \
                or path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        try:
            audio = read(path, easy=True)
        except TypeError:
            audio = read(path)
        except (OSError, ValueError):
            audio = None
        if audio is None:
            continue
        audio_files.append(audio)
        tracks.append(_track(path, audio))

    first = audio_files[0] if audio_files else None
    tracktotal = _total(first, "tracktotal", "tracknumber") if first else None
    disctotal = _total(first, "disctotal", "discnumber") if first else None
    # These are deliberately beside (not in place of) the tag totals: a gap
    # is visible as e.g. tracks_found=11, tracktotal=12.
    return album_document("unknown", _release(audio_files), tracks,
                          reconstructed=True, tracks_found=len(tracks),
                          tracktotal=tracktotal, disctotal=disctotal)


def backfill_album(folder: Path, reader=None) -> dict:
    """Return the reconstructed document for one library album folder."""
    return build_album_document(folder, reader=reader)


def _albums(library: Path):
    """Yield the two-level ``library/<Artist>/<Year Album>`` album folders."""
    if not library.is_dir():
        return
    for artist in sorted(library.iterdir()):
        if not artist.is_dir():
            continue
        for album in sorted(artist.iterdir()):
            if album.is_dir():
                yield album


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="backfill_albums.py",
        description="Reconstruct missing album validation.json files (dry-run by default).")
    parser.add_argument("--apply", action="store_true",
                        help="write records; default is to print the plan")
    parser.add_argument("--root", metavar="PATH",
                        default=os.environ.get("MUSIC_MOUNT", "/mnt/music"),
                        help="music mount (default: $MUSIC_MOUNT or /mnt/music)")
    args = parser.parse_args(argv)

    planned = skipped = 0
    for folder in _albums(Path(args.root) / "library"):
        destination = folder / VALIDATION_FILENAME
        rel = destination.relative_to(Path(args.root))
        if destination.exists():
            print(f"keep   {rel}: validation.json already there")
            skipped += 1
            continue
        doc = backfill_album(folder)
        release = doc["release"] or "no release reference"
        print(f"write  {rel}: {doc['tracks_found']} of "
              f"{doc['tracktotal'] if doc['tracktotal'] is not None else '?'} tracks, {release}")
        if args.apply:
            write_validation(destination, doc)
        planned += 1

    action = "wrote" if args.apply else "would write"
    print(f"\n{action} {planned} document(s); {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
