"""TV rip orchestration: season prompt, episode-range assignment, confirm table."""

import re
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from skive import common
from skive.makemkv import (
    CONFIG_DIR, build_selection, inject_profile, promote_staged, rename_staged,
    resolve_titles, rip_header, subtitle_note, rip_titles, hsize,
)
from skive.rip_common import rip_preamble
from skive.tmdb import fetch_season

console = Console()


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


def parse_range(s: str) -> tuple[int, int] | None:
    """Parse a stated episode range: "N" -> (N, N), "N-M" -> (N, M).

    None for anything unparseable or invalid (N < 1, M < N). Whitespace
    around the numbers is tolerated.

    Args:
        s: Raw answer from the range prompt

    Returns:
        (first, last) episode numbers, or None
    """
    s = s.strip()
    m = re.match(r'^(\d+)$', s)
    if m:
        n = int(m.group(1))
        return (n, n) if n >= 1 else None
    m = re.match(r'^(\d+)\s*-\s*(\d+)$', s)
    if m:
        n, k = int(m.group(1)), int(m.group(2))
        return (n, k) if n >= 1 and k >= n else None
    return None


def next_unstaged(ep_names: dict[int, str], staged: set[int]) -> int:
    """Where this disc's numbering starts: the smallest named episode not
    already staged.

    Every named episode staged (or nothing named at all): continue past the
    highest staged one. Nothing staged: the season starts at 1.

    Args:
        ep_names: episode_number -> name from TMDB
        staged: Episode numbers already staged

    Returns:
        First episode number to offer
    """
    for e in sorted(ep_names):
        if e not in staged:
            return e
    return max(staged, default=0) + 1


def assign_range(titles: list[tuple[int, int, int]], playalls: set[int], lo: int, hi: int) -> dict[int, tuple[int, int]]:
    """Assign eligible titles, in disc order, to the stated episode range.

    Eligible titles are `titles` minus the play-alls; the i-th one gets
    episode lo + i. More eligible titles than the range covers: the tail is
    extra. Fewer: the range is only partly covered. Play-alls are always
    extra. Episode identity is what the user stated plus disc position;
    durations never choose.

    Args:
        titles: List of (title_number, duration_seconds, size_bytes), disc order
        playalls: Title numbers disqualified as play-alls
        lo: First episode number of the stated range
        hi: Last episode number of the stated range

    Returns:
        Dict title_number -> (lo_ep, hi_ep) covering every title
    """
    assignment: dict[int, tuple[int, int]] = {}
    pos = 0
    for t, _, _ in titles:
        if t in playalls:
            assignment[t] = (0, 0)
        elif pos < hi - lo + 1:
            assignment[t] = (lo + pos, lo + pos)
            pos += 1
        else:
            assignment[t] = (0, 0)
    return assignment


def range_mismatch_note(titles: list[tuple[int, int, int]], playalls: set[int], lo: int, hi: int) -> str | None:
    """One-line warning when the eligible titles don't fill the stated range.

    Names the fix-row escape and the `n` re-ask; the table stays the single
    place where the user corrects the map.

    Args:
        titles: List of (title_number, duration_seconds, size_bytes), disc order
        playalls: Title numbers disqualified as play-alls
        lo: First episode number of the stated range
        hi: Last episode number of the stated range

    Returns:
        The warning line, or None when the counts line up or there are no
        eligible titles
    """
    eligible = [t for t, _, _ in titles if t not in playalls]
    if not eligible:
        return None
    size = hi - lo + 1
    if len(eligible) == size:
        return None
    if len(eligible) > size:
        extra = eligible[size]
        return (f"eligible titles: {len(eligible)} but {lo}-{hi} covers {size} — "
                f"title {extra} left extra; fix with {extra}=e or n to re-ask")
    return (f"eligible titles: {len(eligible)} but {lo}-{hi} covers {size} — "
            f"episodes {lo + len(eligible)}-{hi} unassigned; fix with a double like "
            f"{eligible[-1]}={lo + len(eligible)}-{hi} or n to re-ask")


def range_past_end_note(hi: int, max_ep: int) -> str | None:
    """Warning when the stated range runs past the TMDB season's last episode.

    The stated range is truth (ADR 0001) and TMDB seasons can be incomplete,
    so this never rejects or clamps — it only warns: a silent S01E99 on a
    3-episode season is a typo trap.

    Args:
        hi: Last episode number of the stated range
        max_ep: Last episode number the TMDB season lists (0 = unknown)

    Returns:
        The warning line, or None when the range fits or the size is unknown
    """
    if max_ep > 0 and hi > max_ep:
        return (f"stated range ends at E{hi} but the season lists {max_ep} "
                f"episodes — check the table")
    return None


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


RATIO_GREEN = (0.92, 1.04)
"""Inclusive disc/TMDB duration ratio band that reads green (PAL discs run
~4-5% short, so the low edge sits below 0.95)."""


def audit_ratio(disc_secs: int, tmdb_secs: int) -> float | None:
    """Disc ÷ TMDB duration ratio at 2dp; None when either side is unknown.

    Durations grade assignments, they never change them (ADR 0001).

    Args:
        disc_secs: Disc title duration in seconds
        tmdb_secs: TMDB runtime in seconds

    Returns:
        disc / tmdb rounded to 2 decimals, or None when either is <= 0
    """
    if tmdb_secs <= 0 or disc_secs <= 0:
        return None
    return round(disc_secs / tmdb_secs, 2)


def audit_cell(secs: int, lo: int, hi: int, ep_runtimes: dict[int, int]) -> tuple[str, str] | None:
    """Full audit cell for one title: (ratio text, rich style); None = blank.

    Assigned titles (lo >= 1) audit against the summed TMDB runtimes lo..hi
    (a double against both); any episode in the range without a runtime (>0)
    blanks the cell — never a false yellow. Extras (lo == 0) are blank. The
    band grades the exact disc/tmdb quotient; the text shows the 2dp value.

    Args:
        secs: Disc title duration in seconds
        lo: First assigned episode number (0 = extra)
        hi: Last assigned episode number
        ep_runtimes: episode_number -> TMDB runtime seconds

    Returns:
        (f"{ratio:.2f}", "green" or "yellow"), or None when blank
    """
    if lo < 1:
        return None
    runtimes = [ep_runtimes.get(e, 0) for e in range(lo, hi + 1)]
    if any(rt <= 0 for rt in runtimes):
        return None
    total = sum(runtimes)
    if secs <= 0 or total <= 0:
        return None
    exact = secs / total
    inside = RATIO_GREEN[0] <= exact <= RATIO_GREEN[1]
    return (f"{exact:.2f}", "green" if inside else "yellow")


def print_table(display: str, season: int, titles: list[tuple[int, int, int]],
                assignment: dict[int, tuple[int, int]], ep_names: dict[int, str],
                ep_runtimes: dict[int, int]) -> None:
    """Print the confirmation table in title-number order.

    Args:
        display: Show name
        season: Season number
        titles: List of (title, secs, bytes)
        assignment: title -> (lo, hi) mapping
        ep_names: episode_number -> name mapping
        ep_runtimes: episode_number -> TMDB runtime seconds
    """
    season_str = f"{season:02d}"
    console.print(f"\n{display} — season {season_str}")

    table = Table()
    table.add_column("title", style="cyan", width=6)
    table.add_column("duration", style="white", width=9)
    table.add_column("size", style="dim", width=9)
    table.add_column("audit", width=6)
    table.add_column("assignment", style="yellow")

    for t, secs, size in sorted(titles, key=lambda x: x[0]):
        # Format duration H:MM:SS
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        dur = f"{h}:{m:02d}:{s:02d}"

        sz = hsize(size)

        lo, hi = assignment.get(t, (0, 0))
        audit = audit_cell(secs, lo, hi, ep_runtimes)
        audit_str = Text(audit[0], style=audit[1]) if audit else ""

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

        table.add_row(str(t), dur, sz, audit_str, assign_str)

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
        """Play-alls rip as extras whatever the stated range said.

        Re-applied after each automatic rebuild (initial assignment, `n`
        re-ask). Never applied after a fix row: `t=ep` typed at the table is
        the deliberate escape hatch.
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
    ep_runtimes = {ep: rt for ep, _, rt in episodes}
    max_ep = max(ep_names.keys()) if ep_names else 0

    # Get staged episodes
    staged = staged_eps(season_dir, display, season)
    if preamble.disc > 0:
        # Already-promoted episodes live in the parent staging dir
        staged |= staged_eps(season_dir.parent, display, season)
    if staged:
        eps_str = ", ".join(f"E{e:02d}" for e in sorted(staged))
        console.print(f"already staged: {eps_str} — c at the prompt clears the show's staging")

    def ask_range() -> tuple[int, int]:
        """One pre-filled prompt states which episodes this disc carries.

        The pre-fill starts at the first episode not yet staged and spans one
        episode per eligible title; Enter takes it. Unparseable answers
        re-ask.
        """
        eligible = len(titles_info) - len(playalls)
        if eligible == 0:
            print("skive: no eligible titles on this disc", file=sys.stderr)
            sys.exit(1)
        first = next_unstaged(ep_names, staged)
        prefill = str(first) if eligible == 1 else f"{first}-{first + eligible - 1}"
        while True:
            console.print(f"episodes on this disc [{prefill}]: ", end="")
            rng = parse_range((Prompt.ask("") or prefill).strip())
            if rng is not None:
                return rng

    def build_note(lo: int, hi: int) -> str | None:
        """Mismatch and past-the-season-end warnings, joined to one print."""
        lines = [ln for ln in (range_mismatch_note(titles_info, playalls, lo, hi),
                               range_past_end_note(hi, max_ep)) if ln]
        return "\n".join(lines) if lines else None

    lo, hi = ask_range()
    assignment = pin_playalls(assign_range(titles_info, playalls, lo, hi))
    note = build_note(lo, hi)

    # Confirm loop
    while True:
        if note:
            console.print(note)
        print_table(display, season, titles_info, assignment, ep_names, ep_runtimes)
        console.print("ok? (Enter=rip, fixes like 3=7 / 1=1-2 / 2=e, n=episode range, c=clear staged, q=quit): ", end="")
        ans = Prompt.ask("") or ""

        if not ans:
            break
        elif ans.lower() == "q":
            print("skive: quit — nothing ripped.", file=sys.stderr)
            sys.exit(1)
        elif ans.lower().startswith("n"):
            # Re-state the range (`n=5` spells the same as bare `n`; any
            # =-suffix is ignored): disc order stands, the stated range rules.
            lo, hi = ask_range()
            assignment = pin_playalls(assign_range(titles_info, playalls, lo, hi))
            note = build_note(lo, hi)
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
            # No re-assignment: the map comes from the stated range, not from
            # staging. The refreshed set only steers the next pre-fill.
            staged = staged_eps(season_dir, display, season)
            if preamble.disc > 0:
                staged |= staged_eps(season_dir.parent, display, season)
        else:
            # Apply fixes - rebuild title->ep mapping for clash check
            title_to_eps = {}
            for t, (elo, ehi) in assignment.items():
                if elo != 0:
                    for e in range(elo, ehi + 1):
                        title_to_eps[e] = t
            apply_fixes(assignment, [t for t, _, _ in titles_info], staged, ans, max_ep, title_to_eps)
            # A fix row is the user speaking; the automated note does not repeat.
            note = None

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