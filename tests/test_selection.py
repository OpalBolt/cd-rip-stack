"""Headless Pilot tests for the selection table (ADR 0020, slice 03): cursor
movement, the three verbs, several-near prefill, exactly-one-main, accept,
quit and interrupt — plus render checks at 80 columns and a narrow width, in
color and NO_COLOR."""

import asyncio
import io

import pytest
from rich.console import Console
from textual.widgets import DataTable
from rich.theme import Theme

from skive import ui
from skive.scan import TitleInfo
from skive.selection import (
    COLUMNS, EXTRA_ACTION, INTERRUPT, QUIT, SKIP_ACTION, SelectionTable,
    graded_rows, prefill, print_accepted, rip_set_line,
)
from skive.ui import THEME
from skive.validation import MAIN_ACTION

# The Mamma Mia disc: two feature-length titles near the 6480s runtime,
# plus a short. 6252/6480 = 0.96 green; 3000/6480 = 0.46 yellow.
TITLES = [TitleInfo(21, 6252, 4_300_000_000, "dan", "", False),
          TitleInfo(12, 6662, 19_000_000_000, "dan", "", False),
          TitleInfo(3, 300, 500_000_000, "dan", "", False)]
YELLOW_TITLES = [TitleInfo(1, 6300, 5_000_000_000, "en", "", False),
                 TitleInfo(2, 3000, 6_000_000_000, "en", "", False)]
SHORTS = [TitleInfo(1, 600, 100_000_000, "en", "", False),
          TitleInfo(2, 900, 200_000_000, "en", "", False)]


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
    actions, notes = prefill([TitleInfo(5, 6400, 8_000_000_000, "en", "", False),
                              TitleInfo(2, 600, 100_000_000, "en", "", False)], 6480)
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
    assert rip_set_line({21: MAIN_ACTION, 3: EXTRA_ACTION}) == \
        "rip set: t21 + extras t3 (extras marked for encoding)"


# --- Pilot: keys over the real component -------------------------------------

def test_cursor_moves_with_arrows_and_jk():
    _scenario(("down", "j", "k"),
              lambda app: assert_eq(app.query_one(DataTable).cursor_row, 1))


def test_verbs_restate_the_row_action():
    """e marks an extra, s skips — at the cursor."""
    # rows render in title order: row 2 is t21, the biggest-bytes title
    _scenario(("down", "down", "e"),
              lambda app: assert_eq(app.actions[21], EXTRA_ACTION))
    _scenario(("down", "down", "s"),
              lambda app: assert_eq(app.actions[21], SKIP_ACTION))


def test_m_moves_the_single_main():
    """m at the short row moves the main off t12 onto t3."""
    _scenario(("down", "down", "m"), lambda app: assert_eq(app.actions, {
        21: MAIN_ACTION, 12: SKIP_ACTION, 3: SKIP_ACTION}))


def test_verbs_never_demote_the_main():
    """e/s on the main row are no-ops — exactly one main always holds."""
    _scenario(("e", "s"), lambda app: (
        assert_eq(sum(1 for a in app.actions.values() if a == MAIN_ACTION), 1),
        assert_eq(app.actions[12], MAIN_ACTION)))


def test_accept_returns_the_action_map():
    _scenario(("down", "down", "e", "enter"), lambda app: assert_eq(
        app.return_value, {21: EXTRA_ACTION, 12: MAIN_ACTION, 3: SKIP_ACTION}))


def test_yellow_rows_never_block_enter():
    """A far-off ratio grades yellow in the audit column — Enter still rips."""
    _scenario(("enter",), lambda app: (
        assert_isinstance(app.return_value, dict),
        assert_eq(app.return_value[1], MAIN_ACTION)),
        titles=YELLOW_TITLES)


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
        assert_eq([str(c) for c in table.get_row_at(0)[:3]],
                  ["3", "0:05:00", "476.8 MiB"])  # the short
        assert_eq(str(table.get_row_at(0)[4]), SKIP_ACTION)
        assert_eq(str(table.get_row_at(1)[4]), MAIN_ACTION)
        assert_eq([str(c) for c in table.get_row_at(2)[:3]],
                  ["21", "1:44:12", "4.0 GiB"])
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
    _scenario((), lambda app: assert_eq(str(app.query_one(DataTable)
                                           .get_row_at(0)[3]), ""),
              titles=TITLES, runtime_s=None)  # unknown runtime: blank
    _scenario((), lambda app: assert_eq(str(app.query_one(DataTable)
                                           .get_row_at(0)[3]), ""))  # the short


def test_prefill_note_lands_in_the_notes_cell():
    _scenario((), lambda app: (
        assert_eq("near the runtime" in str(app.query_one(DataTable)
                                           .get_row_at(1)[5]), True),
        assert_eq(str(app.query_one(DataTable).get_row_at(0)[5]), "")))


def test_render_in_color_and_no_color():
    """The component renders the same cell text headless with or without the
    NO_COLOR environment set."""
    def check(app):
        table = app.query_one(DataTable)
        assert_eq([str(c) for c in table.get_row_at(1)], [
            "12", "1:51:02", "17.7 GiB", "1.03", MAIN_ACTION,
            "2 titles near the runtime"])
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
    con = Console(file=out, width=width, theme=THEME, highlight=False,
                  emoji=False, force_terminal=color,
                  color_system="truecolor" if color and not no_color else None)
    monkeypatch.setattr(ui, "console", con)
    print_accepted("Mamma Mia! (2008)", TITLES,
                   {21: MAIN_ACTION, 3: EXTRA_ACTION, 12: SKIP_ACTION}, 6480)
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
