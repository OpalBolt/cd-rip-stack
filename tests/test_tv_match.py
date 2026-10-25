"""Tests for TV assignment math: floor, play-alls, range, fix rows, map string, audit."""

import pytest

from skive.tv_match import (
    parse_range, next_unstaged, assign_range, range_mismatch_note,
    range_past_end_note, parse_fix_rows, apply_fixes, map_str,
    tv_floor, sum_disqualified, playall_skip, playall_only, audit_ratio,
    audit_cell, guess_range,
)


# Episode identity: stated range + disc position (ADR 0001)
def test_parse_range():
    """Range answers parse to (lo, hi); junk and inverted ranges are None."""
    assert parse_range("5") == (5, 5)
    assert parse_range("4-6") == (4, 6)
    assert parse_range(" 4 - 6 ") == (4, 6)
    assert parse_range("0") is None
    assert parse_range("6-4") is None
    assert parse_range("x") is None
    assert parse_range("") is None


def test_next_unstaged():
    """Pre-fill starts at the smallest named episode not already staged."""
    names = {1: "ep1", 2: "ep2", 3: "ep3"}
    assert next_unstaged(names, {1}) == 2
    assert next_unstaged(names, {1, 2, 3}) == 4  # all named staged: max + 1
    assert next_unstaged({}, set()) == 1


def test_assign_range_generation_kill():
    """Disc order fills the stated range; the play-all stays extra."""
    titles = [(0, 3932, 1), (1, 3897, 1), (2, 3653, 1), (3, 11482, 1)]
    assert assign_range(titles, {3}, 1, 3) == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (0, 0)}


def test_assign_range_more_titles_than_range():
    """Eligible titles past the range size are extras."""
    titles = [(0, 1000, 1), (1, 1000, 1), (2, 1000, 1), (3, 1000, 1)]
    assert assign_range(titles, set(), 1, 3) == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (0, 0)}


def test_assign_range_fewer_titles_than_range():
    """Fewer eligible titles than the range: only part of it gets covered."""
    titles = [(0, 1000, 1), (1, 1000, 1)]
    assert assign_range(titles, set(), 1, 4) == {0: (1, 1), 1: (2, 2)}


# The episode guess: durations suggest the range, they never assign (ADR 0008)
def test_guess_range_downton_s1d1():
    """Downton S1D1 durations: the play-all (9643s) is out of the pool, the
    episodes 3940/2855/2852s sit green against runtimes E1=4020, E2=2820,
    E3=2820 (ratios 0.98, 1.01, 1.01) — the guess is 1-3."""
    titles = [(0, 3940, 1), (1, 2855, 1), (2, 2852, 1), (3, 9643, 1)]
    runtimes = {1: 4020, 2: 2820, 3: 2820}
    names = {1: "ep1", 2: "ep2", 3: "ep3"}
    assert guess_range(titles, {3}, runtimes, set(), names) == (1, 3)


def test_guess_range_starts_at_first_unstaged():
    """Disc 2 shape: E1-E3 staged, the walk starts at E4 and lands 4-6."""
    titles = [(0, 3940, 1), (1, 2855, 1), (2, 2852, 1), (3, 9643, 1)]
    runtimes = {1: 4020, 2: 2820, 3: 2820, 4: 4020, 5: 2820, 6: 2820}
    names = {e: f"ep{e}" for e in range(1, 7)}
    assert guess_range(titles, {3}, runtimes, {1, 2, 3}, names) == (4, 6)


def test_guess_range_leading_making_of_misconsumed():
    """Known limit (ADR 0008): a leading making-of that fits E1's runtime is
    consumed by the walk — the count still lands, but the positional fill the
    overview shows pins the making-of to E1; the confirm table is the check.
    Asserted here, not fixed."""
    titles = [(0, 2820, 1), (1, 2855, 1), (2, 2852, 1)]
    runtimes = {1: 2820, 2: 2820, 3: 2820}
    names = {1: "ep1", 2: "ep2", 3: "ep3"}
    guess = guess_range(titles, set(), runtimes, set(), names)
    assert guess == (1, 3)  # the count lands ...
    assert assign_range(titles, set(), *guess)[0] == (1, 1)  # ... as the making-of


def test_guess_range_stops_at_first_miss():
    """E2 finds no fitting title: the guess truncates there instead of
    skipping the episode ahead."""
    titles = [(0, 3940, 1), (1, 1800, 1)]  # 1800/2820 = 0.64: no fit for E2
    assert guess_range(titles, set(), {1: 4020, 2: 2820}, set(),
                       {1: "ep1", 2: "ep2"}) == (1, 1)


def test_guess_range_no_runtimes_returns_none():
    """No TMDB runtimes (season-0 shape) or a zero runtime: nothing to
    suggest — the caller falls back to the count-based pre-fill."""
    titles = [(0, 3940, 1), (1, 2855, 1)]
    names = {1: "ep1", 2: "ep2"}
    assert guess_range(titles, set(), {}, set(), names) is None
    assert guess_range(titles, set(), {1: 0, 2: 0}, set(), names) is None


def test_range_mismatch_note():
    """Matching counts -> None; mismatch -> a one-line note naming the escapes."""
    titles = [(0, 1000, 1), (1, 1000, 1), (2, 1000, 1)]
    assert range_mismatch_note(titles, set(), 1, 3) is None

    note = range_mismatch_note(titles, set(), 1, 2)
    assert note is not None and "\n" not in note
    # The suggested fix must always apply: 2=3 is refused past the season's
    # last episode, 2=e is not.
    assert "2=e" in note and "n to re-ask" in note

    assert range_mismatch_note(titles[:1], set(), 1, 4) is not None
    assert range_mismatch_note([], set(), 1, 4) is None  # no eligible titles


def test_range_past_end_note():
    """A range past the season's last episode warns; in-range and unknown-size
    (max_ep=0) do not."""
    assert range_past_end_note(5, 3) is not None
    assert "E5" in range_past_end_note(5, 3) and "3" in range_past_end_note(5, 3)
    assert range_past_end_note(3, 3) is None
    assert range_past_end_note(3, 5) is None
    assert range_past_end_note(5, 0) is None


def test_parse_fix_rows():
    """Parse fix row grammar."""
    rows = parse_fix_rows("1=5,2=1-2,3=e")
    assert rows == [(1, "ep", 5, 5), (2, "range", 1, 2), (3, "extra", 0, 0)]

    # Empty
    assert parse_fix_rows("") == []

    # Invalid range
    with pytest.raises(ValueError, match="bad double-episode range"):
        parse_fix_rows("1=2-1")

    # Invalid episode
    with pytest.raises(ValueError, match="bad episode number"):
        parse_fix_rows("1=0")

    # Bad format
    with pytest.raises(ValueError, match="bad episode-map entry"):
        parse_fix_rows("1=x")


def test_fix_rows_repoint():
    """Test fix row re-pointing."""
    assignment = {0: (1, 1), 1: (2, 2)}
    titles = [0, 1]
    staged = set()
    title_to_eps = {1: 0, 2: 1}

    # E3 is free; title 0 re-points to it, releasing E1
    ok = apply_fixes(assignment, titles, staged, "0=3", max_ep=5, title_to_eps=title_to_eps)

    assert ok
    assert assignment[0] == (3, 3)
    assert 1 not in title_to_eps or title_to_eps[1] != 0


def test_fix_rows_clash_rejection():
    """Reject row claiming already-claimed episode."""
    assignment = {0: (1, 1)}
    titles = [0, 1]  # Add title 1 even though it's not assigned yet
    staged = set()
    title_to_eps = {1: 0}

    # Try to assign title 1 to E1 (already claimed by title 0)
    ok = apply_fixes(assignment, titles, staged, "1=1", max_ep=5, title_to_eps=title_to_eps)

    # Should be rejected (E1 already claimed)
    assert not ok


def test_fix_rows_already_staged():
    """Reject row claiming already-staged episode."""
    assignment = {0: (1, 1)}
    titles = [0]
    staged = {1}  # E1 already staged
    title_to_eps = {}

    ok = apply_fixes(assignment, titles, staged, "0=1", max_ep=5, title_to_eps=title_to_eps)

    # Should be rejected
    assert not ok


def test_map_str():
    """Serialize assignment."""
    assignment = {0: (1, 1), 1: (2, 3), 2: (0, 0)}
    assert map_str(assignment) == "0=1,1=2-3,2=e"


# Durations grade assignments, they never change them (ADR 0001)
def test_audit_ratio():
    """disc ÷ tmdb at 2dp; unknown sides are None."""
    assert audit_ratio(3932, 4140) == 0.95
    assert audit_ratio(3897, 4080) == 0.96  # 0.9551 — round() gives 0.96
    assert audit_ratio(3653, 3840) == 0.95
    assert audit_ratio(3932, 0) is None  # TMDB has no runtime
    assert audit_ratio(0, 4140) is None
    assert audit_ratio(-1, 4140) is None


def test_audit_cell():
    """In-band green, out-of-band yellow; extras and missing runtimes blank.

    A double audits against the sum of both runtimes.
    """
    assert audit_cell(3932, 1, 1, {1: 4140}) == ("0.95", "green")
    assert audit_cell(6000, 1, 1, {1: 4000}) == ("1.50", "yellow")
    assert audit_cell(3932, 0, 0, {1: 4140}) is None  # extra: blank
    assert audit_cell(3932, 1, 1, {}) is None  # episode not in season: blank
    assert audit_cell(3932, 1, 1, {1: 0}) is None  # TMDB runtime missing: blank
    assert audit_cell(7800, 1, 2, {1: 3900, 2: 3900}) == ("1.00", "green")  # double sums


def test_audit_band_edges_inclusive():
    """The PAL band grades the exact quotient: 0.92..1.04 green, just outside
    yellow — even when the 2dp text rounds back onto an edge."""
    assert audit_cell(9200, 1, 1, {1: 10000}) == ("0.92", "green")
    assert audit_cell(10400, 1, 1, {1: 10000}) == ("1.04", "green")
    assert audit_cell(9190, 1, 1, {1: 10000}) == ("0.92", "yellow")  # 0.919
    assert audit_cell(10410, 1, 1, {1: 10000}) == ("1.04", "yellow")  # 1.041


def test_tv_floor():
    """Floor never drops below 600s, never raises above minlength."""
    assert tv_floor(120) == 600
    assert tv_floor(3000) == 3000


def test_sum_disqualified_generation_kill():
    """11482 = 3932+3897+3653 exactly: title 3 is the play-all."""
    titles = [(0, 3932, 1), (1, 3897, 1), (2, 3653, 1), (3, 11482, 1)]
    assert sum_disqualified(titles) == {3}


def test_sum_disqualified_clean_list():
    """No subset sums -> nothing disqualified."""
    titles = [(0, 2500, 1), (1, 2450, 1), (2, 2400, 1)]
    assert sum_disqualified(titles) == set()


def test_sum_disqualified_tolerance():
    """±120s tolerance: sum +100s still disqualifies, +200s does not."""
    plus_100 = [(0, 1000, 1), (1, 1500, 1), (2, 2600, 1)]
    assert sum_disqualified(plus_100) == {2}

    plus_200 = [(0, 1000, 1), (1, 1500, 1), (2, 2700, 1)]
    assert sum_disqualified(plus_200) == set()


def test_sum_disqualified_overlapping_playalls():
    """Several play-alls on one disc all get disqualified."""
    titles = [(0, 1000, 1), (1, 1000, 1), (2, 2000, 1), (3, 3000, 1), (4, 4000, 1)]
    assert sum_disqualified(titles) == {2, 3, 4}


def test_sum_disqualified_subset_size_cap():
    """Sum of 7 components does not disqualify (subset must be 2..6)."""
    titles = [(i, 600, 1) for i in range(7)] + [(7, 4200, 1)]
    assert sum_disqualified(titles) == set()


# Play-alls are never ripped; a fix row rescuing one rips it (ADR 0008)
def test_playall_skip():
    """The skip set holds exactly the play-alls whose final assignment is
    extra: the rescued play-all (t3=E5) rips, the play-all left extra (t4)
    skips, and the plain extra (t2) is not a play-all so it still rips."""
    assignment = {0: (1, 1), 1: (2, 2), 2: (0, 0), 3: (5, 5), 4: (0, 0)}
    assert playall_skip({3, 4}, assignment) == {4}


def test_playall_skip_all_rescued_or_pinned():
    """Every play-all rescued by fix rows: the skip set is empty (rip all as
    today); every play-all pinned extra: all of it skips."""
    assert playall_skip({3, 4}, {3: (1, 2), 4: (5, 5)}) == set()
    assert playall_skip({3, 4}, {3: (0, 0), 4: (0, 0)}) == {3, 4}


# The play-all-only trigger: one eligible title assigned a range (ADR 0009)
def test_playall_only_fires():
    """One eligible title whose final assignment is a range: the trigger
    names the title and the range it carries."""
    assert playall_only([(0, 9643, 1)], set(), {0: (1, 3)}) == (0, 1, 3)


def test_playall_only_lone_eligible_beside_playall():
    """The play-all set shrinks the pool: a ranged title beside a pinned
    play-all is still the lone eligible title."""
    titles = [(0, 9643, 1), (1, 3000, 1)]
    assert playall_only(titles, {1}, {0: (2, 4), 1: (0, 0)}) == (0, 2, 4)


def test_playall_only_disarm_two_eligible():
    """Multi-title discs never fire — even when only one title carries a
    range: a double on a multi-title disc stays a double (ADR 0009)."""
    titles = [(0, 3000, 1), (1, 6200, 1)]
    assert playall_only(titles, set(), {0: (1, 1), 1: (2, 3)}) is None
    assert playall_only(titles, set(), {0: (0, 0), 1: (1, 3)}) is None


def test_playall_only_disarm_single_episode():
    """The lone eligible title assigned one episode is an ordinary disc."""
    assert playall_only([(0, 3000, 1)], set(), {0: (2, 2)}) is None


def test_playall_only_disarm_extra():
    """The lone eligible title left extra — or unmapped — fires nothing."""
    assert playall_only([(0, 3000, 1)], set(), {0: (0, 0)}) is None
    assert playall_only([(0, 3000, 1)], set(), {}) is None
