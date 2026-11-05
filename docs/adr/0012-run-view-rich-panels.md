# Run view: rich panels for the long-running commands

The console layer was never standardized: ~186 print calls across 17 modules
in four styles (`skive:` notes to stderr, plain stdout lines in transcode,
rich tables, subcommand prefixes), and the one genuinely concurrent case —
transcode's prefetch copy running under an encode — interleaved bare `40%`
lines with the encode progress and any skip notes, three activities with no
structure. This ADR standardizes the console layer: one module, one event
vocabulary, and a rich-panel run view for the four long-running commands
(`rip movie`, `rip tv`, `rip music`, `transcode`). The durable logs (run log,
raw log, scan dump — see CONTEXT.md) are untouched: they were standardized
separately and verbosity has never gated them.

## The decision

- **Engine: rich** (`Live` + `Layout`/`Group`), the library already drawing
  the makemkv status block and every table. No alternate screen — normal
  terminal behavior over SSH, tmux, and the distrobox.
- **Layout: stacked panels.** A status panel (the run's situation: what is
  ripping/encoding, progress, ETA), a background panel (one line per
  concurrent activity — the prefetch copy), and a log panel (the tail of
  recent event lines, ~8 visible). The log tail redraws in place; terminal
  scrollback is not the event record — the run log is.
- **Event lines: three kinds** — `info` (routine events, skips, deferrals),
  `warn` (the loud note), `fail`. All to stderr; stdout carries only data
  answers (`search` hits, `--json`).
- **Interactive moments suspend the view.** Pick tables, fix-row prompts,
  season prompts print normally; the view redraws after the answer.
- **Non-TTY falls back to plain tagged lines** — the same event stream as
  `info`/`warn`/`fail`-prefixed lines, plus the throttled progress lines.
  A piped run's story must survive the redirect.
- **Tool output (makemkv MSG, cyanrip, HandBrake) lands in the log panel**,
  prefixed with the tool name, gated by `-v`/`-vv` exactly as today. At `-vv`
  the flood thrashes the redraw — accepted; `-vv` is debugging.
- **The run ends: panels clear, run summary prints** — warn/fail lines
  replayed, then one rich table (per-item parts × timings × result for
  transcode; title × duration × size × result for rips), the ok/failed count,
  and the `--keep` banner.
- **Absorption:** makemkv's bespoke 3-line Live block keeps its robot-output
  parsing but renders through the run view. Short commands (`tag`, `search`,
  `validate`, `sync-sd`) never draw panels — their event lines route through
  the same module so formatting stays uniform.
- **Perf line (transcode only):** the status panel's bottom line carries
  CPU % from `/proc/stat` and the CPU/GPU package temps from hwmon — plain
  sysfs reads, no new dependencies or container tools — red at ≥ 95 °C.
  Omitted entirely in the plain-line fallback; unreadable sensors drop
  silently. The GPU temp matters because the default `-e auto` path encodes
  on the VCN, not the CPU.

## Rejected alternatives

- **Textual** — real scrollback and widgets, but it takes the alternate
  screen (tmux scrollback goes blind mid-run, exactly when an overnight
  encode needs a glance) and is a heavy new dependency for four screens.
- **Activity-tagged plain lines only** (no panels) — cheapest, fully
  grep-able, and it survives as the non-TTY fallback, but it gives no
  at-a-glance answer to "what is happening right now" — the operator's
  actual question when checking a running encode.
- **Single merged panel** — fewer boxes, but every concern fights for one
  space; the stacked layout gives each concern a fixed home.

## Consequences

- Every command's console output routes through one module; adding a fifth
  panel-bearing command is configuration, not new rendering code.
- The plain-line fallback is the testable surface — the suite asserts
  against tagged lines, not terminal frames.
- Migration lands in two slices: `ui.py` + `transcode` first (the concurrent
  case validates the whole design), then the rip commands (makemkv block
  absorption, rip music).
- `windows/transcode.py` stays frozen and line-based; if it ever thaws, it
  adopts the module.
