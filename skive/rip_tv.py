"""TV rip orchestration: season prompt, duration matching, confirm table."""

import re
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from skive import common
from skive.makemkv import (
    CONFIG_DIR, build_selection, inject_profile, promote_staged, rename_staged,
    resolve_titles, rip_header, subtitle_note, rip_titles, hsize,
)
from skive.rip_common import rip_preamble
from skive.tmdb import fetch_season

console = Console()

# Constants ported from bash
SINGLE_EPS = 600  # seconds
DOUBLE_EPS = 300  # seconds


def staged_eps(stage_dir: Path, display: str, season: int) -> set[int]:
    """Return episode numbers already staged for this show and season.

    S01E05.mkv -> 5
    S01E05-E06.mkv -> 5 and 6

    Args:
        stage_dir: TV staging directory
        display: Display name (Show (Year))
        season: Season number

    Returns:
        Set of episode numbers already staged
    """
    staged = set()
    season_str = f"{season:02d}"
    pattern = re.compile(rf'^{re.escape(display)}_S{season_str}E(\d+)(-E(\d+))?$')
    for mkv in stage_dir.glob("*.mkv"):
        base = mkv.stem
        m = pattern.match(base)
        if m:
            lo = int(m.group(1))
            staged.add(lo)
            hi_str = m.group(3)
            if hi_str:
                hi = int(hi_str)
                staged.add(hi)
    return staged


def show_staging(stage_dir: Path, display: str) -> list[Path]:
    """Every staged file belonging to one show: its episodes (any season,
    singles and doubles) and its extras. The table's clear command deletes
    exactly these."""
    return [p for p in sorted(stage_dir.glob("*.mkv"))
            if p.name.startswith(display + "_S") or p.name.startswith(display + " - ")]


def build_candidates(episodes: list[tuple[int, str, int]], staged: set[int]) -> dict[int, tuple[int, str]]:
    """Build candidate episodes from TMDB, excluding staged.

    Args:
        episodes: List of (episode_number, name, runtime_seconds) from TMDB
        staged: Episodes already staged

    Returns:
        Dict mapping episode_number -> (runtime_seconds, name), for episodes with runtime>0 and not staged
    """
    candidates = {}
    for ep_num, name, rt in episodes:
        if rt > 0 and ep_num not in staged:
            candidates[ep_num] = (rt, name)
    return candidates


def tv_floor(minlength: int) -> int:
    """Lowest duration (seconds) a title needs to count as episode material.

    MakeMKV's minlength only keeps fragments out; menu loops run minutes.
    The floor is never below 600s.

    Args:
        minlength: Minimum title length in seconds the scan used

    Returns:
        Effective TV floor in seconds
    """
    return max(minlength, 600)


def sum_disqualified(titles: list[tuple[int, int, int]]) -> set[int]:
    """Titles that are play-alls: duration within ±120s of a subset sum.

    A play-all's duration matches the sum of a subset of 2..6 of the OTHER
    titles' durations. Only the summed title is disqualified; its components
    stay eligible, and every qualifying title is returned. Runs on floored
    titles so short menu loops can't produce false sums.

    Args:
        titles: List of (title_number, duration_seconds, size_bytes), already floored

    Returns:
        Set of title numbers that are play-alls
    """
    tol = 120
    max_parts = 6

    def has_summing_subset(components: list[int], target: int) -> bool:
        """Pruned DFS over ascending components: any 2..6-subset sum ≈ target?"""
        def dfs(start: int, count: int, total: int) -> bool:
            if count >= 2 and abs(total - target) <= tol:
                return True
            if count == max_parts:
                return False
            for i in range(start, len(components)):
                if total + components[i] > target + tol:
                    break  # ascending: every later component overshoots too
                if dfs(i + 1, count + 1, total + components[i]):
                    return True
            return False
        return dfs(0, 0, 0)

    disqualified: set[int] = set()
    for number, secs, _ in titles:
        others = sorted(d for n, d, _ in titles if n != number)
        if has_summing_subset(others, secs):
            disqualified.add(number)
    return disqualified


def match(titles: list[tuple[int, int, int]], candidates: dict[int, tuple[int, str]]) -> dict[int, tuple[int, int]]:
    """Greedy duration matching.

    Titles sorted longest-first. Each claims nearest-runtime unmatched episode.
    Single competes within SINGLE_EPS.
    Consecutive pair competes on sum of runtimes; pair wins only if its distance < best single distance.
    No claim -> extra (lo=hi=0).

    Args:
        titles: List of (title_number, duration_seconds, size_bytes)
        candidates: Dict episode_number -> (runtime_seconds, name)

    Returns:
        Dict title_number -> (lo_ep, hi_ep). lo==hi==0 means extra.
    """
    eps_by_num = list(candidates.keys())

    assignment = {}
    used_eps = set()  # Track used episodes separately

    # Sort titles by duration descending (longest first)
    sorted_titles = sorted(titles, key=lambda x: -x[1])

    for t, t_secs, _ in sorted_titles:
        best_single = None
        best_single_dist = None

        # Try each available single episode
        for e in eps_by_num:
            if e in used_eps:
                continue
            ep_secs = candidates[e][0]
            d = abs(t_secs - ep_secs)
            if d <= SINGLE_EPS:
                if best_single is None or d < best_single_dist:
                    best_single = e
                    best_single_dist = d

        best_pair = None
        best_pair_dist = None

        # Try consecutive pairs
        for e in eps_by_num:
            if e not in candidates:
                continue
            if (e + 1) not in candidates:
                continue
            if e in used_eps:
                continue
            if (e + 1) in used_eps:
                continue

            pair_rt = candidates[e][0] + candidates[e + 1][0]
            d = abs(t_secs - pair_rt)
            if d <= DOUBLE_EPS:
                if best_pair is None or d < best_pair_dist:
                    best_pair = (e, e + 1)
                    best_pair_dist = d

        # Choose best: pair if nearer than single, else single
        if best_pair and (best_single is None or best_pair_dist < best_single_dist):
            lo, hi = best_pair
            used_eps.add(lo)
            used_eps.add(hi)
            assignment[t] = (lo, hi)
        elif best_single is not None:
            e = best_single
            used_eps.add(e)
            assignment[t] = (e, e)
        else:
            assignment[t] = (0, 0)  # Extra

    return assignment


def number_in_order(titles: list[tuple[int, int, int]], first_ep: int) -> dict[int, tuple[int, int]]:
    """Number titles in title order: title i gets episode first_ep + i.

    TV discs lay titles out in episode order; runtimes can't tell episodes
    apart when a season's lengths are near-identical.

    Args:
        titles: List of (title_number, duration_seconds, size_bytes)
        first_ep: Episode number assigned to the first title

    Returns:
        Dict title_number -> (lo_ep, hi_ep)
    """
    return {t: (e, e) for e, (t, _, _) in enumerate(titles, start=first_ep)}


def parse_fix_rows(spec: str) -> list[tuple[int, str, int, int]]:
    """Parse a comma-separated fix row spec (t=ep, t=ep-ep, or t=e).

    Args:
        spec: Comma-separated fix row string

    Returns:
        List of (title_number, kind, lo, hi), where kind is "ep", "range",
        or "extra" and extras have lo=hi=0

    Raises:
        ValueError: Invalid syntax or values
    """
    entries = []
    if not spec.strip():
        return entries

    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue

        m = re.match(r'^(\d+)=e$', entry)
        if m:
            t = int(m.group(1))
            entries.append((t, "extra", 0, 0))
            continue

        m = re.match(r'^(\d+)=(\d+)-(\d+)$', entry)
        if m:
            t = int(m.group(1))
            lo = int(m.group(2))
            hi = int(m.group(3))
            if not (lo >= 1 and hi > lo):
                raise ValueError(f"bad double-episode range in '{entry}'")
            entries.append((t, "range", lo, hi))
            continue

        m = re.match(r'^(\d+)=(\d+)$', entry)
        if m:
            t = int(m.group(1))
            ep = int(m.group(2))
            if ep < 1:
                raise ValueError(f"bad episode number in '{entry}'")
            entries.append((t, "ep", ep, ep))
            continue

        raise ValueError(f"bad episode-map entry '{entry}' (want t=ep, t=ep-ep, or t=e)")

    return entries


def apply_fixes(assignment: dict[int, tuple[int, int]], titles: list[int], staged: set[int],
                fix_spec: str, max_ep: int, title_to_eps: dict[int, tuple[int, int]]) -> bool:
    """Apply fix rows, left-to-right, with clash checking.

    A rejected row (unknown title, past max_ep, already staged, claimed by another)
    changes NOTHING for that title, prints why, returns False.
    Returns True if all rows applied successfully.

    Args:
        assignment: Current title -> (lo, hi) mapping (mutable)
        titles: List of title numbers on disc
        staged: Episodes already staged
        fix_spec: Fix row string
        max_ep: Last episode number in season (for validation)
        title_to_eps: Reverse mapping episode -> title (for clash check)

    Returns:
        True if all fixes applied, False if any row rejected
    """
    rows = parse_fix_rows(fix_spec)
    ok = True
    for t, kind, lo, hi in rows:
        if t not in titles:
            print(f"skive: no title {t} on this disc", file=sys.stderr)
            ok = False
            continue  # Skip but continue other rows

        if kind == "extra":
            # Clear any existing claim, release old episodes
            if t in assignment:
                old_lo, old_hi = assignment[t]
                if old_lo != 0 and old_lo in title_to_eps:
                    if title_to_eps[old_lo] == t:
                        del title_to_eps[old_lo]
                        if old_hi != old_lo and old_hi in title_to_eps:
                            del title_to_eps[old_hi]
            assignment[t] = (0, 0)
            continue

        # Check episode numbers
        if lo < 1:
            print(f"skive: episode {lo} must be >= 1", file=sys.stderr)
            ok = False
            continue
        if max_ep > 0 and hi > max_ep:
            print(f"skive: episode {hi} is past the season's last episode ({max_ep})", file=sys.stderr)
            ok = False
            continue

        # Check already staged
        bad = False
        for e in range(lo, hi + 1):
            if e in staged:
                print(f"skive: episode {e} is already staged", file=sys.stderr)
                bad = True

        # Check already claimed by another title
        if not bad:
            for e in range(lo, hi + 1):
                if e in title_to_eps and title_to_eps[e] != t:
                    other_t = title_to_eps[e]
                    print(f"skive: episode {e} is already claimed by title {other_t}", file=sys.stderr)
                    bad = True

        if bad:
            ok = False
            continue  # Reject this row

        # Release old claim for this title
        if t in assignment:
            old_lo, old_hi = assignment[t]
            if old_lo != 0:
                if old_lo in title_to_eps and title_to_eps[old_lo] == t:
                    del title_to_eps[old_lo]
                    if old_hi != old_lo and old_hi in title_to_eps and title_to_eps[old_hi] == t:
                        del title_to_eps[old_hi]

        # Apply new claim
        assignment[t] = (lo, hi)
        for e in range(lo, hi + 1):
            title_to_eps[e] = t

    return ok


def map_str(assignment: dict[int, tuple[int, int]]) -> str:
    """Serialize assignment to episode map string (0=1,1=2-3,2=e).

    Args:
        assignment: title -> (lo, hi) mapping

    Returns:
        Serialized map string
    """
    parts = []
    for t in sorted(assignment.keys()):
        lo, hi = assignment[t]
        if lo == 0:
            parts.append(f"{t}=e")
        elif lo == hi:
            parts.append(f"{t}={lo}")
        else:
            parts.append(f"{t}={lo}-{hi}")
    return ",".join(parts)


def print_table(display: str, season: int, titles: list[tuple[int, int, int]],
                assignment: dict[int, tuple[int, int]], ep_names: dict[int, str]) -> None:
    """Print the confirmation table in title-number order.

    Args:
        display: Show name
        season: Season number
        titles: List of (title, secs, bytes)
        assignment: title -> (lo, hi) mapping
        ep_names: episode_number -> name mapping
    """
    season_str = f"{season:02d}"
    console.print(f"\n{display} — season {season_str}")

    table = Table()
    table.add_column("title", style="cyan", width=6)
    table.add_column("duration", style="white", width=9)
    table.add_column("size", style="dim", width=9)
    table.add_column("assignment", style="yellow")

    for t, secs, size in sorted(titles, key=lambda x: x[0]):
        # Format duration H:MM:SS
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        dur = f"{h}:{m:02d}:{s:02d}"

        sz = hsize(size)

        lo, hi = assignment.get(t, (0, 0))
        if lo == 0:
            assign_str = "→ extra"
        else:
            code = f"S{season_str}E{lo:02d}"
            if hi != lo:
                code += f"-E{hi:02d}"
            name = ep_names.get(lo, "")
            if name:
                assign_str = f"→ {code} ({name})"
            else:
                assign_str = f"→ {code}"

        table.add_row(str(t), dur, sz, assign_str)

    console.print(table)


def rename_tv(display: str, stage_dir: Path, season: int, assignment: dict[int, tuple[int, int]]) -> None:
    """Rename staged files per the episode map.

    Junk-labeled titles become <display>_SNNENN[-ENNN].mkv or <display> - Extra NN.mkv.
    Never clobbers existing targets.

    Args:
        display: Show name
        stage_dir: TV staging directory
        season: Season number
        assignment: title -> (lo, hi) mapping
    """
    season_str = f"{season:02d}"

    for mkv in stage_dir.glob("*.mkv"):
        base = mkv.stem
        # Extract _tNN
        m = re.search(r'_t(\d+)$', base)
        if not m:
            continue
        title_num = int(m.group(1))

        lohi = assignment.get(title_num)
        if lohi is None:
            # No mapping for this title - skip with warning
            print(f"skive: no episode for title {title_num} ({base}) — left alone", file=sys.stderr)
            continue

        lo, hi = lohi
        if lo == 0:
            # Extra - roll "Extra NN" past existing
            m_val = 1
            while (stage_dir / f"{display} - Extra {m_val:02d}.mkv").exists():
                m_val += 1
            target = stage_dir / f"{display} - Extra {m_val:02d}.mkv"
        elif lo == hi:
            # Single episode
            target_name = f"{display}_S{season_str}E{lo:02d}.mkv"
            target = stage_dir / target_name
        else:
            # Double episode
            target_name = f"{display}_S{season_str}E{lo:02d}-E{hi:02d}.mkv"
            target = stage_dir / target_name

        # Check for clobber
        if target.exists():
            print(f"skive: {target.name} already exists — {base}.mkv left alone", file=sys.stderr)
            continue

        mkv.rename(target)
        cur = base[:m.start()]
        print(f"skive: {cur} → {target.stem}", file=sys.stderr)


def rip_tv(args) -> None:
    """Main TV rip orchestration.

    Args:
        args: argparse.Namespace from cmd_rip_tv

    Raises:
        SystemExit: On any error or user abort
    """
    preamble = rip_preamble("tv", "skive rip tv", args)
    log = preamble.log
    pick = preamble.pick
    display = preamble.display
    titles_full = preamble.titles_full
    stage_tv = preamble.stage_tv
    minlength = preamble.minlength

    # --extras: no season, no scan, no table
    if args.extras:
        log.note("type", "type=tv extras")
        titles = resolve_titles(args.titles, titles_full, preamble.media)
        preexisting = set(stage_tv.glob("*.mkv"))
        log.phase("rip")
        infos = titles_full if titles is None else [t for t in titles_full if t.number in titles]
        worst_rc = rip_titles(titles, CONFIG_DIR / "custom.mmcp.xml", minlength, stage_tv, disc=preamble.disc,
                              total=len(titles_full), infos=infos, header=rip_header(display, infos))
        if worst_rc != 0:
            log.note("rip", f"makemkvcon rc={worst_rc} — partial rip, renaming what completed")

        # Rename as extras
        rename_staged(display, stage_tv, suffix="e", preexisting=preexisting)
        if preamble.disc > 0:
            promote_staged(stage_tv)

        # Subtitle note
        note = subtitle_note(stage_tv)
        print(f"{note}", file=sys.stderr)
        log.note("subtitle", note)

        log.phase_end()
        log.finish(worst_rc)
        if worst_rc == 0:
            common.eject_disc(preamble.disc, log)
        sys.exit(worst_rc)

    # Season prompt
    console.print("season [1] (0 = specials): ", end="")
    season_str = Prompt.ask("") or "1"
    season_str = season_str.strip()

    if not season_str:
        season = 1
    else:
        try:
            season = int(season_str)
        except ValueError:
            print("skive: season must be a number 0-99", file=sys.stderr)
            sys.exit(1)

    if not (0 <= season <= 99):
        print("skive: season must be a number 0-99", file=sys.stderr)
        sys.exit(1)

    log.note("type", f"type=tv season={season}")

    season_dir = stage_tv  # TV always uses the same dir
    season_dir.mkdir(parents=True, exist_ok=True)

    # The gate already scanned the disc; reuse that result (no second scan)
    if not titles_full:
        print("skive: disc scan failed — no titles found", file=sys.stderr)
        sys.exit(1)

    # Bare -T opens the title menu; no -T rips all titles (DVD shows prefer this)
    user_titles = resolve_titles(args.titles, titles_full, preamble.media)
    if user_titles is not None:
        titles_full = [t for t in titles_full if t.number in user_titles]
        if not titles_full:
            print(f"skive: none of the requested titles survived the scan (minlength={minlength})", file=sys.stderr)
            sys.exit(1)

    titles_info = [(t.number, t.seconds, t.size) for t in titles_full]

    # TV floor: menu loops and fragments never make episodes. Dropped
    # entirely — not eligible, not in the table, not ripped.
    titles_info = [t for t in titles_info if t[1] >= tv_floor(minlength)]
    playalls = sum_disqualified(titles_info)

    def pin_playalls(a: dict[int, tuple[int, int]]) -> dict[int, tuple[int, int]]:
        """Play-alls rip as extras whatever the matcher liked.

        Re-applied after each automatic rebuild (initial match, `n`
        renumber, `c` clear). Never applied after a fix row: `t=ep` typed at
        the table is the deliberate escape hatch.
        """
        for t in playalls:
            a[t] = (0, 0)
        return a

    # Fetch season from TMDB
    if not pick.id or pick.id == "-":
        print("skive: the TMDB pick carries no id — cannot fetch episodes. re-pick or use --tmdbid tv:N.", file=sys.stderr)
        sys.exit(1)

    try:
        episodes = fetch_season(pick.id, season)
    except RuntimeError as e:
        print(f"skive: TMDB season fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Build episodes dict
    ep_names = {ep: name for ep, name, _ in episodes}

    # Get staged episodes
    staged = staged_eps(season_dir, display, season)
    if preamble.disc > 0:
        # Already-promoted episodes live in the parent staging dir
        staged |= staged_eps(season_dir.parent, display, season)
    if staged:
        eps_str = ", ".join(f"E{e:02d}" for e in sorted(staged))
        console.print(f"already staged: {eps_str} — c at the prompt clears the show's staging")

    # Build candidates (episodes with runtime, excluding staged)
    candidates = build_candidates(episodes, staged)

    # Check for zero runtimes
    if not candidates:
        log.note("match", "no TMDB runtimes for this season — numbering titles in order")
        # Sequential fallback
        console.print("first episode number on this disc [1]: ", end="")
        firstep_str = Prompt.ask("") or "1"
        try:
            firstep = int(firstep_str)
        except ValueError:
            print("skive: episode must be a number >= 1", file=sys.stderr)
            sys.exit(1)
        if firstep < 1:
            print("skive: episode must be a number >= 1", file=sys.stderr)
            sys.exit(1)

        # Number titles in order
        assignment = pin_playalls(number_in_order(titles_info, firstep))
    else:
        # Duration matching
        assignment = pin_playalls(match(titles_info, candidates))

    # Confirm loop
    while True:
        print_table(display, season, titles_info, assignment, ep_names)
        console.print("ok? (Enter=rip, fixes like 3=7 / 1=1-2 / 2=e, n[=first]=number in order, c=clear staged, q=quit): ", end="")
        ans = Prompt.ask("") or ""

        if not ans:
            break
        elif ans.lower() == "q":
            print("skive: quit — nothing ripped.", file=sys.stderr)
            sys.exit(1)
        elif ans.lower().startswith("n"):
            # Renumber titles in order: duration matching is guesswork when a
            # season's runtimes are near-identical (titles grab episodes that
            # live on later discs of a multi-disc set).
            m = re.match(r'^n=(\d+)$', ans.strip(), re.IGNORECASE)
            if m:
                first_ep = int(m.group(1))
            else:
                # Default: first episode of the season not already staged —
                # right for the next disc of a set.
                default_ep = min((e for e in ep_names if e not in staged), default=1)
                console.print(f"first episode number on this disc [{default_ep}]: ", end="")
                first_str = (Prompt.ask("") or str(default_ep)).strip()
                try:
                    first_ep = int(first_str)
                except ValueError:
                    first_ep = 0
            if first_ep < 1:
                print("skive: episode must be a number >= 1", file=sys.stderr)
                continue
            assignment = pin_playalls(number_in_order(titles_info, first_ep))
        elif ans.lower() == "c":
            doomed = show_staging(season_dir, display)
            if preamble.disc > 0:
                doomed += show_staging(season_dir.parent, display)
            if not doomed:
                console.print("nothing staged for this show")
                continue
            for f in doomed:
                console.print(f"  {f.name}")
            sure = Prompt.ask(f"delete {len(doomed)} staged file(s) for {display}? (y/N): ") or ""
            if sure.lower() != "y":
                continue
            for f in doomed:
                f.unlink()
            staged = staged_eps(season_dir, display, season)
            if preamble.disc > 0:
                staged |= staged_eps(season_dir.parent, display, season)
            candidates = build_candidates(episodes, staged)
            if candidates:
                assignment = pin_playalls(match(titles_info, candidates))
            # else: no runtimes — the sequential assignment stands
        else:
            # Apply fixes - rebuild title->ep mapping for clash check
            title_to_eps = {}
            for t, (lo, hi) in assignment.items():
                if lo != 0:
                    for e in range(lo, hi + 1):
                        title_to_eps[e] = t
            max_ep = max(ep_names.keys()) if ep_names else 0
            apply_fixes(assignment, [t for t, _, _ in titles_info], staged, ans, max_ep, title_to_eps)

    # Selection string + profile injection
    keep = preamble.keep
    sel = build_selection(keep)
    log.note("audio", f"kept={keep if keep else 'all'} origlang={pick.original_language if pick.original_language != '-' else '?'}")

    profile_path = inject_profile(sel)
    try:
        # Rip phase
        log.phase("rip")
        # Explicit -T selections may include titles filtered from the confirmed scan.
        rip_selection = None if user_titles is None else [t for t, _, _ in titles_info]
        worst_rc = rip_titles(rip_selection, profile_path, minlength, season_dir, disc=preamble.disc,
                              total=len(titles_info), infos=titles_full, header=rip_header(display, titles_full))

        if worst_rc != 0:
            log.note("rip", f"makemkvcon rc={worst_rc} — partial rip, renaming what completed")

        # Rename per the map
        rename_tv(display, season_dir, season, assignment)
        if preamble.disc > 0:
            promote_staged(season_dir)

        # Subtitle note
        note = subtitle_note(season_dir)
        print(f"{note}", file=sys.stderr)
        log.note("subtitle", note)

        log.phase_end()
        log.finish(worst_rc)
        if worst_rc == 0:
            common.eject_disc(preamble.disc, log)
        sys.exit(worst_rc)
    finally:
        profile_path.unlink(missing_ok=True)