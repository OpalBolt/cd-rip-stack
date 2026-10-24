"""The validation.json document: the movie and TV shapes, the writer, the reader.

One module the rip flow, the transcoder, and the library checker all import
(CONTEXT.md: Validation file). The file is born in the title folder at rip,
travels staging -> transcode -> library, and is the durable record of what
was ripped and what was chosen: identity, per-title durations and grades,
the recorded main, the run that produced it. The transcoder appends its
encode results; nothing else in the pipeline writes the document. Pure
functions and file IO — the flows own every prompt and table. The library
checker (skive validate) is report-only: it walks the share, prints one
findings table, touches nothing, exits 0.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from rich.console import Console
from rich.table import Table
from rich.text import Text

from skive.tmdb import fetch_by_id, fetch_season, search_first
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


# --- TV post-rip record (map.md decision 6) ----------------------------------


def tv_row(ep: int, file: str, t: int | None, dur_s: int | None,
           runtime_s: int | None) -> dict:
    """One TV-document episodes entry: a staged episode's probed duration,
    graded against that episode's TMDB runtime.

    The band grades the exact staged÷TMDB quotient (grade_main); the stored
    ratio is that quotient rounded to 2dp for the record. An unknown duration
    or runtime leaves ratio/band null — a blank cell, never a false yellow.
    dur_s itself is always recorded, null only when the probe could not read
    the file at all; runtime_s is null when TMDB lists no runtime for the
    episode (the fetch's 0 reads as unknown).

    Args:
        ep: The episode number (a double's lo)
        file: Final file name in the show folder ("Show (2019)_S02E05.mkv")
        t: The disc title the episode came from, None when unknown — fast-scan
            table numbers are scanner indices, and an episode outside this
            run's map has no title to name
        dur_s: Probed duration in seconds, None when it would not read
        runtime_s: TMDB runtime in seconds, None when unknown

    Returns:
        {"ep", "file", "t", "dur_s", "runtime_s", "ratio", "band"} for
        the episodes list
    """
    runtime_s = runtime_s or None
    graded = (grade_main(dur_s, runtime_s) if dur_s is not None else None)
    return {"ep": ep, "file": file, "t": t, "dur_s": dur_s,
            "runtime_s": runtime_s,
            "ratio": round(graded[0], 2) if graded else None,
            "band": graded[1] if graded else None}


def merge_tv_document(old: dict, doc: dict) -> dict:
    """A same-show, same-season re-rip's document merged into the folder's
    existing one.

    episodes: the existing rows kept, this run's appended — an ep already
    recorded is replaced by this run's row, not duplicated (each run probes
    the season's staged files, so its rows are the current actuals). run:
    latest wins. encodes start empty — the record travels on with the new rip.

    Args:
        old: The existing document (same tmdb id and season, caller-checked)
        doc: This run's document

    Returns:
        The merged document, this run's shape throughout
    """
    old_eps = old.get("episodes")
    old_rows = list(old_eps) if isinstance(old_eps, list) else []
    redone = {r.get("ep") for r in doc["episodes"]}
    merged = dict(doc)
    merged["episodes"] = [r for r in old_rows if r.get("ep") not in redone] \
        + list(doc["episodes"])
    return merged


# --- Library checker (skive validate; map.md decision 7) ---------------------

console = Console()

STAGING_NAME = re.compile(r"_t\d+")
"""A leftover staging label in a library file name — the MakeMKV junk tags and
staged `_tNN` title numbers that must never survive into the library (the
Mamma Mia extras pollution; CONTEXT.md: Library checker)."""

SEASON_DIR = re.compile(r"^Season (\d{2,})$")
"""A TV season folder under a show folder; Season 00 is Specials."""

YEAR_SUFFIX = re.compile(r"^(.*) \((\d{4})\)$")
"""The `<Name (Year)>` title-folder shape; the year is what makes a legacy
folder's name+year TMDB search worth running."""

EPISODE_IN_NAME = re.compile(r"S(\d+)E(\d+)")
"""An episode number inside a library file name (`..._S02E05`, transcode's
`... - S02E05`); the season comes from the Season NN folder, the E from here."""


class Finding(NamedTuple):
    """One row of the `skive validate` report.

    folder is the title folder as walked ("movies/Mamma Mia! (2008)"); check
    names the rule ("ratio", "staging name", "missing episodes", "duplicate
    id", "unidentified", "unreadable"); detail is the one-line human text.
    band is set on ratio rows only ("green"/"yellow", the shared band's
    verdict on that file) and None elsewhere; a ratio row with band None is a
    blank cell — a file graded against an unknown runtime, never yellow.
    """

    folder: str
    check: str
    detail: str
    band: str | None = None


def probe_duration(path: Path, run=subprocess.run) -> int | None:
    """The file's duration in seconds via ffprobe (`format=duration`, the
    rename_tv_fast probe shape); None when the file won't probe — the caller
    reports it unreadable instead of crashing the walk.

    Args:
        path: The media file to probe
        run: subprocess.run callable for monkeypatching

    Returns:
        Duration in whole seconds, or None when unreadable
    """
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "csv=p=0", str(path)]
    try:
        result = run(cmd, capture_output=True, text=True)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return int(float(result.stdout.strip()))
    except (ValueError, TypeError, AttributeError):
        return None


def staging_name_findings(folder: Path, rel: str) -> list[Finding]:
    """Every file anywhere under the folder wearing a `_tNN` staging name.

    Args:
        folder: The library title folder
        rel: The folder's walk-relative name for the finding rows

    Returns:
        One "staging name" finding per offending file, sorted by path
    """
    return [Finding(rel, "staging name", str(p.relative_to(folder)))
            for p in sorted(folder.rglob("*"))
            if p.is_file() and STAGING_NAME.search(p.name)]


def _doc_identity(block: object) -> tuple[str, str, int | None] | None:
    """(type, id, runtime) from a document's tmdb block, None when unusable.

    runtime is None when absent or not a positive int — an unknown runtime is
    recorded unknown, never guessed (the no-false-yellow rule).
    """
    if not isinstance(block, dict) or block.get("id") is None:
        return None
    kind = block.get("type")
    runtime = block.get("runtime_s")
    return (kind if kind in ("movie", "tv") else "movie",
            str(block["id"]),
            runtime if isinstance(runtime, int) and runtime > 0 else None)


def _ratio_finding(rel: str, relpath: str, dur_s: int,
                   runtime_s: int | None) -> Finding:
    """One ratio row: the file's probed duration graded in the shared band.

    Unknown runtime (or a zero-duration probe read): a blank cell — band None,
    never yellow (grade_main's no-false-yellow rule).
    """
    graded = grade_main(dur_s, runtime_s)
    if graded is None:
        return Finding(rel, "ratio", relpath)
    return Finding(rel, "ratio", f"{relpath}  {graded[0]:.2f}", graded[1])


def _unreadable_finding(rel: str, relpath: str) -> Finding:
    return Finding(rel, "unreadable", f"{relpath}: duration unreadable")


def _search_identity(kind: str, rel: str, folder_name: str, search,
                     findings: list[Finding]) -> str | None:
    """A legacy folder's identity by name+year search: the trailing (YYYY)
    splits off, the first search hit names the id — a LEAD, not a verdict.

    No (Year) in the name, no match, or a failed search: one "unidentified"
    finding row (plus one stderr warning when TMDB was unreachable), never an
    error — the walk goes on. Returns the id, or None when unresolved.
    """
    m = YEAR_SUFFIX.match(folder_name)
    if m is None:
        findings.append(Finding(rel, "unidentified",
                                "no validation.json and no (Year) in the folder name"))
        return None
    name = m.group(1)
    try:
        pick = search(kind, name)
    except (RuntimeError, ValueError) as e:
        print(f"skive: validate: TMDB search failed for {folder_name} ({e})",
              file=sys.stderr)
        findings.append(Finding(rel, "unidentified",
                                f'TMDB search failed for "{name}"'))
        return None
    if pick is None:
        findings.append(Finding(rel, "unidentified",
                                f'TMDB search: no match for "{name}"'))
        return None
    return pick.id


def _movie_ratio_findings(folder: Path, rel: str, runtime_s: int | None,
                          run=subprocess.run) -> list[Finding]:
    """Ratio rows for a movie folder: the main encode, plus every other .mkv
    in the folder tree that reaches the main floor.

    The main encode is `<folder>.mkv` beside the doc, or — legacy folders —
    the biggest .mkv in the folder root. A full-length extra is the
    two-candidates signature (the Mamma Mia case: the chosen main read 1.03 —
    green — so ratio alone cannot catch it; two graded feature-length files in
    one movie folder is the visible lead), so each graded file gets its own
    row; sub-floor extras are never graded. Probe failures are "unreadable"
    rows, not crashes.
    """
    main = folder / f"{folder.name}.mkv"
    if not main.is_file():
        root_mkvs = [p for p in folder.glob("*.mkv") if p.is_file()]
        main = max(root_mkvs, key=lambda p: p.stat().st_size, default=None)
    others = [p for p in sorted(folder.rglob("*.mkv"))
              if p.is_file() and p != main]
    findings: list[Finding] = []
    for path in ([main] if main is not None else []) + others:
        relpath = str(path.relative_to(folder))
        dur = probe_duration(path, run)
        if dur is None:
            findings.append(_unreadable_finding(rel, relpath))
            continue
        if path != main and dur < MAIN_FLOOR_S:
            continue  # a short extra — candidate-main material only
        findings.append(_ratio_finding(rel, relpath, dur, runtime_s))
    return findings


def check_movie_folder(folder: Path, rel: str, *, run=subprocess.run,
                       search=search_first,
                       fetch_details=fetch_by_id) -> tuple[list[Finding], tuple[str, str] | None]:
    """One movie folder: staging names, ratio rows, and the folder's identity.

    Identity: the folder's validation.json (its id and recorded runtime), else
    the legacy fallback — name+year search for the id, details fetch for the
    runtime. Unresolvable or unreachable: "unidentified" row, walk goes on.

    Args:
        folder: The library movie folder
        rel: The folder's walk-relative name for the finding rows
        run: subprocess.run callable for the ffprobe probes
        search: TMDB name+year search (kind, query) -> pick | None
        fetch_details: TMDB details fetch (type, id) -> pick

    Returns:
        (findings, identity) — identity as (type, id) for the duplicate check,
        None when unresolved
    """
    staging = staging_name_findings(folder, rel)
    doc = load_validation(folder / VALIDATION_FILENAME)
    identity = _doc_identity(doc.get("tmdb")) if doc else None
    findings: list[Finding] = []
    if identity is not None:
        kind, id_, runtime = identity
        return _movie_ratio_findings(folder, rel, runtime, run) + staging, (kind, id_)
    id_ = _search_identity("movie", rel, folder.name, search, findings)
    if id_ is None:
        return findings + staging, None
    try:
        details = fetch_details("movie", id_)
    except (RuntimeError, ValueError) as e:
        print(f"skive: validate: TMDB details fetch failed for {folder.name} ({e})",
              file=sys.stderr)
        findings.append(Finding(rel, "unidentified",
                                f"TMDB details fetch failed for id {id_}"))
        return findings + staging, None
    return _movie_ratio_findings(folder, rel, details.runtime_secs, run) \
        + findings + staging, ("movie", id_)


def _episode_runs(eps: list[int]) -> str:
    """E-codes with consecutive runs collapsed: [6, 7, 8, 11] -> "E06-E08, E11"."""
    parts: list[str] = []
    start = prev = eps[0]
    for ep in eps[1:]:
        if ep == prev + 1:
            prev = ep
            continue
        parts.append(f"E{start:02d}" if start == prev else f"E{start:02d}-E{prev:02d}")
        start = prev = ep
    parts.append(f"E{start:02d}" if start == prev else f"E{start:02d}-E{prev:02d}")
    return ", ".join(parts)


def _tv_season_findings(folder: Path, rel: str, season_dir: Path, show_id: str,
                        doc: dict | None, season_list, run=subprocess.run) -> list[Finding]:
    """One Season NN folder: a ratio row per episode file, and the
    missing-episode check.

    Episode runtimes come from the season's document rows (offline) or the
    TMDB season list (legacy folders); an episode with neither is a blank
    cell, never yellow. The missing check grades the folder's `S..E..` names
    against the TMDB season list when it fetched, else the document's episode
    rows; with neither source it skips — never guesses. A season-list fetch
    failure degrades to the doc source with one stderr warning.
    """
    season = int(SEASON_DIR.match(season_dir.name).group(1))
    findings: list[Finding] = []
    doc_rows: dict[int, dict] = {}
    if doc and isinstance(doc.get("tmdb"), dict) and doc["tmdb"].get("season") == season:
        rows = doc.get("episodes")
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and isinstance(row.get("ep"), int):
                doc_rows[row["ep"]] = row
    try:
        listed = season_list(show_id, season)
    except (RuntimeError, ValueError) as e:
        print(f"skive: validate: TMDB season fetch failed for "
              f"{folder.name} Season {season:02d} ({e})", file=sys.stderr)
        listed = None
    runtimes: dict[int, int | None] = {}
    for ep, row in doc_rows.items():
        rt = row.get("runtime_s")
        runtimes[ep] = rt if isinstance(rt, int) and rt > 0 else None
    for ep, _name, rt in (listed or []):
        runtimes.setdefault(ep, rt or None)
    files: dict[int, Path] = {}
    for p in sorted(season_dir.glob("*.mkv")):
        if not p.is_file():
            continue
        m = EPISODE_IN_NAME.search(p.stem)
        if m:
            files.setdefault(int(m.group(2)), p)
    for ep in sorted(files):
        relpath = str(files[ep].relative_to(folder))
        dur = probe_duration(files[ep], run)
        if dur is None:
            findings.append(_unreadable_finding(rel, relpath))
            continue
        findings.append(_ratio_finding(rel, relpath, dur, runtimes.get(ep)))
    if listed is not None:
        expected, source = sorted(ep for ep, _n, _rt in listed), "TMDB season list"
    elif doc_rows:
        expected, source = sorted(doc_rows), "validation.json"
    else:
        return findings  # nothing to compare against — the check skips
    missing = [ep for ep in expected if ep not in files]
    if missing:
        findings.append(Finding(rel, "missing episodes",
                                f"Season {season:02d} missing "
                                f"{_episode_runs(missing)} ({source})"))
    return findings


def check_tv_folder(folder: Path, rel: str, *, run=subprocess.run,
                    search=search_first,
                    season_list=fetch_season) -> tuple[list[Finding], tuple[str, str] | None]:
    """One TV show folder: staging names, then per-season ratio rows and
    missing-episode checks.

    Identity: any Season NN folder's validation.json names the show id
    (season docs copy beside their encodes); legacy shows fall back to the
    name+year search. Unresolved: "unidentified" row and only the checks that
    need no identity (staging names) — missing episodes skip.

    Args:
        folder: The library show folder
        rel: The folder's walk-relative name for the finding rows
        run: subprocess.run callable for the ffprobe probes
        search: TMDB name+year search (kind, query) -> pick | None
        season_list: TMDB season fetch (tv_id, season) -> [(ep, name, runtime)]

    Returns:
        (findings, identity) — identity as (type, id) for the duplicate check,
        None when unresolved
    """
    staging = staging_name_findings(folder, rel)
    season_dirs = sorted((d for d in folder.iterdir()
                          if d.is_dir() and SEASON_DIR.match(d.name)),
                         key=lambda d: d.name)
    docs: dict[Path, dict] = {}
    show_id: str | None = None
    for d in season_dirs:
        doc = load_validation(d / VALIDATION_FILENAME)
        if doc is None:
            continue
        docs[d] = doc
        identity = _doc_identity(doc.get("tmdb"))
        if identity is not None and show_id is None:
            show_id = identity[1]
    findings: list[Finding] = []
    if show_id is None:
        show_id = _search_identity("tv", rel, folder.name, search, findings)
    if show_id is None:
        return findings + staging, None
    season_rows: list[Finding] = []
    for d in season_dirs:
        season_rows.extend(_tv_season_findings(folder, rel, d, show_id,
                                               docs.get(d), season_list, run))
    return season_rows + findings + staging, ("tv", show_id)


def validate_library(root: Path, *, run=subprocess.run, search=None,
                     fetch_details=None,
                     season_list=None) -> list[Finding]:
    """Walk `library/movies` + `library/tv` under the share root and collect
    the report's findings.

    The thin orchestrator around the per-folder checks: folders walk in name
    order per root, every folder contributes its rows, and after the walk the
    duplicate check folds the collected identities — an id seen under two
    folders is flagged once. Nothing is written, nothing is deleted, nothing
    raises for a bad folder; network failures degrade per-folder. The TMDB
    seams resolve from module globals at call time (search_first, fetch_by_id,
    fetch_season), so tests monkeypatch skive.validation's names.

    Args:
        root: The library root holding movies/ and tv/ (the share's library/)
        run: subprocess.run callable for the ffprobe probes
        search: TMDB name+year search (kind, query) -> pick | None; None =
            skive.tmdb.search_first
        fetch_details: TMDB details fetch (type, id) -> pick; None =
            skive.tmdb.fetch_by_id
        season_list: TMDB season fetch (tv_id, season) -> [(ep, name,
            runtime)]; None = skive.tmdb.fetch_season

    Returns:
        The findings, in walk order with duplicate-id rows appended
    """
    search = search or search_first
    fetch_details = fetch_details or fetch_by_id
    season_list = season_list or fetch_season
    findings: list[Finding] = []
    seen: dict[tuple[str, str], list[str]] = {}
    for sub in ("movies", "tv"):
        base = root / sub
        if not base.is_dir():
            continue
        for folder in sorted((f for f in base.iterdir() if f.is_dir()),
                             key=lambda f: f.name):
            rel = f"{sub}/{folder.name}"
            if sub == "movies":
                rows, identity = check_movie_folder(
                    folder, rel, run=run, search=search, fetch_details=fetch_details)
            else:
                rows, identity = check_tv_folder(
                    folder, rel, run=run, search=search, season_list=season_list)
            findings.extend(rows)
            if identity is not None:
                seen.setdefault(identity, []).append(rel)
    for (kind, id_), rels in sorted(seen.items()):
        if len(rels) > 1:
            findings.append(Finding(rels[0], "duplicate id",
                                    f"tmdb {kind} {id_} also under "
                                    f"{'; '.join(rels[1:])}"))
    return findings


def print_findings(findings: list[Finding], root: Path) -> None:
    """The ONE rich table (repo print_table style): every graded ratio, every
    finding, in walk order. Prints only — nothing here writes or deletes.

    Args:
        findings: The walk's findings
        root: The share root the walk covered
    """
    console.print(f"\nLibrary validation — {root}")
    if not findings:
        console.print("no findings")
        return
    table = Table()
    table.add_column("folder", style="cyan")
    table.add_column("check", style="yellow", width=17)
    table.add_column("detail")
    for f in findings:
        detail = Text(f.detail, style=f.band) if f.band else f.detail
        table.add_row(f.folder, f.check, detail)
    console.print(table)


def run_validate(videos_root: Path | None = None, *,
                 run=subprocess.run) -> None:
    """The `skive validate` entry: resolve the share root ($VIDEOS_MOUNT, else
    /mnt/videos), walk its library/, print the table. Report-only, exit 0
    always — findings are leads, not verdicts; a missing library warns
    instead of walking the wrong tree.

    Args:
        videos_root: Share root override (tests); None reads $VIDEOS_MOUNT
        run: subprocess.run callable for the ffprobe probes
    """
    share = videos_root or Path(os.environ.get("VIDEOS_MOUNT", "/mnt/videos"))
    library = share / "library"
    if not library.is_dir():
        print(f"skive: validate: no library/ under {share}", file=sys.stderr)
        return
    print_findings(validate_library(library, run=run), library)
