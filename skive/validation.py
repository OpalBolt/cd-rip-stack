"""The validation.json document: the movie and TV shapes, the writer, the reader.

One module the rip flow, the transcoder, and the library checker all import
(CONTEXT.md: Validation file). The file is born in the title folder at rip,
travels staging -> transcode -> library, and is the durable record of what
was ripped and what was chosen: identity, per-title durations and grades,
the recorded main, the run that produced it. The transcoder appends its
encode results; nothing else in the pipeline writes the document. Pure
functions and file IO — the flows own every prompt and table."""

import json
from pathlib import Path

from skive.tv_match import RATIO_GREEN  # re-exported: one band, never a copy

VALIDATION_FILENAME = "validation.json"
"""The document's one name in every title folder it travels through."""


def movie_document(tmdb_id: str, title: str, runtime_s: int, volume_label: str,
                   run: str, main: str | None, titles: list[dict]) -> dict:
    """The movie document at rip time, in the shape transcode and the checker read.

    `titles` entries are {"file", "t", "dur_s", "ratio", "band"} — one per
    scanned title kept for the record, ratio grading disc against the TMDB
    runtime in the shared RATIO_GREEN band. `main` is the recorded pick
    ("t21"); None when no candidate main was chosen (shorts, bonus discs) —
    transcode then falls back to biggest-bytes. `encodes` starts empty and is
    appended by the transcoder as {"file", "src", "dur_s"}.

    Args:
        tmdb_id: TMDB id as string
        title: TMDB display title with year, e.g. "Mamma Mia! (2008)"
        runtime_s: TMDB runtime in seconds
        volume_label: Disc volume label the rip came from
        run: Short-uuid of the rip run that produced the file
        main: Chosen main title ("t21"), or None when none was chosen
        titles: Per-title record entries

    Returns:
        The movie document, key order fixed for diff-friendly files
    """
    return {
        "tmdb": {"id": tmdb_id, "type": "movie", "title": title, "runtime_s": runtime_s},
        "volume_label": volume_label,
        "run": run,
        "main": main,
        "titles": list(titles),
        "encodes": [],
    }


def tv_document(tmdb_id: str, title: str, season: int, volume_label: str,
                run: str, episodes: list[dict]) -> dict:
    """The TV document at rip time: the shared identity block plus the episode map.

    The episode map stands in for `main`/candidate ratios: `episodes` entries
    are {"ep", "file", "t", "dur_s", "runtime_s", "ratio", "band"} — one per
    staged episode, ratio grading the staged duration against that episode's
    TMDB runtime in the shared RATIO_GREEN band. Discs rip in order into one
    title folder, so each disc's flow merges its episodes into the existing
    document (load, extend, write). `encodes` starts empty and is appended by
    the transcoder as {"file", "src", "dur_s"}.

    Args:
        tmdb_id: TMDB show id as string
        title: TMDB display title with year, e.g. "Show (2019)"
        season: Season number the disc belongs to
        volume_label: Disc volume label the rip came from
        run: Short-uuid of the rip run that produced the file
        episodes: Per-episode record entries

    Returns:
        The TV document, key order fixed for diff-friendly files
    """
    return {
        "tmdb": {"id": tmdb_id, "type": "tv", "title": title, "season": season},
        "volume_label": volume_label,
        "run": run,
        "episodes": list(episodes),
        "encodes": [],
    }


def write_validation(path: Path, doc: dict) -> None:
    """Write the document as UTF-8 JSON, 2-space indent, trailing newline.

    The one writer every stage uses, so the file reads the same wherever it
    lands. The title folder exists by the time any stage writes.

    Args:
        path: Destination validation.json (<title_dir>/validation.json)
        doc: Document from movie_document/tv_document, or a loaded one
    """
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def load_validation(path: Path) -> dict | None:
    """The document at `path`, or None when there is no validation file.

    Absent file, unreadable bytes, and JSON that isn't an object all read as
    None — the callers' "no validation file" fallback (transcode: biggest-bytes
    main; checker: name+year identity). Nothing raises. A present document
    comes back as the parsed dict.

    Args:
        path: validation.json to read

    Returns:
        The parsed document, or None when absent or unreadable
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None
