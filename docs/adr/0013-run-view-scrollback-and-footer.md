---
status: chrome superseded by ADR 0014 (framed scrollback); scrollback model stands
---

# Run view: scrollback lines and a small live footer

ADR 0012's stacked panels (`Live` + `Layout`) filled the whole terminal
height, wrapped about five changing values in three borders, and kept
events only as an 8-line tail that vanished when the view closed. In use it
looked empty, heavy and unreadable at a glance, and the run's story was gone
from scrollback. The commands are watched, not driven, so the light tier
fits: event and tool lines print as ordinary scrollback lines (`✓ · ! ✗`
symbols on a TTY, the `ok`/`info`/`warn`/`fail` words when plain), and only
a borderless footer of at most three lines redraws beneath them: current
item and progress bar with ETA, background activity, perf line. No `Layout`,
no `Panel`. ADR 0012's other decisions stand: rich, no alternate screen,
prompts suspend the live part, the plain-line fallback, one console module.

## Considered options

- **Keep the panels, cap their height** — fixes the empty space, but events
  still vanish and the boxes stay heavy.
- **Textual / full tier** — rejected again for 0012's reasons; nothing here
  needs a key loop.
