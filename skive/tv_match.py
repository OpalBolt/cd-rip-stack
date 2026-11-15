"""TV assignment math: the TV floor, play-all disqualification, range parsing,
position assignment, fix-row parsing and application, the episode map string,
the play-all-only trigger, and the ratio audit."""

import re

from skive import ui


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


SUM_TIGHT_TOL_S = 120
"""Tight play-all window: duration within ±120s of a subset sum."""

SUM_SOFT_TOL_S = 300
"""Soft play-all window: a deliberately looser ±300s sum window for the
same subset scan (a flat widening of the tight ±120s, chosen over a relative
window to keep the tolerance nameable and testable). Soft matches disqualify
and skip."""


def _summing_subset(components: list[int], target: int, tol: int) -> list[int] | None:
    """Indices of a 2..6-subset of ascending `components` summing to within
    `tol` of `target`, or None. Pruned DFS: ascending order lets a total past
    target + tol end the branch."""
    max_parts = 6

    def dfs(start: int, count: int, total: int, picked: list[int]) -> list[int] | None:
        if count >= 2 and abs(total - target) <= tol:
            return list(picked)
        if count == max_parts:
            return None
        for i in range(start, len(components)):
            if total + components[i] > target + tol:
                break  # ascending: every later component overshoots too
            picked.append(i)
            hit = dfs(i + 1, count + 1, total + components[i], picked)
            if hit is not None:
                return hit
            picked.pop()
        return None

    return dfs(0, 0, 0, [])


def sum_disqualified_windowed(titles: list[tuple[int, int, int]]) -> dict[int, tuple[str, list[int]]]:
    """Play-alls by window tier: title -> ("tight"|"soft", component titles).

    A play-all's duration matches the sum of a subset of 2..6 of the OTHER
    titles' durations, in the tight ±SUM_TIGHT_TOL_S window first, then the
    soft ±SUM_SOFT_TOL_S window. The component title numbers are the matched
    subset — the evidence for the `≈ sum of …` note a table can render. Only
    the summed title is disqualified; its components stay eligible. Runs on
    floored titles so short menu loops can't produce false sums.

    Args:
        titles: List of (title_number, duration_seconds, size_bytes), already floored

    Returns:
        Dict title_number -> (tier, component title numbers, ascending)
    """
    result: dict[int, tuple[str, list[int]]] = {}
    for number, secs, _ in titles:
        others = sorted((d, n) for n, d, _ in titles if n != number)
        for tier, tol in (("tight", SUM_TIGHT_TOL_S), ("soft", SUM_SOFT_TOL_S)):
            hit = _summing_subset([d for d, _ in others], secs, tol)
            if hit is not None:
                result[number] = (tier, [others[i][1] for i in hit])
                break
    return result


def sum_disqualified(titles: list[tuple[int, int, int]]) -> set[int]:
    """Titles that are play-alls: duration within ±120s of a subset sum.

    Both windows count (see sum_disqualified_windowed); only the title numbers
    come back.

    Args:
        titles: List of (title_number, duration_seconds, size_bytes), already floored

    Returns:
        Set of title numbers that are play-alls
    """
    return set(sum_disqualified_windowed(titles))


def playall_skip(playalls: set[int], assignment: dict[int, tuple[int, int]]) -> set[int]:
    """Play-alls whose final assignment is extra — the titles the rip skips.

    Derived after the confirm loop from the final assignment, so a fix row
    assigning a play-all an episode (`0=1-2`) keeps it in the rip (ADR 0008).

    Args:
        playalls: Title numbers disqualified as play-alls
        assignment: Final title -> (lo, hi) mapping (post fix rows)

    Returns:
        Set of title numbers to leave out of the rip selection
    """
    return {t for t in playalls if assignment.get(t, (0, 0)) == (0, 0)}


def playall_only(titles: list[tuple[int, int, int]], playalls: set[int],
                 assignment: dict[int, tuple[int, int]]) -> tuple[int, int, int] | None:
    """The play-all-only trigger: the lone eligible title carrying a range.

    Fires — returning (title, lo, hi) — when exactly one eligible title
    (floored titles minus play-alls) remains and its final assignment is a
    multi-episode range (lo >= 1, hi > lo): the disc's episodes live as
    chapter ranges inside one title (ADR 0009). None for every disarming
    shape — two or more eligible titles, a lone title assigned a single
    episode, the lone title extra.

    Args:
        titles: List of (title_number, duration_seconds, size_bytes), disc order
        playalls: Title numbers disqualified as play-alls
        assignment: Final title -> (lo, hi) mapping (post fix rows)

    Returns:
        (title_number, lo, hi) of the lone ranged title, or None
    """
    eligible = [t for t, _, _ in titles if t not in playalls]
    if len(eligible) != 1:
        return None
    lo, hi = assignment.get(eligible[0], (0, 0))
    if lo >= 1 and hi > lo:
        return (eligible[0], lo, hi)
    return None


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


def guess_range(titles: list[tuple[int, int, int]], playalls: set[int],
                ep_runtimes: dict[int, int], staged: set[int],
                ep_names: dict[int, str]) -> tuple[int, int] | None:
    """The episode guess: a greedy walk that suggests the disc's episode range.

    From the first unstaged episode (discs rip in order, so staged episodes
    are a contiguous prefix), each episode claims the first not-yet-consumed
    eligible title in disc order whose disc÷TMDB ratio is green (the same
    inclusive band the audit uses); the walk stops at the first episode with
    no fitting title or no TMDB runtime. Singles only — doubles stay fix-row
    territory. The pairing is internal: only the range leaves the function;
    durations suggest, they never assign (ADR 0001, ADR 0008).

    Args:
        titles: List of (title_number, duration_seconds, size_bytes), disc order
        playalls: Title numbers disqualified as play-alls
        ep_runtimes: episode_number -> TMDB runtime seconds
        staged: Episode numbers already staged
        ep_names: episode_number -> name from TMDB

    Returns:
        (lo, hi) over the walked episodes, or None when zero episodes matched
    """
    pairing = guess_pairing(titles, playalls, ep_runtimes, staged, ep_names)
    if not pairing:
        return None
    eps = sorted(pairing)
    return (eps[0], eps[-1])


def guess_pairing(titles: list[tuple[int, int, int]], playalls: set[int],
                  ep_runtimes: dict[int, int], staged: set[int],
                  ep_names: dict[int, str]) -> dict[int, int]:
    """The episode guess walk, kept per-title: episode -> title.

    The same greedy walk guess_range reports as a range, but with the
    pairing intact so the prefill can place each episode on its own row.
    Empty dict when zero episodes matched.

    Args:
        titles: List of (title_number, duration_seconds, size_bytes), disc order
        playalls: Title numbers disqualified as play-alls
        ep_runtimes: episode_number -> TMDB runtime seconds
        staged: Episode numbers already staged
        ep_names: episode_number -> name from TMDB

    Returns:
        Dict episode_number -> title_number over the walked episodes
    """
    secs = {t: d for t, d, _ in titles}
    remaining = [t for t, _, _ in titles if t not in playalls]
    ep = next_unstaged(ep_names, staged)
    pairing: dict[int, int] = {}
    while True:
        rt = ep_runtimes.get(ep, 0)
        if rt <= 0:
            break
        fit = next((i for i, t in enumerate(remaining)
                    if secs[t] > 0 and RATIO_GREEN[0] <= secs[t] / rt <= RATIO_GREEN[1]),
                   None)
        if fit is None:
            break
        pairing[ep] = remaining.pop(fit)
        ep += 1
    return pairing


def prefill_assignment(titles: list[tuple[int, int, int]], playalls: set[int],
                       ep_runtimes: dict[int, int], staged: set[int],
                       ep_names: dict[int, str]) -> dict[int, tuple[int, int]]:
    """The full per-title prefill: title -> (lo, hi), extras as (0, 0).

    Green-ratio fit wins: the guess walk pairs each walked episode with its
    own title (singles only; doubles stay fix-row territory). When there are
    no runtimes or no green matches the documented fallback fills one episode
    per eligible title in disc order, starting at the first unstaged episode.
    Play-alls are always extra.

    Args:
        titles: List of (title_number, duration_seconds, size_bytes), disc order
        playalls: Title numbers disqualified as play-alls
        ep_runtimes: episode_number -> TMDB runtime seconds
        staged: Episode numbers already staged
        ep_names: episode_number -> name from TMDB

    Returns:
        Dict title_number -> (lo_ep, hi_ep) covering every title
    """
    pairing = guess_pairing(titles, playalls, ep_runtimes, staged, ep_names)
    if pairing:
        assignment = {t: (0, 0) for t, _, _ in titles}
        for ep, t in pairing.items():
            assignment[t] = (ep, ep)
        return assignment
    eligible = len(titles) - len(playalls)
    first = next_unstaged(ep_names, staged)
    return assign_range(titles, playalls, first, first + eligible - 1)


def episode_clashes(assignment: dict[int, tuple[int, int]]) -> list[tuple[int, list[int]]]:
    """Episodes claimed by two or more titles, ascending.

    Args:
        assignment: title -> (lo, hi) mapping

    Returns:
        List of (episode_number, claiming title numbers in dict order); empty
        when every episode has at most one claimant
    """
    claimed: dict[int, list[int]] = {}
    for t, (lo, hi) in assignment.items():
        for e in range(lo, hi + 1):
            claimed.setdefault(e, []).append(t)
    return [(e, ts) for e, ts in sorted(claimed.items()) if len(ts) > 1]


def staged_claims(assignment: dict[int, tuple[int, int]],
                  staged: set[int]) -> list[tuple[int, int]]:
    """Assignments that claim an already-staged episode, ascending by episode.

    Args:
        assignment: title -> (lo, hi) mapping
        staged: Episode numbers already staged

    Returns:
        List of (episode_number, claiming title number); empty when nothing
        assigned touches the staged set
    """
    out = []
    for t, (lo, hi) in assignment.items():
        for e in range(lo, hi + 1):
            if e in staged:
                out.append((e, t))
    return sorted(out)


def unfilled_episodes(lo: int, hi: int,
                      assignment: dict[int, tuple[int, int]]) -> list[int]:
    """Holes inside the covered range: episodes lo..hi no title claims.

    Args:
        lo: First episode number of the covered range
        hi: Last episode number of the covered range
        assignment: title -> (lo, hi) mapping

    Returns:
        Ascending episode numbers in the range left unassigned; empty when
        the range is fully covered
    """
    filled: set[int] = set()
    for a, b in assignment.values():
        if a >= 1:
            filled.update(range(a, b + 1))
    return [e for e in range(lo, hi + 1) if e not in filled]


def sub_floor_titles(titles: list[tuple[int, int, int]],
                     minlength: int) -> list[int]:
    """The sub-floor titles — duration below tv_floor(minlength) — in disc
    order, numbers intact. The listing set the table and rip flow use for
    titles too short to count as episode material.

    Args:
        titles: List of (title_number, duration_seconds, size_bytes), disc order
        minlength: Minimum title length in seconds the scan used

    Returns:
        Title numbers below the floor, disc order
    """
    floor = tv_floor(minlength)
    return [t for t, d, _ in titles if d < floor]


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
            ui.warn(f"no title {t} on this disc")
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
            ui.warn(f"episode {lo} must be >= 1")
            ok = False
            continue
        if max_ep > 0 and hi > max_ep:
            ui.warn(f"episode {hi} is past the season's last episode ({max_ep})")
            ok = False
            continue

        # Check already staged
        bad = False
        for e in range(lo, hi + 1):
            if e in staged:
                ui.warn(f"episode {e} is already staged")
                bad = True

        # Check already claimed by another title
        if not bad:
            for e in range(lo, hi + 1):
                if e in title_to_eps and title_to_eps[e] != t:
                    other_t = title_to_eps[e]
                    ui.warn(f"episode {e} is already claimed by title {other_t}")
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
