# Video

`rip movie` and `rip tv`: insert disc → scan → TMDB pick → staging. One session
ends with lossless MKVs in `rip/<type>/<Name (Year)>/`, ready for transcode.

```bash
skive rip movie               # rip a movie disc
skive rip tv                  # pick the show, season, confirm the table
skive rip movie --tmdbid 680  # skip the search (tv:N for a show, or a URL)
```

## One session, both commands

1. **Insert the disc.** Recordable media (DVD±R/RW, BD-R) refuses the rip
   unless you checked the disc by eye and pass `--verified` — thrift-store
   burns are often bootlegs.
2. **Fast scan** — `lsdvd` on the device (DVD) or `ffprobe bluray:` (BD) reads
   titles as durations in seconds. Any fast-scan failure or two titles within
   ±1s (decoy playlists) escalates to the full MakeMKV scan (~1–3 min, real
   title numbers). `-T` and `--extras` runs start on the full scan; a TV disc
   whose titles sum into a play-all escalates too.
3. **TMDB** — TMDB must answer before anything is ripped. The disc's volume
   label prefills the search and the results table appears: pick a hit, `s`
   types a new query, an empty result re-prompts typed, `q` aborts; `--tmdbid`
   skips the search.
4. **Staging** — `rip/<type>/<Name (Year)>/d<N>/` (`N` = the drive) is created
   only after your pick; stale junk-labeled leftovers (`B1_t00.mkv`) from a
   failed earlier run are purged, scoped to this title's staging tree only.
5. **Rip** — one `makemkvcon` call per title at `--minlength=$RIP_MINLENGTH`
   (a fast-scan run rips all titles in one call). A failed title never kills
   the run: the worst return code becomes the exit code.
6. **Promotion, then rename** — finished files move up from `d<N>/` into the
   title folder (a collision leaves the file in `d<N>/` with a note, never
   clobbers); then junk labels become `<Name (Year)>_tNN[.pN].mkv`,
   `<Name (Year)> - Extra NN[.pN].mkv`, or `<Show (Year)>_SNNENN[-ENNN].mkv`.
7. **Done** — exit 0 just finishes; eject and swap discs by hand.

`-T/--titles` takes `all` (default), one number, `N-M`, or a comma list
(`0,5-7`); a stray `disc:N` is rejected before the drive spins. Bare `-T`
opens a checkbox menu over the scan — every box starts checked: space toggles,
Enter rips the checked ones, `↑/↓` or `j/k` move, `q` quits.

## rip movie

Before the rip starts, the pre-rip gate: a table (title, duration, size,
ratio vs the TMDB runtime), then the confirm prompt. Several titles near the
TMDB runtime raise a loud main-pick prompt — Enter takes the default
(biggest), a number picks, `q` quits, anything else re-asks. Any yellow ratio
row escalates the confirm to a default-No `y/N`.

```text
Rip with this map? [Enter] rip · q quit
›
```

```bash
skive rip movie --extras   # a bonus disc: no main pick, extras numbering
skive rip movie --part 1   # a split film; --part 2 on the other disc
```

- **Main feature** — auto-picked when one title sits near the TMDB runtime;
  stages as `_tNN.mkv`. Audio keeps the film's original language; subtitles
  keep Danish when present, else English.
- **Parts** — pick the same movie on each disc; halves stage as `_tNN.pN.mkv`;
  transcode merges the main feature's parts in order.
- **Extras** — `--extras` stages `- Extra NN`; a second extras disc rolls past
  the existing numbers.

`--part` is movie-only.

## rip tv

Confirm the show, then the season: Enter = 1, `0` = specials. Titles below
the TV floor (`max(RIP_MINLENGTH, 600)` s) drop entirely, and a title whose
duration matches the sum of 2–6 others is a play-all: the rip skips it; a
fix row assigning it an episode is the only way it rips. `--extras` skips
the season prompt and table (a bonus disc).

One prompt states which episodes this disc carries, pre-filled with a guess
from the first unstaged episode (runtime-matched when TMDB has runtimes, else
one per eligible title) — Enter takes it. Titles are assigned to that stated range in disc order; the table's ratio
column (disc ÷ TMDB runtime, green ~0.92–1.04 — PAL runs ~4–5% short) grades,
never decides.

The confirm table is the single place to correct the map. The prompt lists
the answers:

```text
Rip with this map? [Enter] rip · 3=7 / 1=1-2 / 2=e fix row · n range · c clear staged · q quit
›
```

| Answer | Does |
|---|---|
| Enter | rip as proposed |
| `3=7` / `1=1-2` | re-point title 3 at episode 7 / title 1 at episodes 1–2 |
| `2=e` | demote title 2 to an extra |
| `n` | re-ask the episode range |
| `c` | list the show's staged files; `y` deletes them (mis-file remedy) |
| `q` | quit — nothing ripped, the rip never started |

A title left outside the stated range lists as `→ extra`.

## The console

Every prompt has one shape: the question, the default in brackets, the
choices dimmed, then an answer on a `›` line. A destructive confirm defaults
to No:

```text
delete 4 staged files for Andor (2022)? [y/N]
›
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
Piped, or with `NO_COLOR` or `TERM=dumb`, the same events print as plain
tagged lines (`info`, `ok`, `warn`, `fail`) with no footer. The
[transcode](transcode.md#the-console) page shows both forms in full.

## Play-all-only discs and the chapter split

A disc exposing exactly one eligible title carrying a multi-episode range rips
whole — its episodes are chapter ranges inside one file. After the rename, a
boundary table appears: chapter starts graded against summed TMDB runtimes
suggest each split point; `2=14` means episode 2 starts at chapter 14
(1-based, like VLC). Enter cuts at these boundaries; a fix row moves one
interior boundary (E-lo is pinned to chapter 1; a rejected row prints why and
changes nothing); suggestions outside ±120s of the summed runtimes are flagged
above the table. The cut is `ffmpeg -c copy` — lossless; the combined file is
deleted once every piece exists.

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
  title folder), and staged episodes release their numbers so the TV range
  prefill continues where you left off.

Deciding logic — scan escalation, play-all handling, the split policy — lives in
CONTEXT.md and ADRs 0002, 0008, 0009; this page states what happens, one pointer
from here.

Related: [setup](setup.md) · [nas-layout](nas-layout.md) · [transcode](transcode.md) · [tools](tools.md)
