"""Beets filing hook for music validation provenance records."""

from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import re
from urllib.parse import urlparse

from beets.plugins import BeetsPlugin

from skive.validation import (
    VALIDATION_FILENAME,
    album_document,
    load_validation,
    merge_album_document,
    write_validation,
)

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_DISC_SIDECAR_RE = re.compile(r"\s+CD\d+(?:\s+\(\d+\))?$", re.IGNORECASE)


def _value(item, *names):
    """Read a beets field or an attribute from a test double."""
    for name in names:
        value = item.get(name) if hasattr(item, "get") else None
        if value not in (None, ""):
            return value
        value = getattr(item, name, None)
        if value not in (None, ""):
            return value
    return None


def _path(value) -> Path:
    return Path(os.fsdecode(value))


def _item_paths(task) -> list[Path]:
    items = task.imported_items() if callable(getattr(task, "imported_items", None)) else getattr(task, "items", [])
    return [_path(item.path) for item in items if getattr(item, "path", None)]


def _destination(task) -> Path:
    album = getattr(task, "album", None)
    album_path = getattr(album, "path", None)
    if album_path:
        return _path(album_path)
    paths = _item_paths(task)
    if not paths:
        raise ValueError("import task has no destination items")
    # Item paths name files; their common parent is the imported album folder.
    return Path(os.path.commonpath([str(path.parent) for path in paths]))


def _source_paths(task) -> list[Path]:
    paths = getattr(task, "paths", None) or []
    if not paths:
        raise ValueError("import task has no source path")
    return [_path(path) for path in paths]


def _source(task) -> Path:
    """Return the first source folder in an import task."""
    return _source_paths(task)[0]


def _source_kind(source: Path) -> str:
    """Find the music source folder beneath the task's ``rip`` root."""
    current = source
    while current != current.parent:
        if current.parent.name == "rip":
            return current.name
        current = current.parent
    return "unknown"


def _track(item) -> dict:
    path = _path(item.path)
    duration = _value(item, "length", "duration", "duration_s")
    if duration is not None:
        try:
            duration = int(round(float(duration)))
        except (TypeError, ValueError):
            duration = None
    fmt = _value(item, "format") or path.suffix.lstrip(".").upper() or None
    return {"file": path.name, "dur_s": duration, "format": fmt}


def _release(items, source_doc: dict | None, source: str) -> str | None:
    if source_doc and source_doc.get("release"):
        return source_doc["release"]
    for item in items:
        mbid = _value(item, "mb_albumid", "mb_release_id", "musicbrainz_albumid")
        if mbid:
            return str(mbid)
    if source == "bandcamp":
        for item in items:
            for field in ("comment", "comments", "url", "website"):
                value = _value(item, field)
                match = _URL_RE.search(str(value)) if value else None
                if match and "bandcamp.com" in urlparse(match.group(0)).netloc:
                    return match.group(0).rstrip(".,)")
    return None


def _disc_number(source_doc: dict | None, items,
                 source: Path | None = None) -> str:
    if source_doc and isinstance(source_doc.get("discs"), dict) and source_doc["discs"]:
        return str(next(reversed(source_doc["discs"])))
    if source is not None:
        match = re.search(r"(?:disc|cd)[ _-]?(\d+)$", source.name, re.IGNORECASE)
        if match:
            return match.group(1)
    for item in items:
        number = _value(item, "disc", "discnumber", "disc_number")
        if number:
            return str(number).split("/", 1)[0]
    return "1"


def _has_disc_marker(stem: str) -> bool:
    return _DISC_SIDECAR_RE.search(stem) is not None


def _unique_sidecar_target(destination: Path, name: str) -> Path:
    """Return a non-existing target, retaining both files on a collision."""
    target = destination / name
    if not target.exists():
        return target
    suffix = target.suffix
    base = target.stem
    number = 2
    while True:
        candidate = destination / f"{base} ({number}){suffix}"
        if not candidate.exists():
            return candidate
        number += 1


def _rename_unmarked_sidecars(destination: Path) -> None:
    """Bring old single-disc sidecars into the multi-disc naming form."""
    for suffix in (".log", ".cue"):
        for sidecar in list(destination.glob(f"*{suffix}")):
            if _has_disc_marker(sidecar.stem):
                continue
            target_name = f"{sidecar.stem} CD1{sidecar.suffix}"
            sidecar.replace(_unique_sidecar_target(destination, target_name))


def _file_sidecars(source: Path, destination: Path, disc_number: str,
                   disc_count: int) -> None:
    if not source.is_dir():
        return
    for suffix in (".log", ".cue"):
        for sidecar in source.glob(f"*{suffix}"):
            name = sidecar.name
            if disc_count > 1 and not _has_disc_marker(sidecar.stem):
                name = f"{sidecar.stem} CD{disc_number}{sidecar.suffix}"
            sidecar.replace(_unique_sidecar_target(destination, name))


def handle_import_task_files(task, *, tag_run: str | None = None,
                             mode: str | None = None) -> None:
    """Write one album record after beets has moved an import task's files."""
    sources = _source_paths(task)
    destination = _destination(task)
    items = list(task.imported_items()) if callable(getattr(task, "imported_items", None)) else list(getattr(task, "items", []))
    # The folder directly under rip is authoritative; never use the album
    # folder (or stale metadata) as the music source.
    source_kind = _source_kind(sources[0])
    run = tag_run or os.environ.get("SKIVE_TAG_RUN")
    beets_mode = mode or os.environ.get("SKIVE_TAG_MODE")
    if beets_mode not in {"autotag", "as-is"}:
        beets_mode = "as-is" if source_kind == "bandcamp" else "autotag"
    old = load_validation(destination / VALIDATION_FILENAME) or {}
    discs = {}
    release = None
    filed = date.today().isoformat()
    for source in sources:
        source_doc = load_validation(source / VALIDATION_FILENAME)
        disc_number = _disc_number(source_doc, items, source)
        source_discs = source_doc.get("discs") if source_doc else None
        if isinstance(source_discs, dict):
            discs.update({str(number): dict(entry) for number, entry in source_discs.items()
                          if isinstance(entry, dict)})
        entry = dict(discs.get(disc_number, {}))
        entry.update({"tag_run": run, "filed_at": filed, "mode": beets_mode})
        discs[disc_number] = entry
        if release is None:
            release = _release(items, source_doc, source_kind)

    doc = album_document(
        source_kind, release, [_track(item) for item in items],
        reconstructed=False, discs=discs,
    )
    merged = merge_album_document(old, doc)
    destination.mkdir(parents=True, exist_ok=True)
    write_validation(destination / VALIDATION_FILENAME, merged)
    disc_count = len(merged["discs"])
    if disc_count > 1:
        _rename_unmarked_sidecars(destination)
    for source in sources:
        disc_number = _disc_number(load_validation(source / VALIDATION_FILENAME),
                                   items, source)
        _file_sidecars(source, destination, disc_number, disc_count)


class SkiveAlbumPlugin(BeetsPlugin):
    """Attach provenance to every album successfully moved by beets."""

    def __init__(self):
        super().__init__()
        self.register_listener("import_task_files", self._import_task_files)

    def _import_task_files(self, session, task):
        try:
            handle_import_task_files(task)
        except Exception:
            self._log.exception("could not write album validation record")
