---
status: accepted — supersedes the chrome of ADR 0013; its scrollback model stands
---

# Run view: framed scrollback

ADR 0013 fixed ADR 0012's real fault: events now stay in scrollback, not in a
tail that vanishes. But it also removed every border and every colour that did
not mean a status. In use the run read as a flat list of dim lines. No item
stood apart from the next. Prefetch start and finish were lost among the other
lines, and the footer left nothing behind. The operator wanted boxes and
colour from the start.

We keep scrollback as the record and draw frames that can stream line by
line. A closed rounded **run opener** box comes first. Each item gets a
**item frame**: a `╭─ 1/4  Name ───╮` header rule, event lines between
`│ … │` sides, and a `╰─ ✓ done 7m56s ───╯` closing rule that carries the
result. Each line is drawn whole at the shared width, so the frame streams
yet is rounded at both ends. A closed rounded **live box** redraws below
them; while a frame is open its top is `├─ now ───┤`, joined to the frame.
Colours follow the Kanagawa (wave) palette. The run ends with a
rounded **summary** box whose bottom title is the result line. All boxes share
one width (`min(terminal, 100)`) and one left edge, so they flow together.
Phase names have fixed colours. Only a finished phase writes a line to
scrollback. Step lines (`OCR eng track 3...`) live in the live box, or in
scrollback at `-v`. The plain fallback (pipe, `NO_COLOR`, `TERM=dumb`) does
not change and has no box characters.

A frame header cannot change colour after it prints. Only the closing rule
shows the outcome. We accept this.

Same change: the transcode **prefetch** now starts when the current unit's own
copy finishes, not when its encode starts. It overlaps subs OCR and crop as
well, and peak workdir use stays the same. Its finish is an event line
(`✓ prefetch <name> ready`).

## Considered options

- **Borderless with more colour** (patch ADR 0013): small, but it does not
  give the structure the operator asked for.
- **Closed panels per item**: a closed panel can only draw when its content
  is complete. Events would then appear only when the item ends.
- **Textual / alternate screen**: rejected again for ADR 0012's reasons.
