"""The validation.json document: the movie and TV shapes, the writer, the reader.

One module the rip flow, the transcoder, and the library checker all import
(CONTEXT.md: Validation file). The file is born in the title folder at rip,
travels staging -> transcode -> library, and is the durable record of what
was ripped and what was chosen: identity, per-title durations and grades,
the recorded main, the run that produced it. TV shows index one document
per show: every season with its episodes (the current actuals) and its
disc index (which run staged which episodes). The transcoder appends its
encode results; nothing else in the pipeline writes the document. Pure
functions and file IO — the flows own every prompt and table. The library
checker (skive validate) is report-only: it walks the share, prints one
findings table (or JSON via --json, or the issues-only view with its
action plan via --issues), exits 0. The one exception: --fix renames
staging-labelled extras files to the " - Extra NN" form — the safe automatic
fix; everything else stays a lead for the human.
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


def movie_document(tmdb_id: str, title: str, runtime_s: int | None,
                   volume_label: str | None, run: str | None,
                   main: str | None, titles: list[dict]) -> dict:
    """The movie document at rip time, in the shape transcode and the checker read.

    `titles` entries are {"file", "t", "dur_s", "ratio", "band"} — one per
    scanned title kept for the record, ratio grading disc against the TMDB
    runtime in the shared RATIO_GREEN band. `main` is the recorded pick
    ("t21"); None when no candidate main was chosen (shorts, bonus discs) —
    transcode then falls back to biggest-bytes. The backfill script writes
    the same shape with runtime_s/volume_label/run/main null — the
    reconstruction cannot know what the rip run knew.
    `encodes` starts empty and is appended by the transcoder as {"file",
    "src", "dur_s"}.

    Args:
        tmdb_id: TMDB id as string
        title: TMDB display title with year, e.g. "Mamma Mia! (2008)"
        runtime_s: TMDB runtime in seconds, None when unknown
        volume_label: Disc volume label the rip came from, None when unknown
        run: Short-uuid of the rip run that produced the file, None unknown
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
                run: str, staged: list[int], episodes: list[dict]) -> dict:
    """The TV document at rip time: the shared identity block plus the
    per-season record — episodes and the disc index.

    One `seasons` entry per season: `episodes` entries are {"ep", "file",
    "t", "dur_s", "runtime_s", "ratio", "band"} — one per staged episode,
    ratio grading the staged duration against that episode's TMDB runtime in
    the shared RATIO_GREEN band; a double (S02E05-E06) records one row under
    its lo episode. `discs` is the append-only provenance index, one entry
    per rip run: {"volume_label", "run", "staged"} — which disc, which run
    log, the episodes that run staged. Discs rip in order into one title
    folder (staging has no season subfolders), so each disc's flow merges
    its season into the existing document (load, merge, write). `encodes`
    starts empty and is appended by the transcoder as {"file", "src",
    "dur_s"}.

    Args:
        tmdb_id: TMDB show id as string
        title: TMDB display title with year, e.g. "Show (2019)"
        season: Season number the disc belongs to
        volume_label: Disc volume label the rip came from
        run: Short-uuid of the rip run that produced the file
        staged: Episode numbers this run staged (from its assignment map)
        episodes: Per-episode record entries

    Returns:
        The TV document, key order fixed for diff-friendly files
    """
    return {
        "tmdb": {"id": tmdb_id, "type": "tv", "title": title},
        "seasons": [{
            "season": season,
            "episodes": list(episodes),
            "discs": [{"volume_label": volume_label, "run": run,
                       "staged": list(staged)}],
        }],
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


def _tv_seasons(doc: dict) -> list[dict]:
    """The document's season entries; an old one-season document (tmdb.season
    + episodes, no seasons key — the first schema) reads as one entry with
    no disc provenance. Malformed entries drop out — callers see what is
    usable, nothing raises.

    Args:
        doc: A loaded TV document

    Returns:
        The usable season entries, in the document's own order
    """
    seasons = doc.get("seasons")
    if isinstance(seasons, list):
        return [s for s in seasons if isinstance(s, dict)]
    tmdb = doc.get("tmdb")
    season = tmdb.get("season") if isinstance(tmdb, dict) else None
    if not isinstance(season, int):
        return []
    eps = doc.get("episodes")
    return [{"season": season,
             "episodes": [r for r in eps if isinstance(r, dict)]
             if isinstance(eps, list) else [],
             "discs": []}]


def merge_tv_document(old: dict, doc: dict) -> dict:
    """A same-show re-rip's document merged into the folder's existing one.

    Per season: the existing entries kept, this run's appended — an ep
    already recorded is replaced by this run's row, not duplicated (each run
    probes the season's staged files, so its rows are the current actuals);
    the run's disc entry appends to the season's index (re-merging the same
    run replaces its entry — idempotent). Seasons the new document doesn't
    touch survive untouched; the merged list sorts by season number,
    diff-friendly whatever order the discs ripped in. An old one-season
    document migrates to the seasons shape on the merge. encodes start
    empty — the record travels on with the new rip.

    Args:
        old: The existing document (same tmdb id, caller-checked)
        doc: This run's document

    Returns:
        The merged document, this run's shape throughout
    """
    old_by_season = {s["season"]: s for s in _tv_seasons(old)
                     if isinstance(s.get("season"), int)}
    merged = dict(doc)
    seasons: list[dict] = []
    for entry in doc["seasons"]:
        prev = old_by_season.pop(entry.get("season"), None)
        if prev is None:
            seasons.append(entry)
            continue
        old_rows = prev.get("episodes")
        redone = {r.get("ep") for r in entry.get("episodes", [])}
        episodes = [r for r in (old_rows if isinstance(old_rows, list) else [])
                    if isinstance(r, dict) and r.get("ep") not in redone] \
            + list(entry.get("episodes", []))
        entry_discs = entry.get("discs") or []
        run = entry_discs[0].get("run") if entry_discs else None
        old_discs = prev.get("discs")
        discs = [d for d in (old_discs if isinstance(old_discs, list) else [])
                 if isinstance(d, dict) and d.get("run") != run] \
            + list(entry_discs)
        seasons.append({**entry, "episodes": episodes, "discs": discs})
    seasons.extend(old_by_season.values())
    seasons.sort(key=lambda s: s["season"] if isinstance(s.get("season"), int) else -1)
    merged["seasons"] = seasons
    return merged


# --- Library checker (skive validate; map.md decision 7) ---------------------

console = Console()

STAGING_NAME = re.compile(r"_[te]\d+")
"""A leftover staging label in a library file name — the MakeMKV junk tags
and staged `_tNN` title numbers (and the older `_eNN` extras labels) that
must never survive into the library (the Mamma Mia extras pollution;
CONTEXT.md: Library checker)."""

STAGING_LABEL = re.compile(r"^(?P<base>.+)_[te](?P<num>\d+)(?P<part>\.p\d+)?$")
"""A staging-labelled stem split for renaming: `<base>_<tNN|eNN>[.pN]` — the
fixer's parse, greedy so the LAST label wins (a base may itself contain
underscores)."""

SEASON_DIR = re.compile(r"^(?:Season (\d{2,})|Specials)$")
"""A TV season folder under a show folder; `Specials` is season 0 — the folder
name the pipeline writes for season 0 (CONTEXT.md: Season folder)."""

YEAR_SUFFIX = re.compile(r"^(.*) \((\d{4})\)$")
"""The `<Name (Year)>` title-folder shape; the year is what makes a legacy
folder's name+year TMDB search worth running."""

EPISODE_IN_NAME = re.compile(r"S(\d+)E(\d+)")
"""An episode number inside a library file name (`..._S02E05`, transcode's
`... - S02E05`); the season comes from the Season NN folder, the E from here."""

DOUBLE_EPISODE_TAIL = re.compile(r"-E(\d+)$")
"""The `-E..` tail of a double-episode file name (`... S02E05-E06`); a double
is one file, never split (CONTEXT.md: Double episode) — its one ratio row
covers every episode in the range."""


PAL_NTSC_SPEEDUP = 0.0417
"""The PAL-vs-NTSC runtime disparity (25/24 fps ≈ 4.17%): PAL discs run this
much shorter than the 24fps runtime TMDB records, and RATIO_GREEN already
absorbs it. A ratio beyond the band by MORE than this reads red — no
standards conversion explains it (the checker's display tier; the document
record keeps green/yellow)."""


def ratio_severity(ratio: float) -> str:
    """The checker's tier for an exact ratio: green inside RATIO_GREEN,
    yellow outside it, red outside by more than the PAL-vs-NTSC speedup.

    Args:
        ratio: The exact disc÷TMDB duration quotient

    Returns:
        "green", "yellow" or "red"
    """
    if RATIO_GREEN[0] <= ratio <= RATIO_GREEN[1]:
        return "green"
    if RATIO_GREEN[0] - PAL_NTSC_SPEEDUP <= ratio <= RATIO_GREEN[1] + PAL_NTSC_SPEEDUP:
        return "yellow"
    return "red"


def band_delta(ratio: float) -> float:
    """How far an exact ratio sits from the norm band, 2dp: inside it the
    margin to the nearest edge; outside it the signed overshoot — negative
    short of the band, positive over it.

    Args:
        ratio: The exact disc÷TMDB duration quotient

    Returns:
        The 2dp signed distance from RATIO_GREEN
    """
    lo, hi = RATIO_GREEN
    if ratio < lo:
        return round(ratio - lo, 2)
    if ratio > hi:
        return round(ratio - hi, 2)
    return round(min(ratio - lo, hi - ratio), 2)


class Finding(NamedTuple):
    """One row of the `skive validate` report.

    folder is the title folder as walked ("movies/Mamma Mia! (2008)"); check
    names the rule ("ratio", "staging name", "missing episodes", "duplicate
    id", "unidentified", "unreadable"); detail is the one-line human text.
    band is set on ratio rows only — "green" inside RATIO_GREEN, "yellow"
    outside it, "red" outside by more than the PAL-vs-NTSC speedup — and None
    elsewhere; a ratio row with band None is a blank cell — a file graded
    against an unknown runtime, never yellow. path is the folder-relative
    file on file-scoped rows (ratio, staging name, unreadable); ratio is the
    row's 2dp disc÷TMDB quotient; delta is the 2dp amount inside the norm
    (the margin to the nearest edge) or outside it (negative short, positive
    over).
    """

    folder: str
    check: str
    detail: str
    band: str | None = None
    path: str | None = None
    ratio: float | None = None
    delta: float | None = None


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
    return [Finding(rel, "staging name", str(p.relative_to(folder)),
                    path=str(p.relative_to(folder)))
            for p in sorted(folder.rglob("*"))
            if p.is_file() and STAGING_NAME.search(p.name)]


def fix_staging_names(root: Path) -> list[tuple[Path, Path]]:
    """The --fix arm: rename every extras/ file wearing a `_tNN`/`_eNN`
    staging label to the pipeline's own `<base> - Extra NN[.pN].mkv` form —
    the label's number, rolled forward past names already taken (makemkv's
    staging pattern). Staging-labelled files elsewhere (a stray at folder
    root) are left in place: never guessed, the report keeps flagging them.
    Idempotent — a clean tree renames nothing.

    Args:
        root: The library root (movies/ and tv/ walked for extras/ dirs)

    Returns:
        The (old, new) renames applied, in walk order
    """
    renames: list[tuple[Path, Path]] = []
    for extras in sorted(p for p in root.rglob("extras") if p.is_dir()):
        for p in sorted(extras.iterdir()):
            if not p.is_file():
                continue
            m = STAGING_LABEL.match(p.stem)
            if m is None:
                continue
            base, num = m.group("base"), int(m.group("num"))
            part = m.group("part") or ""
            n = num
            while (extras / f"{base} - Extra {n:02d}{part}.mkv").exists():
                n += 1
            target = extras / f"{base} - Extra {n:02d}{part}.mkv"
            p.rename(target)
            renames.append((p, target))
    return renames


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
                   runtime_s: int | None,
                   ep_label: str | None = None) -> Finding:
    """One ratio row: the file's probed duration graded in the shared band.

    Unknown runtime (or a zero-duration probe read): a blank cell — band None,
    never yellow (grade_main's no-false-yellow rule). ep_label marks a
    double-episode file's row with its range ("E05-E06") so the human reads
    the row as one file, not two episodes.
    """
    graded = grade_main(dur_s, runtime_s)
    if graded is None:
        return Finding(rel, "ratio", relpath, path=relpath)
    label = f"  {ep_label}" if ep_label else ""
    exact = graded[0]
    return Finding(rel, "ratio", f"{relpath}{label}  {exact:.2f}",
                   ratio_severity(exact), path=relpath,
                   ratio=round(exact, 2), delta=band_delta(exact))


def _unreadable_finding(rel: str, relpath: str) -> Finding:
    return Finding(rel, "unreadable", f"{relpath}: duration unreadable",
                   path=relpath)


def _double_runtime(runtimes: dict[int, int | None], lo: int, hi: int) -> int | None:
    """The runtime a double-episode file grades against: the summed runtimes
    over lo..hi when every episode in the range has a known runtime, else the
    single lo runtime. A range the sources cannot fully price grades at lo
    alone rather than guessing the missing runtimes.

    Args:
        runtimes: Per-episode runtime in seconds, None when unknown
        lo: The double's first episode
        hi: The double's last episode (== lo for a single-episode file)

    Returns:
        Summed runtime in seconds, the lo runtime alone, or None
    """
    if all(runtimes.get(ep) for ep in range(lo, hi + 1)):
        return sum(runtimes[ep] for ep in range(lo, hi + 1))
    return runtimes.get(lo)


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

    Episode runtimes come from the season's entry in the show document
    (offline) or the TMDB season list (legacy folders); an episode with
    neither is a blank cell, never yellow. A double episode (`S..E05-E06`,
    one file) is one row: every episode in its range counts as present, and
    the file's duration grades against the range's summed runtime. The
    missing check grades the folder's `S..E..` names against the TMDB season
    list when it fetched, else the document's episode rows; with neither
    source it skips — never guesses. A season-list fetch failure degrades to
    the doc source with one stderr warning.
    """
    season = int(SEASON_DIR.match(season_dir.name).group(1) or "00")
    findings: list[Finding] = []
    doc_rows: dict[int, dict] = {}
    if doc is not None:
        entry = next((s for s in _tv_seasons(doc) if s.get("season") == season),
                     None)
        if entry is not None:
            rows = entry.get("episodes")
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
    episode_files: dict[Path, tuple[int, int]] = {}
    for p in sorted(season_dir.glob("*.mkv")):
        if not p.is_file():
            continue
        m = EPISODE_IN_NAME.search(p.stem)
        if not m:
            continue
        lo = int(m.group(2))
        tail = DOUBLE_EPISODE_TAIL.search(p.stem)
        hi = max(lo, int(tail.group(1))) if tail else lo
        episode_files.setdefault(p, (lo, hi))
        for ep in range(lo, hi + 1):
            files.setdefault(ep, p)
    for p in sorted(episode_files):
        lo, hi = episode_files[p]
        relpath = str(p.relative_to(folder))
        dur = probe_duration(p, run)
        if dur is None:
            findings.append(_unreadable_finding(rel, relpath))
            continue
        label = _episode_runs(list(range(lo, hi + 1))) if hi > lo else None
        findings.append(_ratio_finding(rel, relpath, dur,
                                       _double_runtime(runtimes, lo, hi),
                                       label))
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


def is_issue(finding: Finding) -> bool:
    """The issues-only filter: every non-ratio check is an issue; a ratio
    row is an issue when off-band (yellow/red) or when it is a graded
    `extras/` file — a full-length extra beside the main, the two-
    candidates lead, even when its own grade reads green.

    Args:
        finding: One report row

    Returns:
        True when the row belongs in the issues view
    """
    if finding.check != "ratio":
        return True
    if finding.band not in ("green", None):
        return True
    return bool(finding.path and finding.path.startswith("extras/"))


def _ratio_action(r: Finding) -> str:
    """One manual-review sentence for a ratio issue row."""
    where = r.path or r.detail
    is_movie_main = r.folder.startswith("movies/") \
        and not (r.path or "").startswith("extras/")
    reading = f"{'main ' if is_movie_main else ''}{where} reads {r.ratio:.2f}"
    if r.band == "green":  # a graded extra inside the norm
        return (f"{reading} — a full-length extra beside the main (the "
                f"two-candidates signature); confirm the right title is the main")
    off = f"{abs(r.delta):.0%} short" if r.delta < 0 else f"{abs(r.delta):.0%} over"
    extra = (r.path or "").startswith("extras/")
    if r.band == "red":
        if extra:
            return (f"{reading} ({off}) — a feature-length extra far off the "
                    f"film runtime; check which disc title this is")
        return (f"{reading} ({off}) — beyond what any PAL-vs-NTSC conversion "
                f"or cut difference explains; suspect a partial/broken encode "
                f"or the wrong file as main: verify against validation.json "
                f"and re-rip if needed")
    if extra:
        return (f"{reading} ({off}) — a feature-length extra off the film "
                f"runtime; check which disc title this is")
    if r.delta < 0:
        return (f"{reading} ({off}) — just outside the norm; check the TMDB "
                f"runtime names the intended cut before re-ripping")
    return (f"{reading} ({off}) — likely an extended cut vs the TMDB "
            f"runtime; confirm which cut you want")


def _row_recommendation(f: Finding) -> str:
    """The one action sentence for a single issue row (the JSON embeds it
    per finding; the folder plan reuses it per row).

    Args:
        f: An issue row (is_issue)

    Returns:
        The sentence
    """
    if f.check == "ratio":
        return _ratio_action(f)
    if f.check == "unreadable":
        return f"{f.path or f.detail}: duration unreadable — check the file"
    if f.check == "missing episodes":
        return f"{f.detail} — re-check the episode map or re-rip the disc"
    if f.check == "duplicate id":
        return f"{f.detail} — deduplicate the folders"
    if f.check == "unidentified":
        return (f"{f.detail} — identify the folder manually (re-rip with a "
                f"TMDB match or add validation.json)")
    if f.check == "staging name":
        return (f"rename {f.path or f.detail} to the ` - Extra NN` form "
                f"(`skive validate --fix` does the extras/ files)")
    return f.detail


def _folder_actions(rows: list[Finding]) -> list[str]:
    """The action lines for one folder's issues: the per-row sentences in
    walk order, staging names as one counted line pointing at --fix."""
    actions = [_row_recommendation(r) for r in rows
               if r.check != "staging name"]
    staging = [r for r in rows if r.check == "staging name"]
    if staging:
        actions.append(f"{len(staging)} file(s) still carry `_tNN` staging "
                       f"labels — run `skive validate --fix` to rename them")
    return actions


def recommendations(findings: list[Finding]) -> list[dict]:
    """The manual-review action plan: one entry per folder carrying at least
    one issue, worst priority first (red, then yellow, then note), walk
    order within a tier. Each entry is {"folder", "priority", "actions"}.

    Args:
        findings: The walk's findings (unfiltered — the issues filter runs here)

    Returns:
        The plan entries, worst first
    """
    by_folder: dict[str, list[Finding]] = {}
    for f in findings:
        if is_issue(f):
            by_folder.setdefault(f.folder, []).append(f)
    plan = []
    for folder, rows in by_folder.items():
        prio = "note"
        for r in rows:
            if r.check == "ratio" and r.band == "red":
                prio = "red"
            elif r.check == "ratio" and r.band == "yellow" and prio != "red":
                prio = "yellow"
            elif r.check == "ratio" and r.band == "green" \
                    and prio not in ("red", "yellow"):
                prio = "yellow"  # the full-length-extra lead still asks
        plan.append({"folder": folder, "priority": prio,
                     "actions": _folder_actions(rows)})
    plan.sort(key=lambda e: {"red": 0, "yellow": 1, "note": 2}[e["priority"]])
    return plan


def _band_style(band: str | None) -> str | None:
    """The rich style for a band: red reads bold — the alarm tier."""
    return "bold red" if band == "red" else band


def _delta_cell(f: Finding) -> object:
    """The Δ cell: the signed 0.xx amount outside the norm (− short, +
    over), or the plain margin when inside it; empty on rows with no grade.
    Compact by design — the worded phrasing lives in the action plan."""
    if f.ratio is None or f.delta is None:
        return ""
    if f.band != "green":
        return Text(f"{f.delta:+.2f}", style=_band_style(f.band))
    return Text(f"in {f.delta:.2f}", style="green")


def _collapse_staging(issues: list[Finding]) -> list[Finding]:
    """One staging-name row per folder ("N files with _tNN names, e.g. …"),
    at the folder's first staging position — hundreds of per-file rows
    otherwise drown the issues they accompany."""
    counts: dict[str, list[str]] = {}
    for f in issues:
        if f.check == "staging name":
            counts.setdefault(f.folder, []).append(f.path or f.detail)
    collapsed: list[Finding] = []
    emitted: set[str] = set()
    for f in issues:
        if f.check != "staging name":
            collapsed.append(f)
        elif f.folder not in emitted:
            paths = counts[f.folder]
            collapsed.append(Finding(f.folder, "staging name",
                                     f"{len(paths)} files with _tNN names, "
                                     f"e.g. {paths[0]}"))
            emitted.add(f.folder)
    return collapsed


def _findings_table(findings: list[Finding]) -> Table:
    """The shared rich table (repo print_table style): folder, check,
    detail, and the Δ-from-norm column on graded rows."""
    table = Table()
    table.add_column("folder", style="cyan")
    table.add_column("check", style="yellow", width=17)
    table.add_column("detail")
    table.add_column("Δ", width=8)
    for f in findings:
        detail = Text(f.detail, style=_band_style(f.band)) \
            if f.band else f.detail
        table.add_row(f.folder, f.check, detail, _delta_cell(f))
    return table


def print_findings(findings: list[Finding], root: Path) -> None:
    """The ONE rich table: every graded ratio, every finding, in walk order.
    Prints only — nothing here writes or deletes.

    Args:
        findings: The walk's findings
        root: The share root the walk covered
    """
    console.print(f"\nLibrary validation — {root}")
    if not findings:
        console.print("no findings")
        return
    console.print(_findings_table(findings))


def print_issues(findings: list[Finding], root: Path) -> None:
    """The issues-only view: off-band ratios and every other finding (green
    mains stay out, staging names collapse to one row per folder), then the
    per-folder action plan. Prints only — nothing here writes or deletes.

    Args:
        findings: The walk's findings (unfiltered — the filter runs here)
        root: The share root the walk covered
    """
    issues = [f for f in findings if is_issue(f)]
    console.print(f"\nLibrary validation — {root} (issues only)")
    if not issues:
        console.print("no issues")
        return
    console.print(_findings_table(_collapse_staging(issues)))
    console.print("\n[bold]Manual review — action plan[/bold]")
    for entry in recommendations(findings):
        console.print(f"  [bold]{entry['folder']}[/bold] — {entry['priority']}")
        for action in entry["actions"]:
            console.print(f"    • {action}")


def json_report(findings: list[Finding], root: Path, *, issues: bool = False) -> dict:
    """The report as data: the band definitions, summary counts, every
    finding row (parsed ratio/delta/band), and the action plan — the same
    data the tables show, for anything that consumes it downstream.

    Args:
        findings: The walk's findings
        root: The library root the walk covered
        issues: Filter to issue rows only (is_issue), like --issues

    Returns:
        The report dict; callers serialize with json.dumps
    """
    rows = [f for f in findings if not issues or is_issue(f)]
    ratio_counts = {"green": 0, "yellow": 0, "red": 0, "blank": 0}
    other: dict[str, int] = {}
    for f in rows:
        if f.check == "ratio":
            ratio_counts[f.band or "blank"] += 1
        else:
            other[f.check] = other.get(f.check, 0) + 1
    return {
        "root": str(root),
        "ratio_band": {"norm": list(RATIO_GREEN),
                       "red_beyond": [round(RATIO_GREEN[0] - PAL_NTSC_SPEEDUP, 4),
                                      round(RATIO_GREEN[1] + PAL_NTSC_SPEEDUP, 4)]},
        "summary": {"findings": len(rows), "ratio": ratio_counts,
                    "other": other},
        "findings": [{"folder": f.folder, "check": f.check, "path": f.path,
                      "detail": f.detail, "band": f.band, "ratio": f.ratio,
                      "delta": f.delta,
                      "recommendation": _row_recommendation(f) if is_issue(f)
                      else None} for f in rows],
        "recommendations": recommendations(findings),
    }


def run_validate(videos_root: Path | None = None, *, run=subprocess.run,
                 json_: bool = False, issues: bool = False,
                 fix: bool = False) -> None:
    """The `skive validate` entry: resolve the share root ($VIDEOS_MOUNT, else
    /mnt/videos), optionally apply the safe automatic fix (--fix renames
    staging-labelled extras files), then walk its library/ and print — the
    one table by default, the issues view with --issues, JSON with --json
    (the flags compose: --json --issues emits the filtered report; --fix
    adds a "fixed" list and the walk reflects the applied renames).
    Findings are leads, not verdicts; a missing library warns instead of
    walking the wrong tree.

    Args:
        videos_root: Share root override (tests); None reads $VIDEOS_MOUNT
        run: subprocess.run callable for the ffprobe probes
        json_: Emit the report as JSON instead of a table
        issues: Show only the issues plus the action plan (or filter the JSON)
        fix: Apply the staging-name renames before reporting
    """
    share = videos_root or Path(os.environ.get("VIDEOS_MOUNT", "/mnt/videos"))
    library = share / "library"
    if not library.is_dir():
        message = f"no library/ under {share}"
        if json_:
            print(json.dumps({"root": str(library), "error": message,
                              "findings": [], "recommendations": []}, indent=2))
        else:
            print(f"skive: validate: {message}", file=sys.stderr)
        return
    renames = fix_staging_names(library) if fix else []
    findings = validate_library(library, run=run)
    if json_:
        report = json_report(findings, library, issues=issues)
        if fix:
            report["fixed"] = [{"from": str(o.relative_to(library)),
                                "to": str(n.relative_to(library))}
                               for o, n in renames]
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    if renames:
        console.print(f"\nFixed — {len(renames)} staging label(s) renamed:")
        for old, new in renames:
            console.print(f"  {old.relative_to(library)} → "
                          f"{new.relative_to(library)}")
    if issues:
        print_issues(findings, library)
    else:
        print_findings(findings, library)
