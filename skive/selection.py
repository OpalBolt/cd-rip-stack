"""The selection table (ADR 0020): one transient Textual app over the disc
scan's titles, launched inside the run view's suspend — six universal columns
(`#` · length · size · audit · action · notes), prefilled by the audits. The
operator moves with ↑/↓ or j/k and restates the action at the cursor; Enter
accepts, `q` quits with nothing ripped, Ctrl+C follows the repo Interrupt.

Two verb configs ride the one component: the movie table (`m` moves the
single main, `e` extra, `s` skip) and the TV episode table (typed episode
numbers `3` / `3-4`, Space-marked runs filled in order from one typed number,
`e` extra, `s` skip, `c` clears the show's staged files). The component is a
face, not a brain: the prefill, the graded rows and the episode map's
violations are pure functions/classes here, the exactly-one-main rule stays
in skive.validation and the episode math in skive.tv_match."""

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar, Protocol

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Static

from skive import ui
from skive.makemkv import hsize
from skive.scan import TitleInfo
from skive.tv_match import (
    audit_cell as episode_audit_cell,
)
from skive.tv_match import (
    episode_clashes,
    past_end_claims,
    prefill_assignment,
    staged_claims,
    sub_floor_titles,
    tv_floor,
    unfilled_episodes,
)
from skive.validation import (
    MAIN_ACTION,
    MAIN_FLOOR_S,
    arbitrate_main,
    biggest_title,
    exactly_one_main,
    grade_main,
    main_candidates,
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


def prefill(
    titles: list[TitleInfo], runtime_s: int | None, log=None
) -> tuple[dict[int, str], dict[int, str]]:
    """The audited prefill: main arbitration picks the main, everything else
    skips. Several candidates near the runtime prefill biggest with the
    ambiguity named in the main row's notes; zero or unknown prefills
    biggest silently; no candidate at all (shorts, bonus-shaped discs)
    prefills biggest too so exactly one main always holds. Decoy-cluster
    members and scrambled titles never enter the candidate pool — their rows
    carry the decoy fact in the notes cell — and an all-decoy disc still
    prefills the biggest title overall as its one main, the fact stated on
    row and log. Every outcome
    lands in the run log under phase "main".

    Returns:
        (actions, notes) — title number -> verb, and the notes cell text by
        title number (ambiguity and decoy facts only)
    """
    ordered = sorted(titles, key=lambda t: t.number)
    triples = [(t.number, t.seconds, t.size) for t in ordered]
    actions = {n: SKIP_ACTION for n, _s, _size in triples}
    notes: dict[int, str] = {}
    decoys = [t for t in ordered if t.decoy_cluster or t.scrambled]
    for t in decoys:
        notes[t.number] = f"decoy cluster ×{t.decoy_cluster}" if t.decoy_cluster else "shuffled map"
    skip_note = f" — skipped {len(decoys)} decoys" if decoys else ""

    if not triples:
        _note(log, "main", "main=none — no candidate main on the disc")
        return actions, notes

    clean = [
        (n, s, size)
        for t, (n, s, size) in zip(ordered, triples)
        if not t.decoy_cluster and not t.scrambled
    ]
    arb = arbitrate_main(main_candidates(clean), runtime_s)
    if arb.pick is None and not decoys:
        assert triples  # a disc that reached arbitration has titles
        biggest = biggest_title(triples)
        assert biggest is not None  # triples is non-empty
        main_number = biggest[0]
        notes[main_number] = "biggest bytes — no candidate main"
        _note(log, "main", f"main=t{main_number} biggest bytes — no candidate main on the disc")
    elif arb.pick is None:
        assert clean or triples  # a disc that reached arbitration has titles
        biggest = biggest_title(clean or triples)
        assert biggest is not None  # non-empty input
        main_number = biggest[0]
        if clean:
            # Clean sub-floor titles exist (the shorts flavor): the decoy
            # cluster fact would be false on this main's row.
            why = "biggest bytes — no candidate main"
            notes[main_number] = why
            _note(log, "main", f"main=t{main_number} {why} on the disc" + skip_note)
        else:
            why = "biggest bytes — every title decoy-clustered"
            notes[main_number] = (
                notes.get(main_number, "") + " " if notes.get(main_number) else ""
            ) + why
            _note(log, "main", f"main=t{main_number} {why}" + skip_note)
    else:
        main_number = arb.pick[0]
        how = {
            "sole": "near the runtime — automatic",
            "zero": "biggest bytes — nothing near the runtime",
            "unknown": "biggest bytes — runtime unknown",
            "several": "default (biggest bytes)",
        }[arb.kind]
        _note(
            log,
            "main",
            f"main=t{main_number} {how}"
            + (f" — {len(arb.near)} titles near the runtime" if arb.kind == "several" else "")
            + skip_note,
        )
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
        rows.append({"t": t.number, "dur_s": t.seconds, "ratio": ratio, "band": band})
    return rows


def rip_set_line(actions: dict[int, str]) -> str:
    """The rip-set info line printed under the accepted table: the main
    alone, or the main plus the accepted extras (extras marked for
    encoding). Exactly one main always holds, so the main is always here."""
    main_number = next(n for n, a in actions.items() if a == MAIN_ACTION)
    extras = sorted(n for n, a in actions.items() if a == EXTRA_ACTION)
    if not extras:
        return f"rip set: t{main_number} only"
    return (
        f"rip set: t{main_number} + extras "
        + ", ".join(f"t{n}" for n in extras)
        + " (extras marked for encoding)"
    )


def audit_cell(t: TitleInfo, runtime_s: int | None) -> Text:
    """The audit cell: the exact ratio graded green/yellow for candidates at
    or above MAIN_FLOOR_S; blank (never yellow) when the runtime is unknown
    or the title is sub-floor. The audit never blocks Enter."""
    if t.seconds >= MAIN_FLOOR_S:
        graded = grade_main(t.seconds, runtime_s)
        if graded is not None:
            return Text(f"{graded[0]:.2f}", style=graded[1])
    return Text("")


def _row_cells(
    t: TitleInfo, actions: dict[int, str], notes: dict[int, str], runtime_s: int | None
) -> tuple:
    action = actions.get(t.number, SKIP_ACTION)
    return (
        Text(str(t.number), style="bold"),
        Text(hms(t.seconds)),
        Text(hsize(t.size) if t.size else ""),
        audit_cell(t, runtime_s),
        Text(action, style=ACTION_STYLE.get(action, "")),
        Text(notes.get(t.number, "")),
    )


def print_accepted(
    display: str, titles: list[TitleInfo], actions: dict[int, str], runtime_s: int | None
) -> None:
    """Echo the accepted table as plain lines into scrollback, before the
    run opener — the evidence trail for an interrupted run. Built through
    ui.table, so it is pipe-safe and NO_COLOR-safe by construction."""
    notes = prefill(titles, runtime_s)[1]
    ui.console.print(f"\n{display}")
    table = ui.table(*COLUMNS)
    for t in sorted(titles, key=lambda t: t.number):
        table.add_row(*_row_cells(t, actions, notes, runtime_s))
    ui.console.print(table)


class _Numbered(Protocol):
    """The row protocol every table kind satisfies: a numbered row."""

    number: int


class _Table[T: _Numbered](App):
    """The shared table shell: rows over the scan, cursor movement, accept,
    quit, interrupt. A verb config subclasses it with its own cells and
    actions; Enter accepts (`action_accept`), `q` exits with QUIT (nothing
    ripped), Ctrl+C with INTERRUPT (Textual restores the terminal on every
    exit path)."""

    CSS = """
    #disc { height: 1; text-style: bold; }
    DataTable { height: 1fr; }
    #status { height: auto; }
    #hint { height: auto; color: $text-muted; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("up,k", "move(-1)", show=False, priority=True),
        Binding("down,j", "move(1)", show=False, priority=True),
        Binding("enter", "accept", "accept", priority=True),
        Binding("q", "quit_table", "quit", priority=True),
        Binding("ctrl+c", "interrupt", "interrupt", priority=True),
    ]

    HINT = ""
    COLUMNS: ClassVar[tuple[str, ...]] = COLUMNS
    """The row columns; the boundary table overrides with its own set (the
    component is shared, the columns are per-consumer)."""

    def __init__(self, display: str, titles: list[T]):
        super().__init__()
        self.display_name = display
        self.titles: list[T] = list(titles)

    def _cells(self, t: T) -> tuple:
        raise NotImplementedError

    def _status_widgets(self) -> list:
        return []

    def compose(self) -> ComposeResult:
        yield Static(self.display_name, id="disc")
        yield DataTable(cursor_type="row")
        yield from self._status_widgets()
        yield Static(self.HINT, id="hint")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(*self.COLUMNS)
        self._col_keys = list(table.columns.keys())
        self._row_keys: dict[int, object] = {}
        for t in self.titles:
            self._row_keys[t.number] = table.add_row(*self._cells(t), key=str(t.number))

    def _cursor_title(self) -> TitleInfo:
        return self.titles[self.query_one(DataTable).cursor_row]

    def _refresh_row(self, number: int) -> None:
        t = next(t for t in self.titles if t.number == number)
        table = self.query_one(DataTable)
        for col_key, cell in zip(self._col_keys, self._cells(t)):
            table.update_cell(self._row_keys[number], col_key, cell, update_width=True)

    def action_move(self, delta: int) -> None:
        table = self.query_one(DataTable)
        if delta < 0:
            table.action_cursor_up()
        else:
            table.action_cursor_down()

    def action_accept(self) -> None:
        raise NotImplementedError

    def action_quit_table(self) -> None:
        self.exit(QUIT)

    def action_interrupt(self) -> None:
        self.exit(INTERRUPT)


class SelectionTable(_Table):
    """The movie selection table: rows over the scan, cursor edits, accept.

    Keys: ↑/↓ or j/k move the cursor; `m` moves the single main (exactly one
    main always holds), `e` marks an extra, `s` skips — never on the main
    row, whose action can only be moved with `m`. Enter accepts and returns
    the title→action map."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("m", "main", "main"),
        Binding("e", "verb('extra')", "extra"),
        Binding("s", "verb('skip')", "skip"),
    ]

    HINT = "↑/↓ j/k move · m main · e extra · s skip · Enter accept · q quit"

    def __init__(self, display: str, titles: list[TitleInfo], runtime_s: int | None, log=None):
        super().__init__(display, sorted(titles, key=lambda t: t.number))
        self.runtime_s = runtime_s
        self.run_log = log
        self.actions, self.notes = prefill(self.titles, runtime_s, self.run_log)

    def _cells(self, t: TitleInfo) -> tuple:
        return _row_cells(t, self.actions, self.notes, self.runtime_s)

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


def run_selection_table(
    log, display: str, titles: list[TitleInfo], runtime_s: int | None
) -> dict[int, str] | str:
    """Run the table over the scan and return the accepted title→action map,
    QUIT (the caller exits 1 with nothing ripped) or INTERRUPT (the caller
    re-raises KeyboardInterrupt — exit 130 — once Textual has restored the
    terminal)."""
    return SelectionTable(display, titles, runtime_s, log).run()


# --- TV: the episode map and its table ---------------------------------------

EpisodeAction = tuple[int, int] | None
"""One title's TV action: an episode (lo, hi) — a double when hi > lo —,
(0, 0) for an extra, or None for skip (not ripped)."""

EXTRA_EPISODES = (0, 0)


def parse_episode(text: str) -> tuple[int, int] | None:
    """A typed episode value: `3` -> (3, 3), `3-4` -> (3, 4). None for
    anything else (zero, an inverted or half-typed range)."""
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", text.strip())
    if m is None:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    return (lo, hi) if lo >= 1 and hi >= lo else None


@dataclass
class EpisodeMap:
    """The TV table's state, free of any terminal: every scanned title's
    action, the TMDB facts the rows grade against, and the episodes already
    staged. Violations and warnings are derived from the actions — never
    stored — so every edit re-grades the whole map."""

    season: int
    titles: list[TitleInfo]  # table order: scan order, sub-floor titles last
    assign: dict[int, EpisodeAction]
    evidence: dict[int, str]  # static notes: play-all sums, the sub-floor label
    ep_names: dict[int, str]
    ep_runtimes: dict[int, int]
    staged: set[int]

    @property
    def max_ep(self) -> int:
        return max(self.ep_names, default=0)

    def _episodes(self) -> dict[int, tuple[int, int]]:
        return {t: r for t, r in self.assign.items() if r is not None and r[0] >= 1}

    def blocks(self) -> dict[int, list[str]]:
        """Title -> its blocking reasons (Enter refuses while any exist):
        episode clashes, claims on staged episodes, episodes past the
        season's end."""
        eps = self._episodes()
        out: dict[int, list[str]] = {}
        for e, ts in episode_clashes(eps):
            for t in ts:
                others = ", ".join(f"t{o}" for o in ts if o != t)
                out.setdefault(t, []).append(f"✗ E{e:02d} clash with {others}")
        for e, t in staged_claims(eps, self.staged):
            out.setdefault(t, []).append(f"✗ E{e:02d} already staged")
        for e, t in past_end_claims(eps, self.max_ep):
            out.setdefault(t, []).append(f"✗ E{e:02d} past the season's last (E{self.max_ep:02d})")
        return out

    def unfilled(self) -> list[int]:
        """Episodes inside the covered range nothing claims and nothing
        staged covers — a warning, never a block."""
        eps = self._episodes()
        if not eps:
            return []
        return [
            e
            for e in unfilled_episodes(
                min(r[0] for r in eps.values()), max(r[1] for r in eps.values()), eps
            )
            if e not in self.staged
        ]

    def cells(self, t: TitleInfo, blocks: dict[int, list[str]], marked: bool = False) -> tuple:
        """One row's six cells; a blocked row renders red whole, its reasons
        named in the notes cell."""
        n = t.number
        lohi = self.assign[n]
        if lohi is None:
            action, style = "skip", ACTION_STYLE[SKIP_ACTION]
        elif lohi == EXTRA_EPISODES:
            action, style = "extra", ACTION_STYLE[EXTRA_ACTION]
        else:
            action = f"E{lohi[0]:02d}" + (f"-E{lohi[1]:02d}" if lohi[1] > lohi[0] else "")
            style = "bold green"
        audit = episode_audit_cell(t.seconds, *lohi, self.ep_runtimes) if lohi is not None else None
        parts = list(blocks.get(n, []))
        if lohi is not None and lohi[0] >= 1 and self.ep_names.get(lohi[0]):
            parts.append(self.ep_names[lohi[0]])
        if n in self.evidence:
            parts.append(self.evidence[n])
        row = (
            Text(("* " if marked else "") + str(n), style="bold"),
            Text(hms(t.seconds)),
            Text(hsize(t.size) if t.size else ""),
            Text(audit[0], style=audit[1]) if audit else Text(""),
            Text(action, style=style),
            Text(" · ".join(parts)),
        )
        if n in blocks:
            for cell in row:
                cell.stylize("red")
        return row


def decoy_numbers(titles: list[TitleInfo]) -> set[int]:
    """Title numbers carrying a decoy fact: decoy-cluster members and
    scrambled-map titles. They never enter the episode guess's pool and
    prefill skip with the decoy note — but a row assigned an episode at the
    table still rips."""
    return {t.number for t in titles if t.decoy_cluster or t.scrambled}


def episode_map(
    season: int,
    titles: list[TitleInfo],
    minlength: int,
    playalls: dict[int, tuple[str, list[int]]],
    ep_names: dict[int, str],
    ep_runtimes: dict[int, int],
    staged: set[int],
) -> EpisodeMap:
    """The audited prefill as an EpisodeMap: the episode guess walks from the
    first unstaged episode over the eligible titles; play-alls prefill skip
    with their `≈ sum of …` evidence; sub-floor titles list last with their
    real disc numbers, prefilled skip.

    Args:
        season: Season number the disc belongs to
        titles: Every scanned title, scan order
        minlength: Minimum title length in seconds the scan used
        playalls: sum_disqualified_windowed over the floored titles —
            title -> (tier, component titles)
        ep_names: episode_number -> name from TMDB
        ep_runtimes: episode_number -> TMDB runtime seconds
        staged: Episode numbers already staged
    """
    floor = tv_floor(minlength)
    decoys = decoy_numbers(titles)
    info = [
        (t.number, t.seconds, t.size)
        for t in titles
        if t.seconds >= floor and t.number not in decoys
    ]
    guessed = prefill_assignment(info, set(playalls), ep_runtimes, staged, ep_names)
    below = set(sub_floor_titles([(t.number, t.seconds, t.size) for t in titles], minlength))
    assign: dict[int, EpisodeAction] = {}
    evidence: dict[int, str] = {}
    for t in titles:
        n = t.number
        if n in decoys:
            assign[n] = None
            evidence[n] = f"decoy cluster ×{t.decoy_cluster}" if t.decoy_cluster else "shuffled map"
        elif n in below:
            assign[n] = None
            evidence[n] = "below the TV floor"
        elif n in playalls:
            assign[n] = None
            evidence[n] = "≈ sum of " + ", ".join(str(c) for c in sorted(playalls[n][1]))
        else:
            assign[n] = guessed[n]
    ordered = [t for t in titles if t.number not in below] + [
        t for t in titles if t.number in below
    ]
    return EpisodeMap(season, ordered, assign, evidence, ep_names, ep_runtimes, set(staged))


def print_accepted_episodes(
    display: str, emap: EpisodeMap, accepted: dict[int, EpisodeAction]
) -> None:
    """Echo the accepted episode map as plain lines into scrollback, before
    the run opener — the same six columns as the table. Built through
    ui.table, so it is pipe-safe and NO_COLOR-safe by construction."""
    final = replace(emap, assign=accepted)
    ui.console.print(f"\n{display} — season {emap.season:02d}")
    table = ui.table(*COLUMNS)
    for t in final.titles:
        table.add_row(*final.cells(t, {}))
    ui.console.print(table)


class ConfirmClear(ModalScreen[bool]):
    """The explicit confirm for `c`: lists the show's staged files; only `y`
    deletes — Esc/n (or any other outcome) keeps them."""

    CSS = """
    ConfirmClear { align: center middle; }
    #dialog { width: 80%; height: auto; max-height: 90%;
              border: round $warning; padding: 0 1; }
    #files { height: auto; max-height: 14; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("y", "answer(True)", "delete"),
        Binding("n,escape", "answer(False)", "keep"),
    ]

    def __init__(self, display: str, names: list[str]):
        super().__init__()
        self.display_name = display
        self.names = names

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(
                Text(
                    f"delete {len(self.names)} staged files for {self.display_name}?", style="bold"
                )
            )
            with VerticalScroll(id="files"):
                yield Static(Text("\n".join(self.names)))
            yield Static(Text("y delete · n / Esc keep (default)", style="dim"))

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


class EpisodeTable(_Table):
    """The TV selection table over an EpisodeMap.

    Keys: ↑/↓ or j/k move; digits and `-` type an episode value at the
    cursor (`3`, `3-4`) that Enter commits (a second Enter accepts); Space
    marks rows — a typed number then fills the marked rows in table order
    from that number, never touching unmarked rows; `e` extra, `s` skip; `c`
    lists and (on `y`) deletes the show's staged files; Esc drops pending
    input and marks. A bad edit is named at the status line, never restarts
    the run. Enter returns the title -> EpisodeAction map; it refuses while
    any row is red (clash / staged claim / past the season's end) and when
    nothing would rip. An unfilled episode only warns."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("e", "verb('extra')", "extra"),
        Binding("s", "verb('skip')", "skip"),
        Binding("space", "mark", "mark"),
        Binding("c", "clear_staged", "clear staged"),
        Binding("escape", "cancel", show=False),
        Binding("backspace", "backspace", show=False),
        Binding("minus", "type('-')", show=False),
        *[Binding(d, f"type('{d}')", show=False) for d in "0123456789"],
    ]

    HINT = (
        "↑/↓ j/k move · 3 / 3-4 type episode, Enter sets · Space mark run · "
        "e extra · s skip · c clear staged · Enter accept · q quit"
    )

    def __init__(
        self,
        display: str,
        emap: EpisodeMap,
        list_staging: Callable[[], list[Path]] | None = None,
        clear_staging: Callable[[list[Path]], set[int]] | None = None,
    ):
        super().__init__(f"{display} — season {emap.season:02d}", emap.titles)
        self.show_display = display
        self.emap = emap
        self.list_staging = list_staging or (list)
        self.clear_staging = clear_staging or (lambda doomed: set())
        self.marked: set[int] = set()
        self.pending = ""
        self.message = ""
        self._blocks = emap.blocks()

    def _cells(self, t: TitleInfo) -> tuple:
        return self.emap.cells(t, self._blocks, t.number in self.marked)

    def _status_widgets(self) -> list:
        return [Static(self._status_text(), id="status")]

    def _status_text(self) -> Text:
        lines = []
        if self.emap.staged:
            eps = ", ".join(f"E{e:02d}" for e in sorted(self.emap.staged))
            lines.append(
                Text(f"already staged: {eps} — c clears the show's staged files", style="dim")
            )
        if self._blocks:
            rows = ", ".join(f"t{n}" for n in sorted(self._blocks))
            lines.append(
                Text(f"✗ blocked: fix the red rows ({rows}) — Enter refused", style="bold red")
            )
        unfilled = self.emap.unfilled()
        if unfilled:
            lines.append(
                Text(
                    "! unfilled: "
                    + ", ".join(f"E{e:02d}" for e in unfilled)
                    + " — Enter still accepts",
                    style="bold yellow",
                )
            )
        if self.pending:
            lines.append(Text(f"episode ▸ {self.pending}▏  (Enter sets it)"))
        if self.message:
            lines.append(Text(self.message, style="bold"))
        return Text("\n").join(lines)

    def _regrade(self, message: str = "") -> None:
        """Re-derive violations after any edit and repaint every row (a
        clash turns both rows red) and the status lines."""
        self._blocks = self.emap.blocks()
        self.message = message
        for t in self.titles:
            self._refresh_row(t.number)
        self.query_one("#status", Static).update(self._status_text())

    @property
    def _modal_open(self) -> bool:
        return len(self.screen_stack) > 1

    def _commit(self) -> bool:
        """Apply the typed value: to the marked rows (filled in table order
        from one number) when any are marked, else to the cursor row. True
        when there was something typed."""
        if not self.pending:
            return False
        parsed = parse_episode(self.pending)
        typed, self.pending = self.pending, ""
        if parsed is None:
            self._regrade(f"✗ '{typed}' is not an episode (want 3 or 3-4)")
        elif self.marked:
            if parsed[1] != parsed[0]:
                self._regrade("✗ a marked run fills from one number, not a range")
            else:
                run = [t.number for t in self.titles if t.number in self.marked]
                for i, n in enumerate(run):
                    self.emap.assign[n] = (parsed[0] + i, parsed[0] + i)
                self.marked.clear()
                self._regrade()
        else:
            self.emap.assign[self._cursor_title().number] = parsed
            self._regrade()
        return True

    def action_type(self, ch: str) -> None:
        if self._modal_open:
            return
        if ch == "-" and ("-" in self.pending or not self.pending):
            return
        self.pending += ch
        self._regrade()

    def action_backspace(self) -> None:
        if self.pending and not self._modal_open:
            self.pending = self.pending[:-1]
            self._regrade()

    def action_cancel(self) -> None:
        if self._modal_open:
            return
        self.pending = ""
        self.marked.clear()
        self._regrade()

    def action_move(self, delta: int) -> None:
        if self._modal_open:
            return
        self._commit()
        super().action_move(delta)

    def action_verb(self, verb: str) -> None:
        if self._modal_open:
            return
        self._commit()
        self.emap.assign[self._cursor_title().number] = (
            EXTRA_EPISODES if verb == EXTRA_ACTION else None
        )
        self._regrade()

    def action_mark(self) -> None:
        if self._modal_open:
            return
        self._commit()
        n = self._cursor_title().number
        self.marked ^= {n}
        self._regrade()

    def action_accept(self) -> None:
        if self._modal_open:
            return
        if self._commit():
            return  # Enter set the typed value; a second Enter accepts
        if self._blocks:
            self._regrade("✗ Enter refused — the red rows must be fixed first")
        elif all(r is None for r in self.emap.assign.values()):
            self._regrade("✗ nothing to rip — every title is skipped")
        else:
            self.exit(dict(self.emap.assign))

    def action_quit_table(self) -> None:
        if not self._modal_open:
            super().action_quit_table()

    def action_clear_staged(self) -> None:
        if self._modal_open:
            return
        self._commit()
        doomed = self.list_staging()
        if not doomed:
            self._regrade("nothing staged for this show")
            return

        def answered(yes: bool | None) -> None:
            if not yes:
                self._regrade("kept the staged files")
                return
            self.emap.staged = self.clear_staging(doomed)
            self._regrade(f"deleted {len(doomed)} staged files")

        self.push_screen(ConfirmClear(self.show_display, [f.name for f in doomed]), answered)


def run_episode_table(
    display: str,
    emap: EpisodeMap,
    list_staging: Callable[[], list[Path]],
    clear_staging: Callable[[list[Path]], set[int]],
) -> dict[int, EpisodeAction] | str:
    """Run the TV table over the episode map and return the accepted
    title -> EpisodeAction map, QUIT or INTERRUPT (see run_selection_table).

    Args:
        display: Show name (Show (Year))
        emap: The prefilled episode map
        list_staging: The show's staged files, for `c`
        clear_staging: Deletes the listed files and returns the refreshed
            set of staged episode numbers
    """
    return EpisodeTable(display, emap, list_staging, clear_staging).run()


# --- CD: the track table ------------------------------------------------------

RIP_ACTION = "rip"
"""The CD verbs: every track prefills rip; `s` skips, `r` restores."""

CD_ACTION_STYLE = {RIP_ACTION: "bold green", SKIP_ACTION: "dim"}


@dataclass
class CdTrack:
    """One table row: the disc track number and its TOC duration (a CD has
    no size or scan — the row rides the shared table with the other columns
    blank)."""

    number: int
    seconds: int


def cd_audit_cell(toc_s: int, mb_s: int | None) -> Text:
    """The audit cell: TOC ÷ MusicBrainz track duration, green within 2%
    and yellow when off — a wrong release shows every row uniformly off.
    Blank (never yellow) when the resolve yielded no duration."""
    if not mb_s:
        return Text("")
    ratio = toc_s / mb_s
    style = "green" if 0.98 <= ratio <= 1.02 else "yellow"
    return Text(f"{ratio:.2f}", style=style)


def cd_row_cells(t: CdTrack, actions: dict[int, str], mb_durations: dict[int, int] | None) -> tuple:
    """One CD row's six cells: number · TOC length · blank size · audit ·
    action · blank notes."""
    action = actions.get(t.number, RIP_ACTION)
    return (
        Text(str(t.number), style="bold"),
        Text(hms(t.seconds)),
        Text(""),
        cd_audit_cell(t.seconds, (mb_durations or {}).get(t.number)),
        Text(action, style=CD_ACTION_STYLE.get(action, "")),
        Text(""),
    )


def print_accepted_cd(
    display: str,
    tracks: list[CdTrack],
    accepted: dict[int, str],
    mb_durations: dict[int, int] | None = None,
) -> None:
    """Echo the accepted CD table as plain lines into scrollback, before the
    drive spins — the same six columns, built through ui.table so it is
    pipe-safe and NO_COLOR-safe by construction."""
    ui.console.print(f"\n{display}")
    table = ui.table(*COLUMNS)
    for t in sorted(tracks, key=lambda t: t.number):
        table.add_row(*cd_row_cells(t, accepted, mb_durations))
    ui.console.print(table)


class CdTable(_Table):
    """The CD track table: one row per track, all prefilled rip. Keys: ↑/↓
    or j/k move; `s` skips a known-bad track, `r` restores it; Enter accepts
    and returns the track→action map; `q` quits with nothing ripped."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("s", "verb('skip')", "skip"),
        Binding("r", "verb('rip')", "restore"),
    ]

    HINT = "↑/↓ j/k move · s skip · r restore · Enter accept · q quit"

    def __init__(
        self, display: str, tracks: list[CdTrack], mb_durations: dict[int, int] | None = None
    ):
        super().__init__(display, sorted(tracks, key=lambda t: t.number))
        self.mb_durations = mb_durations
        self.actions = {t.number: RIP_ACTION for t in self.titles}

    def _cells(self, t: CdTrack) -> tuple:
        return cd_row_cells(t, self.actions, self.mb_durations)

    def action_verb(self, verb: str) -> None:
        number = self._cursor_title().number
        self.actions[number] = verb
        self._refresh_row(number)

    def action_accept(self) -> None:
        if all(a == SKIP_ACTION for a in self.actions.values()):
            self.query_one("#hint", Static).update("nothing to rip — every track is skipped")
            return
        self.exit(dict(self.actions))


def run_cd_table(
    display: str,
    tracks: list[CdTrack],
    mb_durations: dict[int, int] | None = None,
) -> dict[int, str] | str:
    """Run the CD track table and return the accepted track→action map,
    QUIT (the caller exits 1 with nothing ripped) or INTERRUPT (see
    run_selection_table)."""
    return CdTable(display, tracks, mb_durations).run()
