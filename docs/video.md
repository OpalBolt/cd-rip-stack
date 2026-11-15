# Video

`rip movie` and `rip tv`: insert disc → scan → TMDB pick → selection table →
rip. One session ends with lossless MKVs in `rip/<type>/<Name (Year)>/`,
ready for transcode.

```bash
skive rip movie               # rip a movie disc
skive rip tv                  # pick the show, season, set the table
skive rip movie --tmdbid 680  # skip the search (tv:N for a show, or a URL)
```

## One session, both commands

1. **Insert the disc.** Recordable media (DVD±R/RW, BD-R) refuses the rip
   unless you checked the disc by eye and pass `--verified` — thrift-store
   burns are often bootlegs.
2. **Disc scan** — one `makemkvcon info` run reads every title: real
   MakeMKV title numbers, durations and sizes (ADR 0021). The verbatim
   output persists as a scan dump, so a questioned table replays without
   the disc in the drive (`MAKEMKV_INFO`).
3. **TMDB** — TMDB must answer before anything is ripped. The disc's volume
   label prefills the search and the results table appears: pick a hit, `s`
   types a new query, an empty result re-prompts typed, `q` aborts; `--tmdbid`
   skips the search.
4. **Staging** — `rip/<type>/<Name (Year)>/d<N>/` (`N` = the drive) is created
   only after your pick; stale junk-labeled leftovers (`B1_t00.mkv`) from a
   failed earlier run are purged, scoped to this title's staging tree only.
5. **Selection table** — the disc's rows with their audited prefill;
   Enter accepts, and the accepted table echoes into scrollback. One
   canonical description below ([the selection table](#the-selection-table)).
6. **Rip** — one `makemkvcon` call per accepted title at
   `--minlength=$RIP_MINLENGTH`; skipped titles are never ripped. A failed
   title never kills the run: the worst return code becomes the exit code.
7. **Promotion, then rename** — finished files move up from `d<N>/` into the
   title folder (a collision leaves the file in `d<N>/` with a note, never
   clobbers); then junk labels become `<Name (Year)>_tNN[.pN].mkv`,
   `<Name (Year)> - Extra NN[.pN].mkv`, or `<Show (Year)>_SNNENN[-ENNN].mkv`.
8. **Done** — exit 0 just finishes; eject and swap discs by hand.

## rip movie

The table decides (ADR 0020). Main arbitration prefills: one title near the
TMDB runtime takes **main** automatically; several near it prefill biggest
with the ambiguity named in the main row's notes; none does (a shorts,
bonus-shaped disc) and biggest still prefills main, so exactly one main
always holds. Everything else prefills **skip**. The keys you might need:
`m` moves the main to the cursor row, `e` marks an extra, `s` drops a title.

Accept and one line names the rip set before the drive spins:

```text
rip set: t7 + extras t3 (extras marked for encoding)
```

- **Main feature** — stages as `_tNN.mkv`. Audio keeps the film's original
  language; subtitles keep Danish when present, else English.
- **Extras** — an accepted extra row rips the title, stages it as
  `<Name (Year)> - Extra NN`, and sets the validation extras marker:
  transcode encodes extras too (superfast). A second extras disc rolls past
  the existing numbers.
- **Parts** — pick the same movie on each disc; halves stage as `_tNN.pN.mkv`;
  transcode merges the main feature's parts in order.

```bash
skive rip movie --part 1   # a split film; --part 2 on the other disc
```

`--part` is movie-only.

## rip tv

Confirm the show, then the season: Enter = 1, `0` = specials. The rip
preflight fires after the season prompt: this exact season already in the
library asks a default-No confirm; other seasons only inform. Then
the table decides the whole episode map (ADR 0020):

- The **episode guess** prefills every eligible title, walking from the first
  unstaged episode — runtime-matched when TMDB has runtimes, else one per
  title in disc order.
- A **play-all** — a title whose duration matches the sum of 2–6 others —
  prefills skip with its evidence in notes (`≈ sum of 3, 4, 5`); assign it
  an episode and it rips as that episode.
- Titles below the **TV floor** (`max(RIP_MINLENGTH, 600)` s) list last with
  their real disc numbers, prefilled skip — a short extra can be rescued
  with a typed episode or `e`.

Accept and the rip takes the accepted map: skipped titles (prefilled
play-alls, sub-floor fragments, anything you restated `s`) are never ripped;
nothing skipped rips "all" in one call. Extras (`e`) stage as
`<Show (Year)> - Extra NN.mkv` beside the episodes. A disc exposing exactly one
eligible title carrying a multi-episode range announces the split before the
drive spins — see below.

## The selection table

Every rip — movie, TV, CD — decides through one table before the drive
spins (ADR 0020). Six columns, identical on every medium:

`#` · length · size · audit · action · notes

- **`#`** — the disc's real number: a MakeMKV title number, or a CD track
  number.
- **length / size** — duration and bytes; CDs show a blank size (the TOC
  carries no size).
- **audit** — the grade, never the decision: a movie title's duration ÷ the
  TMDB runtime, a TV row's ratio for the episode it claims, a CD track's TOC
  ÷ MusicBrainz duration. Green inside the norm, yellow outside. The audit
  never blocks Enter.
- **action** — what the row rips as (the verbs below).
- **notes** — evidence and reasons: play-all sums, the main arbitration's
  ambiguity, episode names, and any blocking reason in full.

Verbs per medium:

- **movie** — `m` main (exactly one always holds; `m` moves it), `e` extra,
  `s` skip.
- **TV** — digits type an episode at the cursor (`3`, or `3-4` for a
double) and Enter sets it; `Space` marks rows, and one typed number fills
the marked rows in table order; `e` extra, `s` skip; `c` clear staged —
lists the show's staged files and, on `y`, deletes them (mis-file remedy).
- **CD** — `s` skip a track, `r` restore; every track prefills rip.

Keys, every medium: `↑`/`↓` or `j`/`k` move the cursor; `Enter` accepts;
`q` quits — nothing ripped, exit 1; `Ctrl+C` is the repo interrupt (partial
cleanup, exit 130); `Esc` drops pending input and marks (TV). A bad edit
is named in the status line below the table — no keystroke restarts the run.

Blocking vs warning: a **red row blocks Enter** and names why in its notes —
an episode clash, a claim on an already-staged episode, an episode past the
season's end. **Unfilled episodes** in the season and every graded audit
only warn — gaps are your call. Enter also refuses when nothing would rip.
On accept, the table echoes into scrollback as plain lines — the same six
columns, pipe-safe and `NO_COLOR`-safe — before the run opener.

Non-TTY: rip flows are interactive by design. A run without a terminal
refuses before the table — one stderr line (`selection table needs a
terminal`), exit 1, nothing spins.

## The console

One-answer prompts keep one shape: the question, the default in brackets
when it has one (the sync-sd picker has none), the choices dimmed, then an
answer on a `›` line. The table's confirms are keys instead — a destructive
confirm is a Textual modal defaulting to No:

```text
y delete · n / Esc keep (default)
```

After the picks and the map, the rip opens with one line that names the
scope (`skive rip tv · Andor (2022) · season 1 · disc 2 · /dev/sr0`). Each
title then gets a bold heading with its events indented beneath
(`▸ 3/6  Andor (2022) S01E04` for TV, `▸ 1/2  title 7` for a movie). Events stay in scrollback with a symbol: `✓` done, `·` routine,
`!` warning, `✗` failure.

A footer of two lines redraws under the scroll. Line one names the title in
progress, with a progress bar and ETA. Line two shows the disc total:

```text
 title 3/6  Andor (2022) S01E04 · rip  ━━━━━━                31%  ETA 14m
      disc 9.4/31.2 GB
```

The run ends with the footer gone, a summary table of titles, and one result
line (`✓ 6 ok · 1h02m`). After a failure, the line names the run log to read.
`NO_COLOR` and `TERM=dumb` only affect the colour. Piped or redirected, the
same events print as plain tagged lines (`info`, `ok`, `warn`, `fail`) with
no footer. The
[transcode](transcode.md#the-console) page shows both forms in full.

## Play-all-only discs and the chapter split

A disc exposing exactly one eligible title carrying a multi-episode range rips
whole — its episodes are chapter ranges inside one file. After the rename, the
boundary table opens — the selection component's fourth consumer, with its own
columns (episode · runtime · chapter start · notes): chapter starts graded
against summed TMDB runtimes suggest each split point. Digits type a chapter
number at the cursor row and Enter sets that boundary (1-based, like VLC);
E-lo is pinned to chapter 1 — only interior boundaries move. Suggestions
outside ±120s of the summed runtimes are flagged in the row's notes and
the status line under the table — a warning, never a block; a rejected
chapter value turns the row red, names why, and
Enter refuses while any row is red. Accepting cuts at the confirmed
boundaries; `q` keeps the combined file. The cut is `ffmpeg -c copy` —
lossless; the combined file is deleted once every piece exists.

Declining (`q`), a missing TMDB runtime, fewer chapters than episodes, an
existing per-episode target, or any ffmpeg failure keeps the combined
`_SNNENN-ENNN.mkv` — the split never loses bytes.

## Failure and interruption

- **Ctrl+C once** — the in-flight partial is deleted, completed renamed titles
  are kept, the run log records `status=interrupted`, exit 130. A second
  Ctrl+C exits instantly without cleanup; the next run's junk purge sweeps
  what's left.
- **A failed title** — renamed if its file completed, noted in the run log,
  never fatal; the exit code is the worst `makemkvcon` rc.
- **Rerunning** — re-run the same command: the junk purge cleans stale
  partials wherever they rest (mid-rip in `d<N>/`, post-promotion in the
  title folder), and staged episodes release their numbers so the episode
  guess continues where you left off.

Deciding logic — the table and its audits (ADR 0020), the single disc scan
(ADR 0021), play-all handling, the split policy — lives in CONTEXT.md and
ADRs 0008, 0009, 0020, 0021; this page states what happens, one pointer
from here.

Related: [setup](setup.md) · [nas-layout](nas-layout.md) · [transcode](transcode.md) · [tools](tools.md)
