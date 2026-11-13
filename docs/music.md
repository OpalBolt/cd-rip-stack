# Music

`rip music`, `tag` and `sync-sd`: CD → staged album → filed library → SD card
in one flow. Staging is `/mnt/music/rip/<music source>/<album>/`, one album
folder each ([nas-layout](nas-layout.md)); the source decides how `tag` files
it: `cd/` (a cyanrip rip), `bandcamp/` and `other/` (downloads — no staging
command yet, roadmap work: unzip or copy them in by hand, then skip to
`tag`). Nothing in this flow has run against real hardware yet — written from
the code and its tests.

## rip music

```bash
skive rip music 103            # rip with the drive's sample offset
skive rip music 6 -N           # skip the MusicBrainz lookup
skive rip music -d 1 6         # rip from drive 1 (same -d N as rip movie/tv)
```

The offset is your drive's CD sample offset and is required — the drive table
is on [setup](setup.md). Without it skive prints usage and exits 2, drive
untouched. Further arguments pass straight through to cyanrip; `-N` also
skips skive's online gate, so an offline rip still runs. With two drives
attached, `-d N` picks the drive — the same makemkv drive number `rip movie`
and `rip tv` use, so the same `-d 1` means the same physical drive. It works
before or after the offset (`skive rip music 6 -d 1` too).

The share mounts on demand and cyanrip runs inside `/mnt/music/rip/cd/`,
writing each album as final-named FLACs into its own `<album>/` subfolder.
If MusicBrainz can't name the disc, the rip fails with a miss box and never
prompts. Several releases match → the box lists the candidates; re-run by hand
with the printed command (`skive rip music 6 -- -R <n>`). No release at all →
the box gives the attach link: attach the disc to its release, then re-run. On
a terminal the console opens with one line (`skive rip music · <album> · offset 6`), then a
`▸ 7/12  <title>` heading per track with its events indented beneath.
A footer redraws under the scroll with the track counter, title, offset and a
progress bar:

```text
 track 7/12  Paranoid Android · offset 6  ━━━━━━━━━━━━          61%
```

cyanrip prints a track's title only after the track finishes. A track that
cyanrip reported progress for already has its heading by then, so the heading
reads `▸ 7/12  track 7` and the title shows only in the footer. The heading
carries the title (`▸ 7/12  Paranoid Android`) when no progress line came
first. Piped, the same two forms print as plain tagged lines:

```text
info  7/12  Paranoid Android
info  7/12  track 7
```

`-v` adds cyanrip tool lines and `-vv` passes cyanrip raw. The run ends with a per-track
summary table (track, duration, result) and one result line. A clean rip just
finishes — eject and swap discs by hand. Ctrl-C stops the child cleanly, logs `interrupted`, exits 130.

### The album record

A successful rip writes `validation.json` into the staged album folder — born
complete: the cyanrip run, the drive offset used, and each track's AccurateRip
status (`accurate`, `partial`, `not_found`) with its confidence. If cyanrip's
report cannot be parsed, the record is still written without the AccurateRip
results and the run log calls it out. `tag` merges into it later.

## tag

```bash
skive tag                      # files all sources in order: cd, other, bandcamp
skive tag cd                   # files only cd (or bandcamp / other)
```

The share mounts, empty sources are skipped, and each source gets its own
beets import. Filing **moves** (`move: yes` in `config/beets-config.yaml`):
anything still in `rip/` is unfiled and offered again next run, an album
skipped at the beets prompt stays in `rip/`, duplicate albums prompt (keep
both / remove / merge), and after each source the run log marks every staged
album `filed` or `left`. The mode follows the source: `cd` and `other` get
normal MusicBrainz autotagging; `bandcamp` imports as-is (`-A`) — tags stay
as they came, but the album still gets its library path, ReplayGain, cover
art and genres.

beets runs in its own interactive session, which skive does not touch. Around
it, skive prints one line per album (`✓ filed <album>`, `! left <album>: still
in rip/cd`) and ends on one result line, for example `✓ 3 filed · 12s` or
`! 3 filed · 1 left · 12s — see /mnt/music/logs/rip.jsonl (run <id>)`. Piped,
the symbols become `ok` and `warn`.

Each filed album ends with a `validation.json` provenance record: source,
release identity, track list, and per disc the tag run, date and mode. The
local plugin (`beetsplug/skive_album.py`) merges into any record already in
the destination — filing disc 2 keeps disc 1's AccurateRip proof — and
carries the cyanrip `.log`/`.cue` sidecars into the album folder, suffixed
` CD<n>` for multi-disc albums. The beets DB (`/mnt/music/music.db`) and
import log (`/mnt/music/logs/import.log`) live on the share; the config is
passed with `-c`, so nothing depends on your cwd or a per-machine beets
config dir.

Two gotchas, both hit at the keyboard:

- **Do not hand-run `beet import`.** It files albums, but the album plugin
  does not load — only `skive tag` starts the beets child with the repo on
  `PYTHONPATH` — so a hand-run import writes no provenance record and leaves
  the `.log`/`.cue` sidecars behind. Use `skive tag`.
- **replaygain needs a GStreamer/GI stack** on this box or beets fails at
  start-up. The install is on [setup](setup.md).

## sync-sd

```bash
skive sync-sd                  # auto-detect the card
skive sync-sd /media/USER/CARD # skip detection, use this mountpoint
```

Finds one mounted removable card (auto-mounts under `/media` or `/run/media`
count); none mounted → lists the `sdx`/`mmcblk` disks and mounts your pick
(sudo). The library copies with `rsync -r --size-only` — size-only because
FAT/exFAT reject `-a`'s timestamps; re-runs skip files already on the card.
`validation.json`, `*.log` and `*.cue` are excluded: the card holds only
playable music. A card this run mounted is unmounted on exit — success,
Ctrl-C, or failure; one already mounted stays mounted.

If you pass no mountpoint and no card is mounted, skive lists the disks and asks
`Pick one` on a `›` line. On a terminal, a one-line footer shows the rsync
progress bar, rate and ETA. The run ends on one result line: `✓ synced 4.1 GB ·
2m10s`, or `✗ interrupted · 2m10s — re-run to resume`.

## search music

`skive search music <term>` walks the three staging sources plus
`library/<Artist>/<Year Album>/`: staging hits show `staging (source)`,
album hits `Artist / Album`, matching is a case-insensitive substring. No
hits still exits 0 (did-you-mean when one is near); an unmountable share is
noted and skipped. Scopes and semantics: [tools](tools.md).

## Existing library

One-time steps for a library that predates this pipeline. Run from the repo.
Seed the beets DB so the first `skive tag` knows what is already filed:

```bash
uv run beet -c config/beets-config.yaml import -A -C -M /mnt/music/library
```

`-A` adds as-is (no autotagging), `-C` no copy, `-M` no move. `-M` matters:
the config sets `move: yes`, and `-C` alone does not turn it off — without
`-M` the seed renames files to their tagged paths.

Backfill records for albums that never got one (dry run by default — prints
every planned write, never replaces an existing record):

```bash
uv run python scripts/backfill_albums.py          # dry run: prints every planned write
uv run python scripts/backfill_albums.py --apply  # write the missing records
uv run python scripts/backfill_albums.py --root /mnt/music
```

Reconstructed records carry `source: unknown`, the release identity found in
tags, and per-track file/duration/format — no AccurateRip data.

Record shape and run-log mechanics live in CONTEXT.md — internals stay there.

Related: [setup](setup.md) · [nas-layout](nas-layout.md) · [tools](tools.md)
