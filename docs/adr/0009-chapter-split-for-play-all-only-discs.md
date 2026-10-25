# Play-all-only discs split into episodes at chapter boundaries, after the rip

Downton Abbey S4 D1 exposed exactly one playable title: a 2:41:09 play-all whose
episodes are chapter ranges inside it. No per-episode titles exist, so the ±120s
subset-sum play-all detection can never flag this shape — and the Double episode
rule ("kept as one file, never split") would strand the season as one giant file.
A disc with exactly one eligible title assigned an episode range (`0=1-3`) is
declared a play-all-only disc: it rips as one title, then a post-rip table splits
it. Post-rip because chapter timestamps do not exist before the file does —
makemkvcon reports only a chapter count.

The boundaries are suggested by grading cumulative chapter starts against summed
TMDB runtimes (±120s window, the ratio-audit band), then confirmed by a human at
a table — the same durations-suggest-people-decide rule as the episode guess
(ADR 0001): `2=14` means episode 2 starts at chapter 14, numbered 1-based to
match what VLC shows. The cut is `ffmpeg -c copy` (DVD chapters are
keyframe-aligned; mkvmerge is not installed). On success the combined file is
deleted — the pieces are lossless and complete. A declined table (`q`, Ctrl+C),
a missing TMDB runtime, an existing target, or any ffmpeg failure keeps the
combined `_S01E01-E03` file: the split never loses bytes, and the fallback is
exactly the pre-feature output shape.

## Consequences

- Doubles on multi-title discs are untouched — still one file, still `S01E01-E02`.
  The Double episode rule survives with one carve-out: the play-all-only shape.
- A combined `_SNNENN-ENNN.mkv` in staging now means "split declined or failed" —
  visible in validation.json as one row graded against the summed runtimes.
- Existing `S01E01-E02` files from earlier seasons stay valid library shapes;
  transcode and the library checker treat them exactly as before.
