"""The selection table (ADR 0020): one transient Textual app over the disc
scan's titles, launched inside the run view's suspend — six universal columns
(`#` · length · size · audit · action · notes), prefilled by main
arbitration. The operator moves with ↑/↓ or j/k and restates the action at
the cursor: `m` moves the single main, `e` marks an extra, `s` skips; Enter
accepts, `q` quits with nothing ripped, Ctrl+C follows the repo Interrupt.
Movie verb config only (KISS): TV and CD verb configs come later.

The component is a face, not a brain: the prefill and the graded rows are
pure functions here, the exactly-one-main rule stays in skive.validation."""

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Static

from skive import ui
from skive.makemkv import hsize
from skive.scan import TitleInfo
from skive.validation import (
    MAIN_ACTION, MAIN_FLOOR_S, arbitrate_main, biggest_title, exactly_one_main,
    grade_main, main_candidates,
)

QUIT = "quit"
"""The app's exit value when the operator quits — nothing ripped (exit 1)."""

INTERRUPT = "interrupt"
"""The app's exit value on Ctrl+C — the caller re-raises after Textual has
restored the terminal, so the flow's interrupt handlers fire (exit 130)."""

EXTRA_ACTION = "extra"
SKIP_ACTION = "skip"
"""The movie verbs beside skive.validation.MAIN_ACTION."""

COLUMNS = ("#", "length", "size", "audit", "action", "notes")
"""The six universal columns (ADR 0020 decision 2) — identical on every
medium once TV and CD ride the same component."""

ACTION_STYLE = {MAIN_ACTION: "bold green", EXTRA_ACTION: "cyan", SKIP_ACTION: "dim"}


def hms(secs: int) -> str:
    """Duration as H:MM:SS (the table's length column and the post-rip
    report share one format)."""
    return f"{secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def _note(log, phase: str, text: str) -> None:
    if log is not None:
        log.note(phase, text)


def prefill(titles: list[TitleInfo], runtime_s: int | None,
            log=None) -> tuple[dict[int, str], dict[int, str]]:
    """The audited prefill: main arbitration picks the main, everything else
    skips. Several candidates near the runtime prefill biggest with the
    ambiguity named in the main row's notes; zero or unknown prefills
    biggest silently; no candidate at all (shorts, bonus-shaped discs)
    prefills biggest too so exactly one main always holds. Every outcome
    lands in the run log under phase "main".

    Returns:
        (actions, notes) — title number -> verb, and the notes cell text by
        title number (ambiguity facts only)
    """
    ordered = sorted(titles, key=lambda t: t.number)
    triples = [(t.number, t.seconds, t.size) for t in ordered]
    actions = {n: SKIP_ACTION for n, _s, _size in triples}
    notes: dict[int, str] = {}

    if not triples:
        _note(log, "main", "main=none — no candidate main on the disc")
        return actions, notes

    arb = arbitrate_main(main_candidates(triples), runtime_s)
    if arb.pick is None:
        main_number = biggest_title(triples)[0]
        notes[main_number] = "biggest bytes — no candidate main"
        _note(log, "main",
              f"main=t{main_number} biggest bytes — no candidate main on the disc")
    else:
        main_number = arb.pick[0]
        how = {"sole": "near the runtime — automatic",
               "zero": "biggest bytes — nothing near the runtime",
               "unknown": "biggest bytes — runtime unknown",
               "several": "default (biggest bytes)"}[arb.kind]
        _note(log, "main",
              f"main=t{main_number} {how}"
              + (f" — {len(arb.near)} titles near the runtime"
                 if arb.kind == "several" else ""))
        if arb.note is not None:
            notes[main_number] = arb.note

    actions[main_number] = MAIN_ACTION
    return actions, notes


def graded_rows(titles: list[TitleInfo], runtime_s: int | None) -> list[dict]:
    """The pre-rip rows for the validation record: {"t", "dur_s", "ratio",
    "band"} per scanned title in title order — ratio/band None where never
    graded (unknown runtime, sub-floor title). post_rip_validation sees no
    change from the old gate's loop."""
    rows = []
    for t in sorted(titles, key=lambda t: t.number):
        ratio = band = None
        if t.seconds >= MAIN_FLOOR_S:
            graded = grade_main(t.seconds, runtime_s)
            if graded is not None:
                ratio, band = graded
        rows.append({"t": t.number, "dur_s": t.seconds,
                     "ratio": ratio, "band": band})
    return rows


def rip_set_line(actions: dict[int, str]) -> str:
    """The rip-set info line printed under the accepted table: the main
    alone, or the main plus the accepted extras (extras marked for
    encoding). Exactly one main always holds, so the main is always here."""
    main_number = next(n for n, a in actions.items() if a == MAIN_ACTION)
    extras = sorted(n for n, a in actions.items() if a == EXTRA_ACTION)
    if not extras:
        return f"rip set: t{main_number} only"
    return (f"rip set: t{main_number} + extras "
            + ", ".join(f"t{n}" for n in extras) + " (extras marked for encoding)")


def audit_cell(t: TitleInfo, runtime_s: int | None) -> Text:
    """The audit cell: the exact ratio graded green/yellow for candidates at
    or above MAIN_FLOOR_S; blank (never yellow) when the runtime is unknown
    or the title is sub-floor. The audit never blocks Enter."""
    if t.seconds >= MAIN_FLOOR_S:
        graded = grade_main(t.seconds, runtime_s)
        if graded is not None:
            return Text(f"{graded[0]:.2f}", style=graded[1])
    return Text("")


def _row_cells(t: TitleInfo, actions: dict[int, str],
               notes: dict[int, str], runtime_s: int | None) -> tuple:
    action = actions.get(t.number, SKIP_ACTION)
    return (Text(str(t.number), style="bold"),
            Text(hms(t.seconds)),
            Text(hsize(t.size) if t.size else ""),
            audit_cell(t, runtime_s),
            Text(action, style=ACTION_STYLE.get(action, "")),
            Text(notes.get(t.number, "")))


def print_accepted(display: str, titles: list[TitleInfo],
                   actions: dict[int, str], runtime_s: int | None) -> None:
    """Echo the accepted table as plain lines into scrollback, before the
    run opener — the evidence trail for an interrupted run. Built through
    ui.table, so it is pipe-safe and NO_COLOR-safe by construction."""
    notes = prefill(titles, runtime_s)[1]
    ui.console.print(f"\n{display}")
    table = ui.table(*COLUMNS)
    for t in sorted(titles, key=lambda t: t.number):
        table.add_row(*_row_cells(t, actions, notes, runtime_s))
    ui.console.print(table)


class SelectionTable(App):
    """The movie selection table: rows over the scan, cursor edits, accept.

    Keys: ↑/↓ or j/k move the cursor; `m` moves the single main (exactly one
    main always holds), `e` marks an extra, `s` skips — never on the main
    row, whose action can only be moved with `m`. Enter accepts and returns
    the title→action map; `q` exits with QUIT (nothing ripped); Ctrl+C exits
    with INTERRUPT (Textual restores the terminal on every exit path)."""

    CSS = """
    #disc { height: 1; text-style: bold; }
    DataTable { height: 1fr; }
    #hint { height: 1; color: $text-muted; }
    """

    BINDINGS = [
        Binding("up,k", "move(-1)", show=False),
        Binding("down,j", "move(1)", show=False),
        Binding("m", "main", "main"),
        Binding("e", "verb('extra')", "extra"),
        Binding("s", "verb('skip')", "skip"),
        Binding("enter", "accept", "accept", priority=True),
        Binding("q", "quit_table", "quit", priority=True),
        Binding("ctrl+c", "interrupt", "interrupt", priority=True),
    ]

    def __init__(self, display: str, titles: list[TitleInfo],
                 runtime_s: int | None, log=None):
        super().__init__()
        self.display_name = display
        self.titles = sorted(titles, key=lambda t: t.number)
        self.runtime_s = runtime_s
        self.run_log = log
        self.actions, self.notes = prefill(self.titles, runtime_s, self.run_log)

    def compose(self) -> ComposeResult:
        yield Static(self.display_name, id="disc")
        yield DataTable(cursor_type="row")
        yield Static("↑/↓ j/k move · m main · e extra · s skip · Enter accept · q quit",
                     id="hint")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(*COLUMNS)
        self._col_keys = list(table.columns.keys())
        self._row_keys: dict[int, object] = {}
        for t in self.titles:
            key = table.add_row(*_row_cells(t, self.actions, self.notes,
                                            self.runtime_s),
                                key=str(t.number))
            self._row_keys[t.number] = key

    def _cursor_title(self) -> TitleInfo:
        table = self.query_one(DataTable)
        return self.titles[table.cursor_row]

    def _refresh_row(self, number: int) -> None:
        t = next(t for t in self.titles if t.number == number)
        table = self.query_one(DataTable)
        cells = _row_cells(t, self.actions, self.notes, self.runtime_s)
        for col_key, cell in zip(self._col_keys, cells):
            table.update_cell(self._row_keys[number], col_key, cell)

    def action_move(self, delta: int) -> None:
        table = self.query_one(DataTable)
        if delta < 0:
            table.action_cursor_up()
        else:
            table.action_cursor_down()

    def action_main(self) -> None:
        number = self._cursor_title().number
        for k, a in list(self.actions.items()):
            if a == MAIN_ACTION:
                self.actions[k] = SKIP_ACTION
                self._refresh_row(k)
        self.actions[number] = MAIN_ACTION
        self._refresh_row(number)

    def action_verb(self, verb: str) -> None:
        number = self._cursor_title().number
        if self.actions.get(number) == MAIN_ACTION:
            return  # exactly one main always holds — move it with m
        self.actions[number] = verb
        self._refresh_row(number)

    def action_accept(self) -> None:
        if exactly_one_main(self.actions) is not None:
            # Unreachable through the keys (m always moves the single main);
            # refuse to rip rather than break the invariant.
            self.query_one("#hint", Static).update("exactly one main required")
            return
        self.exit(dict(self.actions))

    def action_quit_table(self) -> None:
        self.exit(QUIT)

    def action_interrupt(self) -> None:
        self.exit(INTERRUPT)


def run_selection_table(log, display: str, titles: list[TitleInfo],
                        runtime_s: int | None) -> dict[int, str] | str:
    """Run the table over the scan and return the accepted title→action map,
    QUIT (the caller exits 1 with nothing ripped) or INTERRUPT (the caller
    re-raises KeyboardInterrupt — exit 130 — once Textual has restored the
    terminal)."""
    return SelectionTable(display, titles, runtime_s, log).run()
