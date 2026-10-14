"""TMDB API integration — search, fetch by id. Stdlib only (urllib)."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

console = Console()


@dataclass
class TMDBPick:
    """A TMDB result from search or by-id fetch."""
    title: str
    year: str  # "-" if none
    original_language: str  # "-" if none
    type: str  # "movie" or "tv"
    id: str  # TMDB id as string


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
        print("skive: no $TMDB_API_KEY in env — set it and re-run.", file=sys.stderr)
        sys.exit(1)

    url = f"https://api.themoviedb.org/3/configuration?api_key={api_key}"
    try:
        _http_get_json(url, timeout=5)
    except RuntimeError as e:
        print(f"skive: TMDB is unreachable ({e})", file=sys.stderr)
        sys.exit(1)


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

    return TMDBPick(title=title, year=year, original_language=orig_lang, type=type_, id=tmdb_id)


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
        print("skive: no $TMDB_API_KEY in env — set it and re-run.", file=sys.stderr)
        sys.exit(1)

    query: str | None = prefill or None
    while True:
        if query is None:
            console.print("\nTMDB search (blank=skip, q=quit): ", end="")
            query = Prompt.ask("") or ""

            if query.lower() == "q":
                print("skive: quit — re-run later", file=sys.stderr)
                sys.exit(1)

            if not query:
                print("skive: skipped — re-run to search again", file=sys.stderr)
                continue

        # URL-encode the query
        q_enc = urllib.parse.quote(query)
        url = f"https://api.themoviedb.org/3/search/{kind}?api_key={api_key}&query={q_enc}"

        try:
            data = _http_get_json(url)
        except RuntimeError as e:
            print(f"skive: TMDB request failed ({e})", file=sys.stderr)
            print("skive: re-run when network is available", file=sys.stderr)
            sys.exit(1)

        results = data.get("results", [])
        if not results:
            print(f"skive: no TMDB matches for '{query}'", file=sys.stderr)
            query = None
            continue

        # Show top 5 in a rich table
        table = Table(title=f'TMDB "{query}" — top {len(results[:5])} {kind} results')
        table.add_column("#", style="cyan", width=4)
        table.add_column("Title", style="white")
        table.add_column("Year", style="yellow", width=6)
        table.add_column("Lang", style="magenta", width=4)
        table.add_column("ID", style="dim", width=6)

        for i, r in enumerate(results[:5], 1):
            title = r.get("title") or r.get("name", "-")
            date_str = r.get("release_date") or r.get("first_air_date", "-")
            year = date_str[:4] if date_str and date_str != "-" else "-"
            lang = r.get("original_language", "-")
            rid = str(r.get("id", "-"))
            table.add_row(str(i), title, year, lang, rid)

        console.print(table)

        while True:
            console.print(f"pick 1-{len(results[:5])} (Enter=1, s=new search, q=quit): ", end="")
            pick_str = Prompt.ask("") or "1"

            if pick_str.lower() == "q":
                print("skive: quit — re-run later", file=sys.stderr)
                sys.exit(1)

            if pick_str.lower() == "s":
                query = None
                break

            try:
                pick_idx = int(pick_str)
                if 1 <= pick_idx <= len(results[:5]):
                    r = results[pick_idx - 1]
                    title = r.get("title") or r.get("name", "-")
                    date_str = r.get("release_date") or r.get("first_air_date", "-")
                    year = date_str[:4] if date_str and date_str != "-" else "-"
                    orig_lang = r.get("original_language", "-")
                    tmdb_id = str(r.get("id", "-"))
                    return TMDBPick(title=title, year=year, original_language=orig_lang, type=kind, id=tmdb_id)
                else:
                    print(f"skive: pick 1-{len(results[:5])}", file=sys.stderr)
            except ValueError:
                print(f"skive: invalid choice '{pick_str}'", file=sys.stderr)


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