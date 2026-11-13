"""TMDB API integration — search, fetch by id. Stdlib only (urllib)."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from rich.text import Text

from skive import ui


@dataclass
class TMDBPick:
    """A TMDB result from search or by-id fetch."""
    title: str
    year: str  # "-" if none
    original_language: str  # "-" if none
    type: str  # "movie" or "tv"
    id: str  # TMDB id as string
    # Movie runtime in seconds (TMDB reports minutes). None = unknown: a fake 0
    # would read as a real duration and could produce a false-yellow ratio cell
    # (the tv_match.audit_cell no-false-yellow rule). TV stays None; episode
    # runtimes arrive at season level via fetch_season.
    runtime_secs: int | None = None


def _api_key() -> str:
    api_key = os.environ.get("TMDB_API_KEY")
    if not api_key:
        raise ValueError("no $TMDB_API_KEY in env")
    return api_key


def _http_get_json(url: str, timeout: int = 15) -> dict:
    """Fetch JSON from a URL, return parsed dict. Raises on failure."""
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError, OSError) as e:
        message = str(e)
        if api_key := os.environ.get("TMDB_API_KEY"):
            message = message.replace(api_key, "***")
        raise RuntimeError(f"TMDB request failed: {message}") from e


def require_online() -> None:
    """Probe TMDB API; exit if offline."""
    try:
        api_key = _api_key()
    except ValueError:
        ui.fail("no $TMDB_API_KEY in env — set it and re-run.")
        sys.exit(1)

    url = f"https://api.themoviedb.org/3/configuration?api_key={api_key}"
    try:
        _http_get_json(url, timeout=5)
    except RuntimeError as e:
        ui.fail(f"TMDB is unreachable ({e})")
        sys.exit(1)


def title_matches(want: str, hit: str) -> bool:
    """Two titles equal modulo case and punctuation — "X-Men" == "x men";
    "X-Men" != "X-Men: Apocalypse". The name+year callers that must never
    guess (the validation backfill, its repair) accept a hit only when its
    title also matches the folder's name."""
    norm = lambda s: "".join(c for c in s.casefold() if c.isalnum())
    return bool(hit) and norm(want) == norm(hit)


def parse_tmdbid(arg: str, kind: str) -> tuple[str, str]:
    """Parse --tmdbid argument, return (type, id).

    Args:
        arg: TMDB id or URL
        kind: "movie" or "tv" — expected type

    Raises:
        ValueError: Invalid id or wrong type

    Returns:
        (type, id) where type is "movie" or "tv"
    """
    # URL pasting: themoviedb.org/movie/N or themoviedb.org/tv/N
    if "themoviedb.org" in arg:
        import re
        m = re.search(r'/(\w+)/(\d+)', arg)
        if not m:
            raise ValueError(f"no movie or tv id found in URL '{arg}'")
        url_type = m.group(1)
        num = m.group(2)
        if url_type not in ("movie", "tv"):
            raise ValueError(f"unknown type in URL: {url_type}")
        if url_type != kind:
            raise ValueError(f"that is a {url_type} id; {kind} needs {'tv:N' if kind == 'tv' else 'a movie id'}")
        return url_type, num

    # Type prefix: movie:123 or tv:123
    if ":" in arg:
        type_prefix, num = arg.split(":", 1)
        if type_prefix not in ("movie", "tv"):
            raise ValueError(f"unknown type prefix: {type_prefix}")
        if type_prefix != kind:
            raise ValueError(f"{type_prefix} id passed to {kind} command")
        try:
            int(num)
        except ValueError:
            raise ValueError(f"bad TMDB id '{arg}'")
        return type_prefix, num

    # Bare id: only allowed for movie, TV requires tv:N prefix
    if kind == "tv":
        raise ValueError("TV requires tv:N or a /tv/N URL; bare id is ambiguous")
    try:
        int(arg)
    except ValueError:
        raise ValueError(f"bad TMDB id '{arg}'")
    return "movie", arg


def _runtime_secs_from_details(data: dict) -> int | None:
    """Convert a details payload's runtime (TMDB minutes) to seconds.

    Missing runtime stays None — never a fake 0, which downstream would read
    as a real duration (the tv_match.audit_cell no-false-yellow rule).
    """
    minutes = data.get("runtime")
    return minutes * 60 if minutes else None


def _resolve_movie_runtime(api_key: str, movie_id: str) -> int | None:
    """Resolve a movie's runtime (seconds) from its details endpoint.

    Search results carry no runtime, so this runs after the user's pick. Any
    fetch failure returns None — the pick already succeeded; runtime is an
    enhancement and must not kill it.
    """
    url = f"https://api.themoviedb.org/3/movie/{movie_id}?api_key={api_key}"
    try:
        data = _http_get_json(url)
    except RuntimeError:
        return None
    return _runtime_secs_from_details(data)


def fetch_by_id(type_: str, id_: str) -> TMDBPick:
    """Fetch a TMDB entry by id.

    Args:
        type_: "movie" or "tv"
        id_: TMDB id (as string)

    Returns:
        TMDBPick with the resolved data

    Raises:
        RuntimeError: On API failure
        ValueError: On bad id
    """
    api_key = _api_key()

    url = f"https://api.themoviedb.org/3/{type_}/{id_}?api_key={api_key}"
    try:
        data = _http_get_json(url)
    except RuntimeError as e:
        raise RuntimeError(f"TMDB fetch of {type_}/{id_} failed: {e}") from e

    # TMDB TSV: title // name, (release_date // first_air_date // "-")[0:4], original_language // "-", type, id
    title = data.get("title") or data.get("name", "-")
    date_str = data.get("release_date") or data.get("first_air_date", "-")
    year = date_str[:4] if date_str and date_str != "-" else "-"
    orig_lang = data.get("original_language", "-")
    tmdb_id = str(data.get("id", "-"))

    # Movie details already carry the runtime (converted to seconds); TV stays
    # unknown — episode runtimes arrive at season level via fetch_season.
    runtime_secs = _runtime_secs_from_details(data) if type_ == "movie" else None

    return TMDBPick(title=title, year=year, original_language=orig_lang, type=type_, id=tmdb_id,
                    runtime_secs=runtime_secs)


def search_loop(kind: str, prefill: str = "") -> TMDBPick:
    """Interactive TMDB search: run the prefill, show table, pick one.

    `prefill` (the disc's volume label) skips the first query prompt — the
    search runs straight away and lands on the results table. At the table,
    `s` drops back to a typed query. Re-prompts on garbage query or zero
    results. 'q' aborts (exit 1).

    Args:
        kind: "movie" or "tv" — search index to use
        prefill: Query to run before prompting ("" to prompt immediately)

    Returns:
        TMDBPick with the user's selection

    Raises:
        SystemExit: On 'q' or unrecoverable error
    """
    try:
        api_key = _api_key()
    except ValueError:
        ui.fail("no $TMDB_API_KEY in env — set it and re-run")
        sys.exit(1)

    query: str | None = prefill or None
    while True:
        if query is None:
            query = ui.ask("TMDB search", choices="Enter skip · q quit")

            if query.lower() == "q":
                ui.info("quit — re-run later")
                sys.exit(1)

            if not query:
                ui.info("skipped — re-run to search again")
                continue

        # URL-encode the query
        q_enc = urllib.parse.quote(query)
        url = f"https://api.themoviedb.org/3/search/{kind}?api_key={api_key}&query={q_enc}"

        try:
            data = _http_get_json(url)
        except RuntimeError as e:
            ui.fail(f"TMDB request failed ({e}) — re-run when network is available")
            sys.exit(1)

        results = data.get("results", [])
        if not results:
            ui.info(f"no TMDB matches for '{query}'")
            query = None
            continue

        # Show top 5 in the shared table look; only the pick number is styled
        top = results[:5]
        table = ui.table("#", "Title", "Year", "Lang", "ID",
                         title=f'TMDB "{query}" — top {len(top)} {kind} results')
        for i, r in enumerate(top, 1):
            title = r.get("title") or r.get("name", "-")
            date_str = r.get("release_date") or r.get("first_air_date", "-")
            year = date_str[:4] if date_str and date_str != "-" else "-"
            lang = r.get("original_language", "-")
            rid = str(r.get("id", "-"))
            table.add_row(Text(str(i), style="bold"), title, year, lang, rid)

        with ui.suspend():
            ui.console.print(table)

        while True:
            pick_str = ui.ask(f"Pick 1-{len(top)}", choices="s new search · q quit", default="1")

            if pick_str.lower() == "q":
                ui.info("quit — re-run later")
                sys.exit(1)

            if pick_str.lower() == "s":
                query = None
                break

            try:
                pick_idx = int(pick_str)
                if 1 <= pick_idx <= len(top):
                    r = results[pick_idx - 1]
                    title = r.get("title") or r.get("name", "-")
                    date_str = r.get("release_date") or r.get("first_air_date", "-")
                    year = date_str[:4] if date_str and date_str != "-" else "-"
                    orig_lang = r.get("original_language", "-")
                    tmdb_id = str(r.get("id", "-"))
                    # Search results lack runtime, so a movie pick resolves it
                    # from the details endpoint (failure degrades to unknown).
                    runtime_secs = _resolve_movie_runtime(api_key, tmdb_id) if kind == "movie" else None
                    return TMDBPick(title=title, year=year, original_language=orig_lang, type=kind, id=tmdb_id,
                                    runtime_secs=runtime_secs)
                else:
                    ui.warn(f"pick 1-{len(top)}")
            except ValueError:
                ui.warn(f"invalid choice '{pick_str}'")


def search_first(kind: str, query: str, year: str | None = None) -> TMDBPick | None:
    """The first TMDB search result for `query` — no table, no prompt.

    `year` (a "YYYY" string) filters the hits: the first result whose
    release year matches wins, None when none does. TMDB sorts search by
    popularity, so a bare name's top hit is often a sequel or a sibling
    ("X-Men" returns X-Men: Apocalypse before X-Men) — callers that know
    the year (a title folder's suffix) must pass it.

    The library checker's identity fallback (skive validate): legacy folders
    resolve by name+year to a hit, a LEAD the report shows, never a verdict.
    None on zero results; RuntimeError on network failure like every
    fetch here — the caller degrades that folder instead of aborting. Search
    results carry no runtime; callers needing one fetch details separately
    (fetch_by_id).

    Args:
        kind: "movie" or "tv" — search index to use
        query: The search text
        year: Release year the hit must carry, None to take the raw top hit

    Returns:
        The matching result as a TMDBPick, or None when nothing matched

    Raises:
        RuntimeError: On API failure
    """
    api_key = _api_key()
    q_enc = urllib.parse.quote(query)
    url = f"https://api.themoviedb.org/3/search/{kind}?api_key={api_key}&query={q_enc}"
    data = _http_get_json(url)
    for r in data.get("results", []):
        if year is not None:
            date_str = r.get("release_date") or r.get("first_air_date") or ""
            if date_str[:4] != year:
                continue
        break
    else:
        return None
    title = r.get("title") or r.get("name", "-")
    date_str = r.get("release_date") or r.get("first_air_date", "-")
    date_year = date_str[:4] if date_str and date_str != "-" else "-"
    return TMDBPick(title=title, year=date_year,
                    original_language=r.get("original_language", "-"),
                    type=kind, id=str(r.get("id", "-")))


def fetch_season(tv_id: str, season: int) -> list[tuple[int, str, int]]:
    """Fetch a TV season's episodes from TMDB.

    Args:
        tv_id: TMDB TV show id
        season: Season number (0 for specials)

    Returns:
        List of (episode_number, name, runtime_seconds) sorted by episode_number.
        TMDB reports minutes; they are converted to seconds here so the
        duration matcher (which works in seconds) never sees mixed units.
        Episodes with no runtime are included with runtime=0.

    Raises:
        RuntimeError: On API failure
    """
    api_key = _api_key()

    url = f"https://api.themoviedb.org/3/tv/{tv_id}/season/{season}?api_key={api_key}"
    try:
        data = _http_get_json(url)
    except RuntimeError as e:
        raise RuntimeError(f"TMDB season fetch failed (tv/{tv_id}/season/{season}): {e}") from e

    episodes: list[tuple[int, str, int]] = []
    for ep in data.get("episodes", []):
        ep_num = ep.get("episode_number")
        name = ep.get("name", "")
        runtime = (ep.get("runtime") or 0) * 60  # TMDB minutes -> seconds
        if ep_num is not None:
            episodes.append((ep_num, name, runtime))

    # Sort by episode_number
    episodes.sort(key=lambda x: x[0])
    return episodes