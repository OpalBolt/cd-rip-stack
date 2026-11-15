"""Tests for TV assignment math: floor, play-alls, the guess and prefill, the
table's violation checks, audit."""

import pytest

from skive.tv_match import (
    next_unstaged, assign_range, tv_floor, sum_disqualified,
    sum_disqualified_windowed, playall_only, audit_ratio, audit_cell,
    guess_pairing, prefill_assignment, episode_clashes, staged_claims,
    unfilled_episodes, past_end_claims, sub_floor_titles,
)


# Episode identity: stated range + disc position (ADR 0001)
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


# The episode guess: durations suggest the range, they never assign (ADR 0008)
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
    """Windows: sum +100s is tight, +200s is soft (both disqualify now),
    +400s is outside both."""
    plus_100 = [(0, 1000, 1), (1, 1500, 1), (2, 2600, 1)]
    assert sum_disqualified(plus_100) == {2}

    plus_200 = [(0, 1000, 1), (1, 1500, 1), (2, 2700, 1)]
    assert sum_disqualified(plus_200) == {2}  # soft window

    plus_400 = [(0, 1000, 1), (1, 1500, 1), (2, 2900, 1)]
    assert sum_disqualified(plus_400) == set()


def test_sum_disqualified_windowed_tiers():
    """Each window yields its tier and the matched component subset as
    evidence for the `≈ sum of …` note."""
    tight = [(0, 1000, 1), (1, 1500, 1), (2, 2520, 1)]  # 2500, +20s
    disq = sum_disqualified_windowed(tight)
    assert disq == {2: ("tight", [0, 1])}

    soft = [(0, 1000, 1), (1, 1500, 1), (2, 2700, 1)]  # 2500, +200s
    disq = sum_disqualified_windowed(soft)
    assert disq == {2: ("soft", [0, 1])}

    assert sum_disqualified_windowed([(0, 1000, 1), (1, 1500, 1)]) == {}


def test_sum_disqualified_overlapping_playalls():
    """Several play-alls on one disc all get disqualified."""
    titles = [(0, 1000, 1), (1, 1000, 1), (2, 2000, 1), (3, 3000, 1), (4, 4000, 1)]
    assert sum_disqualified(titles) == {2, 3, 4}


def test_sum_disqualified_subset_size_cap():
    """Sum of 7 components does not disqualify (subset must be 2..6)."""
    titles = [(i, 600, 1) for i in range(7)] + [(7, 4200, 1)]
    assert sum_disqualified(titles) == set()


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


# The engine seams extracted in slice 01

def test_guess_pairing_pairs_episodes_with_titles():
    """The guess walk pairs each walked episode with its own title."""
    titles = [(0, 3940, 1), (1, 2855, 1), (2, 2852, 1), (3, 9643, 1)]
    runtimes = {1: 4020, 2: 2820, 3: 2820}
    names = {1: "ep1", 2: "ep2", 3: "ep3"}
    pairing = guess_pairing(titles, {3}, runtimes, set(), names)
    assert pairing == {1: 0, 2: 1, 3: 2}


def test_prefill_assignment_uses_green_fit():
    """Green ratios pair each episode with its own title; play-alls stay extra."""
    titles = [(0, 3940, 1), (1, 2855, 1), (2, 2852, 1), (3, 9643, 1)]
    runtimes = {1: 4020, 2: 2820, 3: 2820}
    names = {1: "ep1", 2: "ep2", 3: "ep3"}
    assert prefill_assignment(titles, {3}, runtimes, set(), names) == {
        0: (1, 1), 1: (2, 2), 2: (3, 3), 3: (0, 0)}


def test_prefill_assignment_starts_at_first_unstaged():
    """Staged episodes are a contiguous prefix; the prefill continues past them."""
    titles = [(0, 3940, 1), (1, 2855, 1), (2, 2852, 1)]
    runtimes = {4: 4020, 5: 2820, 6: 2820}
    names = {4: "ep4", 5: "ep5", 6: "ep6"}
    assert prefill_assignment(titles, set(), runtimes, {1, 2, 3}, names) == {
        0: (4, 4), 1: (5, 5), 2: (6, 6)}


def test_prefill_assignment_fallback_one_per_eligible():
    """No runtimes (or no green matches): one episode per eligible title."""
    titles = [(0, 1000, 1), (1, 1000, 1), (2, 11482, 1)]
    names = {1: "ep1", 2: "ep2"}
    assert prefill_assignment(titles, {2}, {}, set(), names) == {
        0: (1, 1), 1: (2, 2), 2: (0, 0)}
    # Zero green matches (runtimes wildly off) falls back the same way.
    assert prefill_assignment(titles, {2}, {1: 100, 2: 100}, set(), names) == {
        0: (1, 1), 1: (2, 2), 2: (0, 0)}


def test_prefill_assignment_empty_and_playall_only_edges():
    """Empty title list -> empty map; a play-all-only disc prefills nothing."""
    names = {1: "ep1"}
    assert prefill_assignment([], set(), {}, set(), names) == {}
    titles = [(3, 11482, 1)]
    assert prefill_assignment(titles, {3}, {1: 4020}, set(), names) == {3: (0, 0)}


def test_episode_clashes():
    """An episode claimed by two titles is a clash; singles and extras are not."""
    assignment = {0: (1, 2), 1: (2, 2), 2: (0, 0)}
    assert episode_clashes(assignment) == [(2, [0, 1])]
    assert episode_clashes({0: (1, 1), 1: (2, 3)}) == []


def test_staged_claims():
    """Assignments touching staged episodes come back as (episode, title)."""
    assignment = {0: (1, 3), 1: (2, 2)}
    assert staged_claims(assignment, {2}) == [(2, 0), (2, 1)]
    assert staged_claims(assignment, set()) == []
    assert staged_claims({0: (0, 0)}, {1, 2}) == []


def test_unfilled_episodes():
    """Holes inside the covered range; extras never fill."""
    assignment = {0: (1, 2), 1: (4, 4), 2: (0, 0)}
    assert unfilled_episodes(1, 4, assignment) == [3]
    assert unfilled_episodes(1, 2, assignment) == []
    assert unfilled_episodes(1, 3, {0: (1, 2)}) == [3]
    assert unfilled_episodes(1, 4, {0: (0, 0)}) == [1, 2, 3, 4]


def test_past_end_claims():
    """Assignments running past the season's last episode, ascending; an
    unknown season size (0) never flags."""
    a = {0: (1, 1), 1: (9, 9), 2: (3, 4), 3: (11, 12)}
    assert past_end_claims(a, 8) == [(9, 1), (12, 3)]
    assert past_end_claims(a, 0) == []
    assert past_end_claims({0: (1, 2)}, 2) == []


def test_sub_floor_titles():
    """Sub-floor titles, in disc order, numbers intact."""
    titles = [(0, 100, 1), (1, 5000, 1), (2, 599, 1)]
    assert sub_floor_titles(titles, 600) == [0, 2]
    assert sub_floor_titles(titles, 4000) == [0, 2]
    assert sub_floor_titles([(7, 600, 1)], 600) == []
