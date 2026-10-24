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
from typing import NamedTuple

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


# --- Candidate-main arbitration (map.md decision 4) -------------------------

MAIN_FLOOR_S = 2400
"""Shortest duration (seconds) that counts as candidate-main material — 40
minutes of feature on any media; menu loops and shorts stay below it."""


class MainArbitration(NamedTuple):
    """The candidate-main arbitration outcome.

    kind reads "sole" (exactly one candidate near the runtime — pick is
    automatic), "several" (two or more near — the human picks; near carries
    them for the prompt, pick is the default), "zero" (no candidate near —
    biggest-bytes fallback, silent) or "unknown" (no runtime to grade against
    — biggest-bytes fallback, silent; near-ness is never guessed). pick is the
    automatic/fallback/default candidate as a (number, secs, size) triple, or
    None when there is nothing to pick (no candidates at all: shorts, bonus
    discs — the flow records main=None and transcode keeps its biggest-bytes
    fallback)."""

    kind: str
    near: list[tuple[int, int, float]]  # (t-number, secs, exact ratio), several only
    pick: tuple[int, int, int] | None


def grade_main(dur_s: int, runtime_s: int | None) -> tuple[float, str] | None:
    """(exact ratio, band) for one candidate graded against the film runtime.

    The band grades the exact disc÷TMDB quotient in the shared RATIO_GREEN
    band; callers show the 2dp value. None — a blank cell, never yellow —
    when either side is unknown (no-false-yellow).

    Args:
        dur_s: Candidate title duration in seconds
        runtime_s: TMDB runtime in seconds, None when unknown

    Returns:
        (ratio, "green" or "yellow"), or None when ungradable
    """
    if runtime_s is None or runtime_s <= 0 or dur_s <= 0:
        return None
    ratio = dur_s / runtime_s
    inside = RATIO_GREEN[0] <= ratio <= RATIO_GREEN[1]
    return (ratio, "green" if inside else "yellow")


def main_candidates(titles: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """The candidate mains: titles at/above MAIN_FLOOR_S, in the order given.

    Args:
        titles: (title_number, duration_seconds, size_bytes) triples, the
            tv_match helper shape

    Returns:
        The candidates (duration >= MAIN_FLOOR_S)
    """
    return [t for t in titles if t[1] >= MAIN_FLOOR_S]


def biggest_title(titles: list[tuple[int, int, int]]) -> tuple[int, int, int] | None:
    """The biggest-bytes title; longest duration breaks ties — and decides
    outright when every size is 0 (the fast scan knows durations only).

    Args:
        titles: (title_number, duration_seconds, size_bytes) triples

    Returns:
        The fallback candidate, or None for an empty list
    """
    return max(titles, key=lambda t: (t[2], t[1]), default=None)


def arbitrate_main(candidates: list[tuple[int, int, int]],
                   runtime_s: int | None) -> MainArbitration:
    """The main-feature decision (map.md decision 4): one candidate near the
    TMDB runtime wins automatically, several near need the human, none near
    — or no runtime at all — falls back to biggest bytes silently. Both
    heuristics picked the wrong film in the Mamma Mia case, so only "several"
    asks; the flow's prompt lists `near` with the biggest-bytes default.

    Args:
        candidates: Candidate mains (main_candidates), (number, secs, size)
        runtime_s: TMDB runtime in seconds, None when unknown

    Returns:
        MainArbitration — near carries (t-number, duration, exact ratio) in
        title order, several only; pick is the automatic/fallback/default
        candidate, None when there were no candidates at all
    """
    if runtime_s is None or runtime_s <= 0:
        return MainArbitration("unknown", [], biggest_title(candidates))
    near = []
    for c in candidates:
        graded = grade_main(c[1], runtime_s)
        if graded is not None and graded[1] == "green":
            near.append(c)
    if len(near) == 1:
        return MainArbitration("sole", [], near[0])
    if len(near) > 1:
        return MainArbitration("several",
                               [(n, s, s / runtime_s) for n, s, _ in sorted(near)],
                               biggest_title(near))
    return MainArbitration("zero", [], biggest_title(candidates))


# --- Post-rip record (map.md decision 5) ------------------------------------


def movie_row(file: str, t: int, dur_s: int | None, runtime_s: int | None) -> dict:
    """One movie-document titles entry: a staged file's probed duration,
    graded against the film runtime when both are known and the duration
    reaches the main floor.

    The band grades the exact disc÷TMDB quotient (grade_main); the stored
    ratio is that quotient rounded to 2dp for the record. An unknown duration
    or runtime — or a sub-floor short — leaves ratio/band null: a blank cell,
    never a false yellow. dur_s itself is always recorded, null only when the
    probe could not read the file at all.

    Args:
        file: Final file name in the title folder ("Mamma Mia! (2008)_t21.mkv")
        t: The file's real title number, parsed from the final _tNN
        dur_s: Probed duration in seconds, None when it would not read
        runtime_s: TMDB runtime in seconds, None when unknown

    Returns:
        {"file", "t", "dur_s", "ratio", "band"} for the titles list
    """
    graded = (grade_main(dur_s, runtime_s)
              if dur_s is not None and dur_s >= MAIN_FLOOR_S else None)
    return {"file": file, "t": t, "dur_s": dur_s,
            "ratio": round(graded[0], 2) if graded else None,
            "band": graded[1] if graded else None}


def resolve_main(chosen_main: str | None, graded_rows: list[dict],
                 staged_rows: list[dict], fast_scan: bool) -> str | None:
    """The table's chosen main resolved to a real staged file number.

    Full-scan table numbers are MakeMKV numbers, so the file whose final
    _tNN equals the chosen number wins outright; missing there (skipped
    decoy, collision roll forward) — and always on the fast scan, whose
    table numbers are scanner indices — the resolution falls back to a
    duration join ±1s against the chosen title's scanned duration. A join
    that matches nothing or several files resolves to None: never guess.

    Args:
        chosen_main: The table's pick ("t21"), None when nothing was chosen
        graded_rows: The pre-rip rows — the scanned duration per table number
        staged_rows: The probed post-rip rows — the real _tNN and actual
            duration per staged file (this run's files only)
        fast_scan: The fast path ran — table numbers are indices, skip the
            exact-number match

    Returns:
        The main as a real "tNN" file number, or None when unresolved
    """
    if chosen_main is None:
        return None
    main_t = int(chosen_main[1:])
    if not fast_scan and any(r["t"] == main_t for r in staged_rows):
        return chosen_main
    scanned = next((r["dur_s"] for r in graded_rows if r["t"] == main_t), None)
    if scanned is None:
        return None
    matches = sorted(r["t"] for r in staged_rows
                     if r["dur_s"] is not None and abs(r["dur_s"] - scanned) <= 1)
    if len(matches) == 1:
        return f"t{matches[0]}"
    return None


def merge_movie_document(old: dict, doc: dict) -> dict:
    """A same-film re-rip's document merged into the folder's existing one.

    titles: the existing rows kept, this run's appended (this run probes
    only its own files, so nothing duplicates). main: this run's resolution
    wins when present, else the old one stands. run: latest wins. encodes
    start empty — the record travels on with the new rip.

    Args:
        old: The existing document (same tmdb id, caller-checked)
        doc: This run's document

    Returns:
        The merged document, this run's shape throughout
    """
    old_titles = old.get("titles")
    merged = dict(doc)
    merged["titles"] = (list(old_titles) if isinstance(old_titles, list) else []) \
        + list(doc["titles"])
    if merged["main"] is None:
        merged["main"] = old.get("main")
    return merged
