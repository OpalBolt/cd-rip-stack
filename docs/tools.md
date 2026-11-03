# Tools

`validate` and `search` — the two read-only library commands. `validate`
checks the video library for anomalies; `search` answers "have I already
ripped this?".

## validate

`skive validate` walks `library/movies/` and `library/tv/` under the videos
share (`$VIDEOS_MOUNT`) and reports findings. It is read-only and always
exits 0 — findings are leads for a human, not verdicts. Video library only
today; a music library checker is roadmap work.

```bash
skive validate             # the full table
skive validate --issues    # issues only, plus a per-folder action plan
skive validate --fix       # also apply the safe automatic rename
skive validate --json      # the report as JSON (composes: --json --issues)
```

The checks — one row each in the report:

- **Ratio** — duration ÷ TMDB runtime per graded file: the movie main
  encode, TV episodes, and any other file reaching the main-feature floor
  (extras included). Graded against the norm band (0.92–1.04): green inside,
  yellow outside, red beyond what a PAL-vs-NTSC speedup or a cut difference
  explains. A full-length extra beside the main is the two-candidates
  signature — the lead for "the wrong title became the main". An unknown
  TMDB runtime leaves a blank cell, never a false yellow.
- **Staging name** — files still wearing `_tNN`/`_eNN` staging labels inside
  a title folder.
- **Missing episodes** — a season folder missing episodes against the TMDB
  season list or its validation.json episode map.
- **Duplicate id** — the same TMDB id filed under two title folders.
- **Unidentified** — a folder without validation.json whose identity cannot
  be recovered by TMDB name search (no `(Year)` in the name, search failed,
  or no match).
- **Unreadable** — a file ffprobe cannot measure.

`--issues` keeps only the actionable rows: every non-ratio check, plus ratio
rows that are off-band (yellow/red) or graded `extras/` files. It appends a
per-folder action plan — one entry per folder carrying an issue, worst
priority first (red, yellow, note), with a worded sentence per row; staging
names collapse to one counted line pointing at `--fix`. The plain table
stays compact by design; the worded phrasing lives in the action plan.

`--fix` is the only arm that changes anything: it renames staging-labelled
files under `extras/` directories to the pipeline's
`<base> - Extra NN[.pN].mkv` form, rolling forward past names already
taken. Staging labels outside `extras/` are never guessed — they stay in the
report. Idempotent: a clean tree renames nothing.

`--json` swaps the table for the full report: the ratio band definition, a
summary, every finding (issue rows embed their recommendation), the
recommendation plan, and — with `--fix` — the applied renames. No
`library/` under the share warns instead of walking the wrong tree.

## search

`skive search <scope> <query>` is a case-insensitive substring walk over
folder names — no database, just the disk.

Scopes: `movie` (movies roots only), `tv` (tv roots only), `video` (both),
`music`, `all`. Video scopes walk the same title folders across states:
`rip/` (staging), `library/`, `lossless/`, `issues/`. The music scope walks
`rip/cd/`, `rip/other/`, `rip/bandcamp/` and `library/<Artist>/<Album>/`.

```bash
skive search movie "mamma mia"
skive search tv "west wing"
skive search all "kind of blue"
```

Each hit prints the title and its states; TV hits list the seasons carried
(`S01, S02` — zero as `Specials`), and staging hits are marked, so you know
a copy is not filed yet. Music hits print `Artist / Album`.

Zero hits print `no hits` plus up to three did-you-mean near-miss titles —
and the command still exits 0. A scoped share that cannot be mounted prints
one note line and is skipped; the run still succeeds.

Related: [video](video.md) · [transcode](transcode.md) · [music](music.md) · [nas-layout](nas-layout.md)
