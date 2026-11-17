"""Headless Pilot tests for the selection table (ADR 0020): the movie table
(slice 03: cursor movement, the three verbs, several-near prefill,
exactly-one-main, accept, quit and interrupt) and the TV episode table (slice
04: typed episodes and doubles, Space mark-fill, sub-floor and play-all
rescue, blocking and warning states, `c` clear-staged) — plus render checks
at 80 columns and a narrow width, in color and NO_COLOR."""

import asyncio
import io
from pathlib import Path

from rich.console import Console
from textual.widgets import DataTable

from skive import ui
from skive.scan import TitleInfo, _parse_titles
from skive.selection import (
    COLUMNS,
    EXTRA_ACTION,
    INTERRUPT,
    QUIT,
    SKIP_ACTION,
    EpisodeTable,
    SelectionTable,
    decoy_numbers,
    episode_map,
    graded_rows,
    parse_episode,
    prefill,
    print_accepted,
    print_accepted_episodes,
    rip_set_line,
)
from skive.tv_match import playall_only, sum_disqualified_windowed, tv_floor
from skive.ui import THEME
from skive.validation import MAIN_ACTION

# The Mamma Mia disc: two feature-length titles near the 6480s runtime,
# plus a short. 6252/6480 = 0.96 green; 3000/6480 = 0.46 yellow.
TITLES = [
    TitleInfo(21, 6252, 4_300_000_000, "dan", "", False),
    TitleInfo(12, 6662, 19_000_000_000, "dan", "", False),
    TitleInfo(3, 300, 500_000_000, "dan", "", False),
]
YELLOW_TITLES = [
    TitleInfo(1, 6300, 5_000_000_000, "en", "", False),
    TitleInfo(2, 3000, 6_000_000_000, "en", "", False),
]
SHORTS = [
    TitleInfo(1, 600, 100_000_000, "en", "", False),
    TitleInfo(2, 900, 200_000_000, "en", "", False),
]


def _scenario(keys, check, titles=TITLES, runtime_s=6480, size=(80, 24)):
    """Run the table headless, press `keys`, and run `check(app)` inside the
    live context — the widget tree is pruned once the app exits."""

    async def go():
        app = SelectionTable("Mamma Mia! (2008)", titles, runtime_s)
        async with app.run_test(size=size) as pilot:
            if keys:
                await pilot.press(*keys)
                await pilot.pause()
            check(app)

    asyncio.run(go())


# --- prefill (the audited defaults; arbitration itself is slice 01's) --------


def test_prefill_sole_near_and_skip_rest():
    """One candidate near the runtime prefills main; everything else skips."""
    actions, notes = prefill(
        [
            TitleInfo(5, 6400, 8_000_000_000, "en", "", False),
            TitleInfo(2, 600, 100_000_000, "en", "", False),
        ],
        6480,
    )
    assert actions == {5: MAIN_ACTION, 2: SKIP_ACTION}
    assert notes == {}


def test_prefill_several_near_names_the_ambiguity_in_notes():
    """Several near the runtime prefill biggest, with the ambiguity fact as
    the main row's notes cell."""
    actions, notes = prefill(TITLES, 6480)
    assert actions == {21: SKIP_ACTION, 12: MAIN_ACTION, 3: SKIP_ACTION}
    assert notes == {12: "2 titles near the runtime"}


def test_prefill_shorts_take_biggest_and_note_it():
    """No candidate main at all: the biggest title still prefills main so
    exactly one main holds for Enter, noted on its row."""
    actions, notes = prefill(SHORTS, 900)
    assert actions == {1: SKIP_ACTION, 2: MAIN_ACTION}
    assert notes == {2: "biggest bytes — no candidate main"}


def test_prefill_empty_scan_names_no_main():
    actions, notes = prefill([], 6480)
    assert actions == {} and notes == {}


# The replayed Flame & Citron disc: t38 is the sole green clean title
# (7816/7800 = 1.002); t0 (ratio 0.856, yellow) is clean too; the other 82
# titles share one segment map — decoy cluster ×82.
DECOY_TITLES = _parse_titles(
    (Path(__file__).parent / "fixtures" / "flammencitron-decoys.txt").read_text()
)


def test_prefill_flammencitron_decoy_disc_names_t38_and_notes_the_cluster():
    """The replayed F&C dump: t38 prefills main (the sole green clean title),
    every one of the 82 decoy members' notes cells names the cluster."""
    actions, notes = prefill(DECOY_TITLES, 7800)
    assert actions[38] == MAIN_ACTION
    assert sum(1 for a in actions.values() if a == MAIN_ACTION) == 1
    decoy_notes = [n for n in notes.values() if n.startswith("decoy cluster")]
    assert len(decoy_notes) == 82
    assert all(n == "decoy cluster ×82" for n in decoy_notes)
    assert 38 not in notes  # the main is clean: no decoy fact on its row


def test_prefill_skips_bigger_decoys_for_the_clean_candidate():
    """Decoys with more bytes than the one clean green candidate never
    become main — neither by arbitration nor by the biggest-bytes fallback."""
    titles = [
        TitleInfo(1, 6500, 20_000_000_000, "en", "", False, decoy_cluster=3),
        TitleInfo(2, 6400, 19_000_000_000, "en", "", False, decoy_cluster=3),
        TitleInfo(3, 6440, 3_000_000_000, "en", "", False),
    ]
    actions, notes = prefill(titles, 6480)
    assert actions == {1: SKIP_ACTION, 2: SKIP_ACTION, 3: MAIN_ACTION}
    assert notes == {1: "decoy cluster ×3", 2: "decoy cluster ×3"}


def test_prefill_scrambled_title_is_skipped_and_noted():
    titles = [
        TitleInfo(1, 6450, 4_000_000_000, "en", "", True),
        TitleInfo(2, 6400, 4_000_000_000, "en", "", False),
    ]
    actions, notes = prefill(titles, 6480)
    assert actions == {1: SKIP_ACTION, 2: MAIN_ACTION}
    assert notes == {1: "shuffled map"}


def test_prefill_all_decoys_still_names_exactly_one_main():
    """An all-decoy disc: the biggest title overall still prefills main —
    the one-main invariant holds, row note and log state the cluster fact."""
    log = _FakeLog()
    titles = [
        TitleInfo(1, 6000, 5_000_000_000, "en", "", False, decoy_cluster=3),
        TitleInfo(2, 6500, 6_000_000_000, "en", "", False, decoy_cluster=3),
    ]
    actions, notes = prefill(titles, 6480, log)
    assert actions == {1: SKIP_ACTION, 2: MAIN_ACTION}
    assert notes == {
        1: "decoy cluster ×3",
        2: "decoy cluster ×3 biggest bytes — every title decoy-clustered",
    }
    assert log.notes == [
        ("main", ("main=t2 biggest bytes — every title decoy-clustered — skipped 2 decoys"))
    ]


def test_prefill_decoys_and_clean_subfloor_titles_pick_clean_biggest():
    """A decoy cluster above the floor plus clean sub-floor titles: the
    main is the biggest clean title (not the decoy), with the no-candidate
    wording — no false "every title decoy-clustered" fact anywhere."""
    log = _FakeLog()
    titles = [
        TitleInfo(1, 6500, 20_000_000_000, "en", "", False, decoy_cluster=3),
        TitleInfo(2, 6400, 19_000_000_000, "en", "", False, decoy_cluster=3),
        TitleInfo(3, 300, 3_000_000_000, "en", "", False),
    ]
    actions, notes = prefill(titles, 6480, log)
    assert actions == {1: SKIP_ACTION, 2: SKIP_ACTION, 3: MAIN_ACTION}
    assert notes == {
        1: "decoy cluster ×3",
        2: "decoy cluster ×3",
        3: "biggest bytes — no candidate main",
    }
    assert log.notes == [
        ("main", ("main=t3 biggest bytes — no candidate main on the disc — skipped 2 decoys"))
    ]


def test_prefill_main_log_line_notes_skipped_decoy_count():
    """The run-log main line gains `— skipped N decoys` when N>0 and stays
    byte-identical to the old line when none were skipped."""
    log = _FakeLog()
    prefill(DECOY_TITLES, 7800, log)
    assert log.notes == [("main", ("main=t38 near the runtime — automatic — skipped 82 decoys"))]
    clean_log = _FakeLog()
    prefill(TITLES, 6480, clean_log)
    assert clean_log.notes == [
        ("main", ("main=t12 default (biggest bytes) — 2 titles near the runtime"))
    ]


class _FakeLog:
    """Captures note() calls like the run log's phase notes."""

    def __init__(self):
        self.notes = []

    def note(self, phase, text, echo=True):
        self.notes.append((phase, text))


def test_m_on_a_decoy_row_moves_the_main_and_enter_accepts_it():
    """Notes are facts, never gates: `m` on a decoy row moves the main
    there and Enter still accepts."""
    _scenario(
        ("m", "enter"),
        lambda app: assert_eq(
            app.return_value, dict(app.return_value) | {DECOY_TITLES[0].number: MAIN_ACTION}
        ),
        titles=DECOY_TITLES,
        runtime_s=7800,
    )


def test_decoy_fact_shows_in_the_table_and_the_accepted_echo(monkeypatch):
    """The decoy cluster fact is visible in the table's notes cell and in
    the accepted-table echo under it."""

    def check(app):
        # rows in title order: row 0 is t0 (clean), row 1 the first decoy
        assert_eq("decoy cluster ×82" in str(app.query_one(DataTable).get_row_at(1)[5]), True)

    _scenario((), check, titles=DECOY_TITLES, runtime_s=7800)
    out = io.StringIO()
    con = Console(
        file=out,
        width=120,
        theme=THEME,
        highlight=False,
        emoji=False,
        force_terminal=False,
        color_system=None,
    )
    monkeypatch.setattr(ui, "console", con)
    print_accepted("Flammen & Citronen", DECOY_TITLES, {38: MAIN_ACTION}, 7800)
    assert "decoy cluster ×82" in out.getvalue()


def test_prefill_unknown_runtime_prefills_biggest_silently():
    actions, notes = prefill(TITLES, None)
    assert actions[12] == MAIN_ACTION and notes == {}


def test_graded_rows_keep_the_validation_shape():
    """{"t", "dur_s", "ratio", "band"} per scanned title in title order,
    sub-floor and unknown-runtime rows ungraded — the record's old shape."""
    assert graded_rows(TITLES, 6480) == [
        {"t": 3, "dur_s": 300, "ratio": None, "band": None},
        {"t": 12, "dur_s": 6662, "ratio": 6662 / 6480, "band": "green"},
        {"t": 21, "dur_s": 6252, "ratio": 6252 / 6480, "band": "green"},
    ]
    assert graded_rows(TITLES, None) == [
        {"t": 3, "dur_s": 300, "ratio": None, "band": None},
        {"t": 12, "dur_s": 6662, "ratio": None, "band": None},
        {"t": 21, "dur_s": 6252, "ratio": None, "band": None},
    ]


def test_rip_set_line_names_main_only_and_extras():
    assert rip_set_line({21: MAIN_ACTION}) == "rip set: t21 only"
    assert (
        rip_set_line({21: MAIN_ACTION, 3: EXTRA_ACTION})
        == "rip set: t21 + extras t3 (extras marked for encoding)"
    )


# --- Pilot: keys over the real component -------------------------------------


def test_cursor_moves_with_arrows_and_jk():
    _scenario(("down", "j", "k"), lambda app: assert_eq(app.query_one(DataTable).cursor_row, 1))


def test_verbs_restate_the_row_action():
    """e marks an extra, s skips — at the cursor."""
    # rows render in title order: row 2 is t21, the biggest-bytes title
    _scenario(("down", "down", "e"), lambda app: assert_eq(app.actions[21], EXTRA_ACTION))
    _scenario(("down", "down", "s"), lambda app: assert_eq(app.actions[21], SKIP_ACTION))


def test_m_moves_the_single_main():
    """m at the short row moves the main off t12 onto t3."""
    _scenario(
        ("down", "down", "m"),
        lambda app: assert_eq(app.actions, {21: MAIN_ACTION, 12: SKIP_ACTION, 3: SKIP_ACTION}),
    )


def test_verbs_never_demote_the_main():
    """e/s on the main row are no-ops — exactly one main always holds."""
    _scenario(
        ("e", "s"),
        lambda app: (
            assert_eq(sum(1 for a in app.actions.values() if a == MAIN_ACTION), 1),
            assert_eq(app.actions[12], MAIN_ACTION),
        ),
    )


def test_accept_returns_the_action_map():
    _scenario(
        ("down", "down", "e", "enter"),
        lambda app: assert_eq(
            app.return_value, {21: EXTRA_ACTION, 12: MAIN_ACTION, 3: SKIP_ACTION}
        ),
    )


def test_yellow_rows_never_block_enter():
    """A far-off ratio grades yellow in the audit column — Enter still rips."""
    _scenario(
        ("enter",),
        lambda app: (
            assert_isinstance(app.return_value, dict),
            assert_eq(app.return_value[1], MAIN_ACTION),
        ),
        titles=YELLOW_TITLES,
    )


def test_quit_returns_nothing_ripped():
    _scenario(("q",), lambda app: assert_eq(app.return_value, QUIT))


def test_interrupt_returns_the_interrupt_sentinel():
    _scenario(("ctrl+c",), lambda app: assert_eq(app.return_value, INTERRUPT))


def test_exactly_one_main_after_every_edit():
    """A scramble of moves and verbs leaves exactly one main standing."""

    def check(app):
        assert sum(1 for a in app.actions.values() if a == MAIN_ACTION) == 1

    _scenario(("down", "m", "k", "e", "down", "down", "s", "m", "k", "s"), check)


def assert_eq(got, want):
    assert got == want, f"{got!r} != {want!r}"


def assert_isinstance(got, kind):
    assert isinstance(got, kind)


# --- render checks: 80 columns and narrow, color and NO_COLOR ----------------


def test_rows_render_six_columns_at_80_and_narrow():
    """The component renders the six universal columns with the audited
    cells at 80 columns and at a narrow width; rows sit in title order."""

    def check(app):
        table = app.query_one(DataTable)
        assert_eq(len(table.columns), len(COLUMNS))
        assert_eq(table.row_count, len(TITLES))
        assert_eq(
            [str(c) for c in table.get_row_at(0)[:3]], ["3", "0:05:00", "476.8 MiB"]
        )  # the short
        assert_eq(str(table.get_row_at(0)[4]), SKIP_ACTION)
        assert_eq(str(table.get_row_at(1)[4]), MAIN_ACTION)
        assert_eq([str(c) for c in table.get_row_at(2)[:3]], ["21", "1:44:12", "4.0 GiB"])

    for size in ((80, 24), (48, 24)):
        _scenario((), check, size=size)


def test_audit_cell_grades_green_yellow_and_blanks():
    """Yellow ratios are visible in the audit column, never blocking; blank
    (not yellow) when the runtime is unknown or the title is sub-floor."""

    def graded(app):
        table = app.query_one(DataTable)
        assert_eq(str(table.get_row_at(0)[3]), "0.97")
        assert_eq(table.get_row_at(0)[3].style, "green")
        assert_eq(str(table.get_row_at(1)[3]), "0.46")
        assert_eq(table.get_row_at(1)[3].style, "yellow")

    _scenario((), graded, titles=YELLOW_TITLES)
    _scenario(
        (),
        lambda app: assert_eq(str(app.query_one(DataTable).get_row_at(0)[3]), ""),
        titles=TITLES,
        runtime_s=None,
    )  # unknown runtime: blank
    _scenario(
        (), lambda app: assert_eq(str(app.query_one(DataTable).get_row_at(0)[3]), "")
    )  # the short


def test_prefill_note_lands_in_the_notes_cell():
    _scenario(
        (),
        lambda app: (
            assert_eq("near the runtime" in str(app.query_one(DataTable).get_row_at(1)[5]), True),
            assert_eq(str(app.query_one(DataTable).get_row_at(0)[5]), ""),
        ),
    )


def test_render_in_color_and_no_color():
    """The component renders the same cell text headless with or without the
    NO_COLOR environment set."""

    def check(app):
        table = app.query_one(DataTable)
        assert_eq(
            [str(c) for c in table.get_row_at(1)],
            ["12", "1:51:02", "17.7 GiB", "1.03", MAIN_ACTION, "2 titles near the runtime"],
        )

    for no_color in (False, True):

        def go(no_color=no_color):
            import os

            if no_color:
                os.environ["NO_COLOR"] = "1"
            else:
                os.environ.pop("NO_COLOR", None)
            try:
                _scenario((), check)
            finally:
                os.environ.pop("NO_COLOR", None)

        go()


# --- the accepted echo (plain lines into scrollback) -------------------------


def _echo(monkeypatch, width, color, no_color=False):
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    out = io.StringIO()
    con = Console(
        file=out,
        width=width,
        theme=THEME,
        highlight=False,
        emoji=False,
        force_terminal=color,
        color_system="truecolor" if color and not no_color else None,
    )
    monkeypatch.setattr(ui, "console", con)
    print_accepted(
        "Mamma Mia! (2008)", TITLES, {21: MAIN_ACTION, 3: EXTRA_ACTION, 12: SKIP_ACTION}, 6480
    )
    return out.getvalue()


def test_echo_is_pipe_safe_plain_lines_at_80(monkeypatch):
    """Off a TTY the echo renders the ASCII table — no escape codes, pipe-safe."""
    out = _echo(monkeypatch, width=80, color=False)
    assert "\x1b[" not in out
    assert "Mamma Mia! (2008)" in out
    for col in COLUMNS:
        assert col in out
    assert "rip set" not in out  # the rip set line is the caller's ui.info


def test_echo_at_a_narrow_width(monkeypatch):
    out = _echo(monkeypatch, width=48, color=False)
    assert "Mamma Mia! (2008)" in out
    for col in COLUMNS:
        assert col in out


def test_echo_no_color_is_plain(monkeypatch):
    """NO_COLOR on a TTY still renders the plain table, escape-free."""
    out = _echo(monkeypatch, width=80, color=True, no_color=True)
    assert "\x1b[" not in out
    assert "Mamma Mia! (2008)" in out


def test_echo_in_color_on_a_tty(monkeypatch):
    out = _echo(monkeypatch, width=80, color=True)
    assert "Mamma Mia! (2008)" in out
    assert "main" in out and "extra" in out


# --- TV: the episode table (slice 04) ----------------------------------------

# The Generation Kill disc: three episodes, a play-all (t3 ≈ sum of 0, 1, 2)
# and a sub-floor making-of (t4). Rows render: 0, 1, 2, 3, then 4 last.
GK = [
    TitleInfo(0, 3932, 1_000_000_000, "dan", "", False),
    TitleInfo(1, 3897, 1_000_000_000, "dan", "", False),
    TitleInfo(2, 3653, 1_000_000_000, "dan", "", False),
    TitleInfo(3, 11482, 3_000_000_000, "dan", "", False),
    TitleInfo(4, 300, 100_000_000, "dan", "", False),
]
GK_EPS = {e: f"ep{e}" for e in range(1, 11)}
GK_RUNTIMES = {1: 4140, 2: 4080, 3: 3840, **{e: 3900 for e in range(4, 11)}}


def _gk_map(staged=(), titles=GK):
    info = [(t.number, t.seconds, t.size) for t in titles if t.seconds >= 600]
    return episode_map(
        1, titles, 120, sum_disqualified_windowed(info), GK_EPS, GK_RUNTIMES, set(staged)
    )


def _tv(keys, check, emap=None, staging=(), size=(100, 30)):
    """Run the episode table headless, press `keys` (a pause between each so
    modals settle), run `check(app)` inside the live context. `staging` is
    the list of files `c` offers; the clear callback deletes them."""

    async def go():
        emap_ = emap or _gk_map()
        files = list(staging)
        app = EpisodeTable(
            "Generation Kill (2008)",
            emap_,
            lambda: [f for f in files if f.exists()],
            lambda doomed: ([f.unlink() for f in doomed], set())[1],
        )
        async with app.run_test(size=size) as pilot:
            for k in keys:
                await pilot.press(k)
                await pilot.pause()
            check(app)

    asyncio.run(go())


def _status(app) -> str:
    return app._status_text().plain


def test_parse_episode_values():
    assert parse_episode("3") == (3, 3)
    assert parse_episode("3-4") == (3, 4)
    for bad in ("", "0", "3-", "-3", "4-3", "x", "3-4-5"):
        assert parse_episode(bad) is None


def test_episode_map_prefill_guess_playall_and_sub_floor():
    """The guess fills the eligible titles; the play-all prefills skip with
    its sum evidence; the sub-floor title lists last, real number, skip."""
    emap = _gk_map()
    assert emap.assign == {0: (1, 1), 1: (2, 2), 2: (3, 3), 3: None, 4: None}
    assert [t.number for t in emap.titles] == [0, 1, 2, 3, 4]
    assert emap.evidence == {3: "≈ sum of 0, 1, 2", 4: "below the TV floor"}


# --- TV: decoys leave eligibility before sums and the guess (slice 03) -------


def _rip_fed_map(titles, eps=GK_EPS, runtimes=GK_RUNTIMES):
    """episode_map fed exactly as rip_tv does: the floored, decoy-free list
    drives the play-all sums; the guess pool is floored AND non-decoy."""
    decoys = decoy_numbers(titles)
    info = [
        (t.number, t.seconds, t.size)
        for t in titles
        if t.seconds >= tv_floor(120) and t.number not in decoys
    ]
    return episode_map(1, titles, 120, sum_disqualified_windowed(info), eps, runtimes, set())


def test_decoys_with_greener_fit_get_none_and_the_decoy_note():
    """Decoy rows never take an episode however green their ratio; clean
    titles take the episodes; a sub-floor decoy names the cluster, not the
    floor, and still lists last."""
    titles = [
        TitleInfo(0, 3932, 1_000_000_000, "dan", "", False),
        TitleInfo(1, 3897, 1_000_000_000, "dan", "", False),
        TitleInfo(
            2, 4100, 2_000_000_000, "dan", "", False, decoy_cluster=3
        ),  # 4100/3932 = 1.04, yellow — excluded
        # anyway; t3 is the green decoy (1.00)
        TitleInfo(3, 3900, 2_000_000_000, "dan", "", True),
        TitleInfo(4, 300, 100_000_000, "dan", "", False, decoy_cluster=2),
    ]
    emap = _rip_fed_map(titles, runtimes={1: 3932, 2: 3897})
    assert emap.assign == {0: (1, 1), 1: (2, 2), 2: None, 3: None, 4: None}
    assert emap.evidence == {2: "decoy cluster ×3", 3: "shuffled map", 4: "decoy cluster ×2"}
    assert [t.number for t in emap.titles] == [0, 1, 2, 3, 4]


def test_decoy_durations_never_sum_disqualify_the_real_title():
    """A real title whose duration equals the sum of decoy durations keeps
    its episode — the decoys never reach the sums."""
    titles = [
        TitleInfo(0, 3000, 1_000_000_000, "dan", "", False),
        TitleInfo(1, 1500, 1_000_000_000, "dan", "", False, decoy_cluster=2),
        TitleInfo(2, 1500, 1_000_000_000, "dan", "", False, decoy_cluster=2),
    ]
    decoys = decoy_numbers(titles)
    info = [
        (t.number, t.seconds, t.size)
        for t in titles
        if t.seconds >= tv_floor(120) and t.number not in decoys
    ]
    # The decoy-inclusive shape WOULD sum-match t0; the decoy-free one must not.
    assert sum_disqualified_windowed(
        [(t.number, t.seconds, t.size) for t in titles if t.seconds >= 600]
    ) == {0: ("tight", [1, 2])}
    assert sum_disqualified_windowed(info) == {}
    emap = _rip_fed_map(titles, runtimes={1: 3000})
    assert emap.assign[0] == (1, 1)
    assert all("≈ sum of" not in e for e in emap.evidence.values())


def test_all_decoys_input_prefills_every_row_none_with_notes():
    titles = [
        TitleInfo(0, 3932, 1_000_000_000, "dan", "", False, decoy_cluster=2),
        TitleInfo(1, 3897, 1_000_000_000, "dan", "", True),
    ]
    emap = _rip_fed_map(titles)
    assert emap.assign == {0: None, 1: None}
    assert emap.evidence == {0: "decoy cluster ×2", 1: "shuffled map"}


def test_playall_only_fires_on_the_decoy_free_list_beside_a_cluster():
    """The chapter-split trigger judges decoy-free eligibility: a lone clean
    title beside a decoy cluster still fires."""
    titles = [(0, 6000, 1), (1, 1500, 1), (2, 1500, 1)]  # 1, 2 decoy-excluded
    decoy_free = [t for t in titles if t[0] not in {1, 2}]
    assert playall_only(decoy_free, set(), {0: (1, 2)}) == (0, 1, 2)


def test_accepted_echo_renders_the_decoy_note(monkeypatch):
    out = io.StringIO()
    con = Console(
        file=out, width=80, theme=THEME, highlight=False, emoji=False, force_terminal=False
    )
    monkeypatch.setattr(ui, "console", con)
    titles = [
        TitleInfo(0, 3932, 1_000_000_000, "dan", "", False, decoy_cluster=3),
        TitleInfo(1, 3897, 1_000_000_000, "dan", "", False),
    ]
    emap = _rip_fed_map(titles, runtimes={1: 3897})
    print_accepted_episodes("Show (2000)", emap, {0: None, 1: (1, 1)})
    assert "decoy cluster ×3" in out.getvalue()


def test_sub_floor_rows_list_last_even_when_scanned_first():
    titles = [GK[4], *GK[:4]]
    assert [t.number for t in _gk_map(titles=titles).titles] == [0, 1, 2, 3, 4]


def test_rows_render_six_columns_with_audit_and_evidence():
    """Six columns at 80 columns and a narrow width; episode names and the
    play-all / sub-floor evidence in notes, the ratio in the audit column."""

    def check(app):
        table = app.query_one(DataTable)
        assert_eq(len(table.columns), len(COLUMNS))
        assert_eq(table.row_count, 5)
        row0 = [str(c) for c in table.get_row_at(0)]
        assert_eq(row0[0], "0")
        assert_eq(row0[3], "0.95")
        assert_eq(row0[4], "E01")
        assert_eq(row0[5], "ep1")
        assert_eq([str(c) for c in table.get_row_at(3)][4:], ["skip", "≈ sum of 0, 1, 2"])
        assert_eq([str(c) for c in table.get_row_at(4)][4:], ["skip", "below the TV floor"])

    for size in ((80, 24), (48, 24)):
        _tv((), check, size=size)


def test_typed_episode_sets_the_cursor_row_on_enter():
    """Digits accumulate at the cursor; Enter commits the value (and only
    that — a second Enter accepts)."""

    def check(app):
        assert_eq(app.emap.assign[0], (5, 5))
        assert_eq(app.return_value, None)  # the first Enter did not accept

    _tv(("5", "enter"), check)
    _tv(
        ("5", "enter", "enter"),
        lambda app: assert_eq(
            app.return_value, {0: (5, 5), 1: (2, 2), 2: (3, 3), 3: None, 4: None}
        ),
    )


def test_typed_double_is_one_range():
    _tv(
        ("down", "down", "3", "minus", "4", "enter"),
        lambda app: assert_eq(app.emap.assign[2], (3, 4)),
    )


def test_typing_shows_an_input_line_and_backspace_edits_it():
    def check(app):
        assert "episode ▸ 3" in _status(app)

    _tv(("3", "minus", "4", "backspace", "backspace"), check)
    _tv(("3", "minus", "4"), lambda app: assert_eq("episode ▸ 3-4" in _status(app), True))


def test_a_bad_typed_value_is_named_and_changes_nothing():
    def check(app):
        assert_eq(app.emap.assign[0], (1, 1))
        assert "'0' is not an episode" in _status(app)

    _tv(("0", "enter"), check)


def test_moving_commits_the_pending_value_to_the_row_left():
    _tv(
        ("6", "down"),
        lambda app: (
            assert_eq(app.emap.assign[0], (6, 6)),
            assert_eq(app.query_one(DataTable).cursor_row, 1),
        ),
    )


def test_space_marks_a_run_and_one_number_fills_it_in_table_order():
    """Marked rows fill from the typed number in disc order; unmarked rows
    never change."""

    def check(app):
        assert_eq(app.emap.assign, {0: (7, 7), 1: (2, 2), 2: (8, 8), 3: None, 4: None})
        assert_eq(app.marked, set())

    # mark t0 and t2 (skipping t1), type 7, Enter
    _tv(("space", "down", "down", "space", "7", "enter"), check)


def test_marks_show_in_the_number_column_and_escape_clears_them():
    def check(app):
        assert_eq(str(app.query_one(DataTable).get_row_at(0)[0]), "* 0")

    _tv(("space",), check)
    _tv(("space", "escape"), lambda app: assert_eq(app.marked, set()))


def test_a_range_cannot_fill_a_marked_run():
    def check(app):
        assert_eq(app.emap.assign[0], (1, 1))
        assert "fills from one number" in _status(app)

    _tv(("space", "3", "minus", "4", "enter"), check)


def test_sub_floor_row_is_rescued_by_typing_an_episode():
    """A sub-floor title (bottom row, real number 4) prefills skip; typing an
    episode rescues it and it comes back in the accepted map."""
    _tv(
        ("down", "down", "down", "down", "4", "enter", "enter"),
        lambda app: assert_eq(app.return_value[4], (4, 4)),
    )


def test_sub_floor_row_is_rescued_as_extra_with_e():
    _tv(
        ("down", "down", "down", "down", "e", "enter"),
        lambda app: assert_eq(app.return_value[4], (0, 0)),
    )


def test_playall_is_rescued_by_typing_an_episode():
    _tv(
        ("down", "down", "down", "4", "enter", "enter"),
        lambda app: assert_eq(app.return_value[3], (4, 4)),
    )


def test_e_and_s_restate_a_row_as_extra_or_skip():
    _tv(("e",), lambda app: assert_eq(app.emap.assign[0], (0, 0)))
    _tv(("down", "s"), lambda app: assert_eq(app.emap.assign[1], None))


def test_a_clash_reddens_both_rows_names_them_and_blocks_enter():
    """Two titles on one episode: both rows render red with the other named,
    the status says Enter is refused, and Enter does not accept."""

    def check(app):
        assert_eq(sorted(app._blocks), [0, 1])
        table = app.query_one(DataTable)
        for row in (0, 1):
            cells = table.get_row_at(row)
            assert "✗ E02 clash with" in str(cells[5])
            assert all(
                any("red" in str(sp.style) for sp in c.spans) for c in cells
            )  # the whole row renders red
        assert "t1" in str(table.get_row_at(0)[5])
        assert "t0" in str(table.get_row_at(1)[5])
        assert "blocked" in _status(app) and "t0, t1" in _status(app)
        assert_eq(app.return_value, None)

    _tv(("2", "enter", "enter", "enter"), check)


def test_fixing_a_clash_unblocks_enter():
    _tv(
        ("2", "enter", "down", "9", "enter", "enter"),
        lambda app: assert_eq(app.return_value[1], (9, 9)),
    )


def test_staged_episode_renders_a_marker_and_blocks_a_claim():
    """Episodes already staged show in the status line; claiming one is a
    block (red row, named), Enter refused."""
    emap = _gk_map(staged={1})
    assert emap.assign[0] == (2, 2)  # the guess starts at the first unstaged

    def check(app):
        assert "already staged: E01" in _status(app)
        assert_eq(app._blocks, {0: ["✗ E01 already staged"]})
        assert_eq(app.return_value, None)

    _tv(("1", "enter", "enter"), check, emap=emap)


def test_claim_past_the_season_end_blocks_enter():
    def check(app):
        assert "past the season's last (E10)" in str(app.query_one(DataTable).get_row_at(0)[5])
        assert_eq(app.return_value, None)

    _tv(("9", "9", "enter", "enter"), check)


def test_unfilled_episode_warns_loudly_but_never_blocks():
    """E5 here leaves E4 inside the covered range unclaimed: the status
    warns, and Enter still accepts."""

    def check(app):
        assert "! unfilled: E04" in _status(app)
        assert_eq(app.return_value, None)

    _tv(("5", "enter"), check)
    _tv(("5", "enter", "enter"), lambda app: assert_eq(app.return_value[0], (5, 5)))


def test_the_ratio_audit_never_gates():
    """A far-off ratio grades yellow in the audit column; Enter still
    accepts. Rescue the 300s making-of onto E9 (a 3900s episode): 0.08."""
    rescue = ("down", "down", "down", "down", "9", "enter")
    _tv(rescue, lambda app: assert_eq(app.query_one(DataTable).get_row_at(4)[3].style, "yellow"))
    _tv(rescue + ("enter",), lambda app: assert_eq(app.return_value[4], (9, 9)))


def test_nothing_to_rip_refuses_enter():
    def check(app):
        assert "nothing to rip" in _status(app)
        assert_eq(app.return_value, None)

    _tv(("s", "down", "s", "down", "s", "enter"), check)


def test_quit_and_interrupt_exit_with_their_sentinels():
    _tv(("q",), lambda app: assert_eq(app.return_value, QUIT))
    _tv(("ctrl+c",), lambda app: assert_eq(app.return_value, INTERRUPT))


def test_c_lists_the_staged_files_and_deletes_only_on_explicit_y(tmp_path):
    a = tmp_path / "Show_S01E01.mkv"
    b = tmp_path / "Show_S01E02-E03.mkv"
    a.touch()
    b.touch()

    def listed(app):
        # the modal is up: it names both files; nothing deleted yet
        text = " ".join(str(w.render()) for w in app.screen.query("Static"))
        assert "Show_S01E01.mkv" in text and "Show_S01E02-E03.mkv" in text
        assert a.exists() and b.exists()

    _tv(("c",), listed, staging=[a, b])

    _tv(
        ("c", "y"),
        lambda app: assert_eq(
            (a.exists(), b.exists(), "deleted 2" in _status(app)), (False, False, True)
        ),
        staging=[a, b],
    )


def test_c_declined_keeps_files_and_enter_in_the_modal_never_deletes_or_accepts(tmp_path):
    a = tmp_path / "Show_S01E01.mkv"
    a.touch()
    _tv(("c", "n"), lambda app: assert_eq(a.exists(), True), staging=[a])
    _tv(("c", "escape"), lambda app: assert_eq(a.exists(), True), staging=[a])

    def check(app):
        assert_eq(a.exists(), True)
        assert_eq(app.return_value, None)  # Enter / q did nothing under the modal

    _tv(("c", "enter", "q"), check, staging=[a])


def test_c_with_nothing_staged_says_so():
    _tv(("c",), lambda app: assert_eq("nothing staged for this show" in _status(app), True))


def test_clearing_staged_files_refreshes_the_staged_set_and_unblocks(tmp_path):
    """After c+y the staged set the table grades against is the callback's
    refreshed one: a claim on a just-cleared episode stops blocking."""
    f = tmp_path / "Show_S01E01.mkv"
    f.touch()
    emap = _gk_map(staged={1})

    def check(app):
        assert_eq(app._blocks, {})
        assert_eq(app.emap.staged, set())
        assert_eq(app.emap.assign[0], (1, 1))

    _tv(("1", "enter", "c", "y"), check, emap=emap, staging=[f])


def test_accepted_echo_prints_the_episode_table_plain(monkeypatch):
    out = io.StringIO()
    con = Console(
        file=out, width=80, theme=THEME, highlight=False, emoji=False, force_terminal=False
    )
    monkeypatch.setattr(ui, "console", con)
    emap = _gk_map()
    print_accepted_episodes("Generation Kill (2008)", emap, dict(emap.assign))
    text = out.getvalue()
    assert "\x1b[" not in text
    assert "Generation Kill (2008) — season 01" in text
    for col in COLUMNS:
        assert col in text
    assert "E01" in text and "≈ sum of 0, 1, 2" in text and "below the TV floor" in text


# --- CD: the track table (slice 05) ------------------------------------------

from skive.selection import RIP_ACTION, CdTable, CdTrack, cd_audit_cell, print_accepted_cd

CD_TRACKS = [CdTrack(1, 200), CdTrack(2, 300)]


_NO_MB = object()  # sentinel distinct from None — None means "resolve failed"


def _cd(keys, check, tracks=CD_TRACKS, mb=_NO_MB, size=(80, 24)):
    if mb is _NO_MB:
        mb = {1: 200, 2: 300}

    async def go():
        app = CdTable("CD · offset 6", tracks, mb)
        async with app.run_test(size=size) as pilot:
            if keys:
                await pilot.press(*keys)
                await pilot.pause()
            check(app)

    asyncio.run(go())


def test_cd_prefills_all_rip():
    """Every track prefills rip; the audit cell grades TOC ÷ MB (green at
    1.00, yellow uniformly off, blank without a resolved duration)."""
    assert_eq(cd_audit_cell(200, 200).plain, "1.00")
    assert_eq(cd_audit_cell(200, 200).style, "green")
    assert_eq(cd_audit_cell(200, 150).style, "yellow")
    assert_eq(cd_audit_cell(200, None).plain, "")

    def check(app):
        assert_eq(app.actions, {1: RIP_ACTION, 2: RIP_ACTION})
        cells = app._cells(CD_TRACKS[1])
        assert_eq(cells[1].plain, "0:05:00")
        assert_eq(cells[2].plain, "")  # size blank for CDs
        assert_eq(cells[3].plain, "1.00")
        assert_eq(cells[4].plain, "rip")

    _cd(None, check)


def test_cd_blank_audit_when_the_resolve_failed():
    def check(app):
        assert_eq(app._cells(CD_TRACKS[0])[3].plain, "")

    _cd(None, check, mb=None)


def test_cd_skip_and_restore():
    def check(app):
        assert_eq(app.actions, {1: "skip", 2: RIP_ACTION})
        assert_eq(app._cells(CD_TRACKS[0])[4].plain, "skip")

    _cd(("s",), check)


def test_cd_restore_returns_the_track_to_rip():
    def check(app):
        assert_eq(app.actions, {1: RIP_ACTION, 2: RIP_ACTION})

    _cd(("s", "down", "r", "up", "r"), check)


def test_cd_accept_returns_the_action_map():
    def check(app):
        pass

    result = {}

    def capture(app):
        result["actions"] = app.actions

    _cd(("s", "down", "enter"), capture)
    assert_eq(result["actions"], {1: "skip", 2: RIP_ACTION})


def test_cd_refuses_to_accept_all_skip():
    def check(app):
        # still open: the hint names the refusal, no exit happened
        assert_eq(app.return_value, None)  # Enter refused, still open
        assert_eq(app.actions, {1: "skip", 2: "skip"})

    _cd(("s", "down", "s", "enter"), check)


def test_cd_quit_exits_with_nothing_ripped():
    """`q` exits with QUIT — the caller rips nothing."""

    async def go():
        app = CdTable("CD · offset 6", CD_TRACKS, {1: 200, 2: 300})
        async with app.run_test() as pilot:
            await pilot.press("q")
            await pilot.pause()
            return app.return_value

    assert_eq(asyncio.run(go()), QUIT)


def test_cd_accepted_echo_prints_plain(monkeypatch):
    out = io.StringIO()
    con = Console(
        file=out, width=80, theme=THEME, highlight=False, emoji=False, force_terminal=False
    )
    monkeypatch.setattr(ui, "console", con)
    print_accepted_cd("CD · offset 6", CD_TRACKS, {1: "skip", 2: RIP_ACTION}, {1: 200, 2: 300})
    text = out.getvalue()
    assert "\x1b[" not in text
    assert "CD · offset 6" in text
    for col in COLUMNS:
        assert col in text
    assert "0:03:20" in text and "1.00" in text and "skip" in text
