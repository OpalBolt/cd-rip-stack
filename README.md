# skive

Rip CDs to AccurateRip-verified FLAC. Rip DVDs and Blu-rays to lossless MKV on
Linux, then transcode to 1080p H.265 on a Windows box. One Python app, five
commands:

```text
skive rip movie  [-T SPEC] [--part N] [-E] [--tmdbid ID|URL] [-V]   rip a movie to lossless MKV
skive rip tv     [-T SPEC] [-E] [--tmdbid tv:N|URL] [-V]            rip a TV disc to named episodes
skive rip music  [offset] [cyanrip flags…]                                        rip a CD to FLAC
skive tag                                                                         file the CD rip into the library
skive sync-sd    [mountpoint]                                                     copy the library to an SD card
```

Ripping happens on the Linux machine with the drive. Transcoding runs on a
separate Windows machine (see [Windows transcode box](#windows-transcode-box)).
Both share one NAS mount: `rip/` stages the lossless MKVs (one folder per
movie or show), `library/` holds the encodes. `windows/transcode.py` +
`windows/subs.py` are the whole Windows install, copied over by hand.

Every skive command takes `-v` (tool messages) or `-vv` (raw tool output)
before the subcommand — `skive -v rip movie`. `rip movie` and `rip tv` also
take `-d N` to name the drive, so two discs can rip at once. Ctrl+C anywhere
is a graceful interrupt: the child stops, the in-flight partial is deleted,
completed titles are kept, exit 130.

## Linux setup

Requires Python 3.13 and uv. If you use mise:

```bash
mise run install      # tools + skive on PATH (editable, from this repo)
mise run update       # ff-pull and refresh the install
```

or, without mise, install uv by its own means and then:

```bash
uv tool install --editable .
```

That puts `skive` on your PATH from this repo — `mise run update` (or the uv
command again) after pulling. The venv carries rich (prompts/tables) plus the
beets stack (tagging) and loguru.

Non-Python prerequisites.

Via mise — pinned in `mise.toml`, arrive with `mise install`:

- `seconv` — Subtitle Edit's headless converter, the subtitle-pass engine
- `ffmpeg` — conda-forge build from the mise registry; provides `ffprobe`
  (subtitle note + subtitle pass) and `ffmpeg`

By hand:

- `makemkv` (AUR) with a MakeMKV key in `~/.MakeMKV/settings.conf` as
  `app_Key = "..."` — the free beta key rotates every 30 days
- `cyanrip` (AUR) for CD rips
- `flac` (provides `metaflac`)
- `lsdvd` — the DVD fast-scan gate: reads title durations straight off the
  device; without it every DVD silently falls back to the slow full scan
- `mkvtoolnix` (provides `mkvmerge`, the backfill's strip step)
- `tesseract` with `tesseract-data-eng` and `tesseract-data-dan` — OCR for the
  subtitle pass; `tesseract --list-langs` must show `eng` and `dan`
- `rsync` for `sync-sd`
- NFS mounts reachable (see below)
- for DVD naming: a TMDB API key

Arch, in two commands (`yay` works in place of `paru`):

```bash
sudo pacman -S --needed flac lsdvd mkvtoolnix-cli rsync tesseract tesseract-data-eng tesseract-data-dan
paru -S --needed makemkv cyanrip
```

### Environment variables

| Variable | Meaning |
|---|---|
| `NFS_VIDEOS` | NFS export mounted at `/mnt/videos` on demand (e.g. `192.168.103.4:/export/movies`) |
| `NFS_MUSIC` | NFS export mounted at `/mnt/music` on demand (e.g. `192.168.103.4:/export/music`) |
| `TMDB_API_KEY` | TMDB API key (movie/TV naming) |
| `RIP_MINLENGTH` | title-scan minimum length in seconds (default `120`) |
| `MUSIC_MOUNT` | override the music mountpoint (default `/mnt/music`) |
| `VIDEOS_MOUNT` | override the videos mountpoint (default `/mnt/videos`) |

Keys come from the process environment, never from `.env`. NFS mounts happen
on demand and are never unmounted by skive.

### Migrating an old share

A share staged with the old layout (flat `rip/movies/*.mkv`, `library/TV/`,
flat `rip-DONE/`) is re-treed by one script:

```bash
python scripts/migrate_layout.py                # dry-run: prints every move it would make
python scripts/migrate_layout.py --apply        # execute the moves
python scripts/migrate_layout.py --mock         # rehearse on an empty-file copy in /tmp
python scripts/migrate_layout.py --root /mnt/videos   # explicit root (default: $VIDEOS_MOUNT)
```

Run the dry-run first and read the move list. `--mock` builds a structure-only
mirror of the share under /tmp (empty files, no media bytes), applies the full
migration there, and prints the tree to inspect — a free end-to-end rehearsal. The script is idempotent — a
re-run on a migrated tree reports zero moves. Anything it cannot classify is
reported and left in place, never touched: junk labels (`B1_t00.mkv`), names
that lost their title year, a bare ` - Extra NN` file in a flat context the
library cannot type (a flat extra follows its title's home — movie or show —
so only a title homed in neither, or in both, of the movie and TV homes
leaves the extra undecidable), and any path that would traverse a
symlink. Old text `rip.log` files are left in place, unread.

Names that lost their title year are not junk: they are rips whose rename
never wrote the `(Year)`. `scripts/backfill_years.py` completes that rename
— per title group it runs the same interactive TMDB pick the rip flow uses,
then renames each file in place to the canonical `<Name (Year)>` name
(glued MakeMKV disc tags dropped, `_tNN` kept, groups landing on one name
become `.pN` parts; a TMDB entry with no release date asks for the year by
hand — Enter skips the group). Dry-run by default; after `--apply`, re-run the
migration to re-tree them.

```bash
python scripts/backfill_years.py               # dry-run: pick per title, print the plan
python scripts/backfill_years.py --apply       # rename in place, then re-run migrate_layout.py
```

After `--apply`, rescan the Jellyfin library once. Jellyfin keeps its single
root `/mnt/videos/library`, so the split into `library/movies/` and
`library/tv/` costs one rescan, not a reconfigure.

## Layout on the NAS

```text
/mnt/videos/
  rip/<type>/<Name (Year)>/         staging: one folder per movie or show
    d{disc}/                        the ripping drive's subdir while a rip runs (parallel drives never collide on junk labels)
  library/movies/<Movie (Year)>/    finished movie encodes + extras/
  library/tv/<Show (Year)>/         Season NN/ (season 0 = Specials/) + extras/
  lossless/<type>/<Name (Year)>/    where transcode --keep parks lossless sources (verify, then clean by hand)
  issues/<type>/<Name (Year)>/      quarantine for failed transcodes: the source + that run's log lines and raw log
  logs/rip.jsonl                    the run log, appended by both machines
  logs/raw/<run>.jsonl              one run's raw tool output (deleted on a clean exit)
```

`<type>` is `movies` or `tv` — the first path segment under `rip/` is what
tells transcode a file is TV. Staged names: `<Movie (Year)>_tNN.mkv`,
`_tNN.pN.mkv` (split-film half), `<Movie (Year)> - Extra NN[.pN].mkv`
(extras), `<Show (Year)>_S01E02.mkv`, `_S01E01-E02.mkv` (double). Finished
files are promoted up from `d{disc}` into the title folder before transcode
sees them.

Point Jellyfin at `/mnt/videos/library` — the single library root.

## Ripping video

Both `rip movie` and `rip tv` share the same behavior:

1. **Burned-media gate** — recordable media (DVD±R/RW, BD-R) refuses the rip
   unless you verified the disc by eye and pass `--verified`. Thrift-store
   discs are often bootlegs; genuinely-original recordable releases exist
   (e.g. Warner Archive DVD-R).
2. **Fast scan first** — a pressed DVD or Blu-ray is read in seconds, not
   minutes: `lsdvd` straight on the device (no mount, no sudo) or `ffprobe
   bluray:`. Any failure, or two titles within ±1s (decoy playlists),
   escalates to the full MakeMKV scan. Runs with `-T` or `--extras` start on
   the full scan.
3. **Junk purge** — stale junk-labeled leftovers (`B1_t00.mkv`) from a failed
   earlier session are deleted before the rip — scoped to this title's
   staging tree, never a sibling title or another drive's.
4. **Online gate** — TMDB must answer before anything spins.
5. **TMDB search** — the disc's volume label searches TMDB automatically and
   the results table appears; pick a hit, or `s` drops to a typed search. An
   empty result re-prompts typed, `q` aborts. `--tmdbid` (bare id, `tv:N`, or
   a pasted themoviedb.org URL) skips the search.
6. **Staging** — the title folder `rip/<type>/<Name (Year)>/` is created only
   after you pick the title at the TMDB prompt, with `d{disc}` inside (the
   ripping drive).
7. **Rip** — one `makemkvcon` call per title at `--minlength=$RIP_MINLENGTH`
   (a fast-scan run rips all titles in one call). A failed title never kills
   the run: the worst return code is collected and becomes the exit code.
8. **Promotion + rename** — finished files move up from `d{disc}` into the
   title folder (a name collision leaves the file in place with a note), then
   junk labels become `<Title> (Year)_tNN[.pN].mkv` (titles),
   `<Title> (Year) - Extra NN[.pN].mkv` (extras), or
   `<Show (Year)>_SNNENN[-ENNN].mkv` (episodes).
9. **Done** — a clean rip (exit code 0) just finishes; eject and swap
   discs by hand.

`-T/--titles` takes `all` (the default), a single number, a range `N-M`, or a
comma list like `0,5-7`. Bare `-T` (no value) opens an interactive menu to
check the titles you want — space toggles a box, Enter rips the checked ones,
`↑/↓` or `j/k` move, `q` quits, and every box starts checked. A stray
`disc:N` is rejected before the drive spins.

### rip movie

```bash
skive rip movie                # rip all titles
skive rip movie -T 0,5-7       # rip a title spec
skive rip movie -T             # menu to pick titles
skive rip movie --extras       # rip the 2nd disc of a set as extras
skive rip movie --part 1       # then --part 2 on the other disc (split film)
skive rip movie --verified     # override the burned-disc refusal
```

The volume label prefills the TMDB search; the pick names the rip. Audio is the
film's original language; subtitles keep Danish when present, else English.

**Multi-disc sets**, two kinds:

- A **film split across discs** — rip each disc with `--part N`, picking the
  same movie at each prompt. Halves stage as `_tNN.pN.mkv`; transcode merges
  the main feature's parts in order into one movie.
- An **extras disc** — rip with `--extras`. Files stage as
  `<Movie (Year)> - Extra NN.mkv` and file under the movie's `extras/`. A
  second extras disc rolls past the existing numbers.

### rip tv

```bash
skive rip tv                   # pick the show, season, then confirm the table
skive rip tv --extras          # a show's extras disc (no season prompt)
skive rip tv --tmdbid tv:68357 # skip the search
```

Confirm the show at the results table (the volume label prefills the search),
then the season (Enter = 1, `0` = specials). skive scans the disc and rips
all titles by default — a bare `-T` opens the title menu first, or a `-T`
spec narrows straight to those titles. Titles under the TV floor
(`max(RIP_MINLENGTH, 600)` seconds) drop entirely, and a title whose duration
matches the sum of two to six others is a play-all: it always rips as an
extra.

One prompt then states which episodes this disc carries, pre-filled with the
first episode not already staged and one episode per eligible title — Enter
takes it. Titles are assigned to that stated range in disc order; durations
never choose, they grade: the table shows a per-title ratio audit (disc ÷
TMDB runtime, green ~0.92–1.04 — PAL discs run ~4–5% short). Episodes
already staged for the season release their numbers, so a second disc
prefills the next episodes. The confirm table (title order: duration, size,
audit, assignment) is the single place to correct the map: Enter rips as
proposed, fix rows like `3=7`, `1=1-2`, or `2=e` re-point one title at a
time, `n` re-asks the episode range, `c` lists the show's staged files and,
after a y/N, deletes them (the remedy when an earlier run mis-filed
something), and `q` aborts before the drive spins. A title left outside the
stated range lists as `→ extra`.

`--part` is movie-only: episodes never merge. An aired special is not an
extra — it is a season 0 episode through the normal flow.

## Ripping music

### rip music

```bash
skive rip music 103            # rip with the drive's sample offset
skive rip music 6 -N           # skip the MusicBrainz lookup
skive rip music 6 -R 2         # force a re-rip of track 2
```

`offset` is your drive's CD sample offset, required (see [Drives](#drives));
any further arguments pass straight through to cyanrip. If cyanrip's
MusicBrainz lookup matches several releases, skive lists them, asks for a
number, and re-runs with `-R <n>`.

Rips land in `/mnt/music/rip/` (mounted on demand from `NFS_MUSIC`). A clean
rip just finishes — swap discs by hand.

### tag

```bash
skive tag
```

Runs beets over `/mnt/music/rip/`: MusicBrainz metadata, cover art, Last.fm
genres, ReplayGain, then moves the tagged FLAC into `/mnt/music/library/`.
The originals stay in `rip/` — clean that by hand when done. Rip several
discs, then `tag` the batch.

### sync-sd

```bash
skive sync-sd                  # auto-detect the card
skive sync-sd /media/USER/CARD # skip detection, use this mountpoint
```

Mounts `/mnt/music`, then copies `/mnt/music/library/` onto the card with
`rsync -r --size-only` — a copy, the source stays. Size-only works on any card
filesystem (FAT/exFAT choke on `-a`'s timestamps), and re-runs skip files
already on the card.

It auto-detects a mounted removable device, or one auto-mounted under
`/media` or `/run/media` (catches internal SD readers). If none is mounted, it
lists the removable disks (`sdx`/`mmcblk`) and mounts the one you pick. A card
this run mounted is unmounted again on exit — success, Ctrl-C, or failure. A
card that was already mounted stays mounted.

## Drives

AccurateRip needs the drive's CD sample offset, in samples:

- Black Blu-ray drive: `103`
- Sandberg: `6`

MakeMKV enumerates drives through `/dev/sg*` (SCSI-generic), not `/dev/sr*`.

- If it reports no drives: `sudo modprobe sg` (persist with
  `echo sg | sudo tee /etc/modules-load.d/sg.conf`).
- If it sees drives but cannot open them: add yourself to the `cdrom` group
  and log in again.
- Unmount the disc before you rip.

## Logging

Every command appends JSON lines to `logs/rip.jsonl` on the videos share —
one object per line, `{ts, run, script, software, name, phase, status,
elapsed_s}` — one line per phase plus a total on exit. `run` is a short UUID
minted per invocation; `status` carries `started`, `ok`, `FAIL(rc)`,
`interrupted`, or note text. Both machines append to the same file over NFS
(`tag` is not logged).

Each run's raw tool output is captured to `logs/raw/<run>.jsonl`
(`{ts, run, tool, line}`). A clean exit deletes it; a failure or interrupt
keeps it. When transcode quarantines a source to `issues/`, that run's log
lines and raw log are copied in beside it, so everything about one failure
sits in one folder.

Verbosity (`-v`, `-vv`) gates the console only — both logs are always
written. Old text `rip.log` files are left in place and read by nothing.

## Windows transcode box

Transcoding happens on a separate Windows machine; ripping stays on Linux.
The whole Windows install is two files, `windows/transcode.py` +
`windows/subs.py` (kept side by side) — stdlib-only Python, no skive
imports, runs on a clean `python.exe`.

### Install

1. `winget install Python.Python.3` (add `python` to PATH).
2. Install **HandBrake** (the CLI must be reachable), **MKVToolNix**
   (`mkvmerge.exe`), and **ffmpeg** (`ffprobe.exe`, `winget install
   Gyan.FFmpeg`). Each is found on PATH, else under its default Program
   Files dir.
3. For the subtitle pass: install **Tesseract** (`winget install
   UB-Mannheim.TesseractOCR`; select English and Danish in the installer)
   and **SeConv** (the `SeConv-Windows-x64.zip` from the Subtitle Edit
   releases; put `seconv.exe` on PATH).
4. Map the NAS share: `net use Z: \\nas\videos` (or any drive letter). Copy
   `windows/transcode.py` and `windows/subs.py` to the box, same folder.
5. Open an SSH session and run:

```powershell
python transcode.py                    # transcode everything staged
python transcode.py --keep             # park sources in lossless\<type>\<Name (Year)> instead of deleting
python transcode.py --retry            # re-run items from the issues folder instead of rip staging
python transcode.py "X2 (2003)"        # only movies whose staged name starts with the prefix
python transcode.py -e nvenc           # NVIDIA GPU encode (qsv/vce for Intel/AMD)
python transcode.py -v                 # + HandBrake/mkvmerge error lines (-vv = raw passthrough)
```

`$env:VIDEOS` overrides the default `Z:\` root (drive letter or UNC both
work). An SSH session does not inherit desktop drive mappings, so a missing
drive fails loudly with a `net use` hint — it never silently scans "no MKVs".

### What it does

- Scans `rip\` (or `issues\` with `--retry`). The first path segment under
  the root decides the type — `tv` (or legacy `TV`) is TV, anything else a
  movie. Titles rest at either depth — promoted in the title folder
  (`rip\movies\Name (Year)\`) or fresh in its `d{disc}` subdir — and one
  walk finds both. A name without a `(YYYY)` tail is a rip in progress or a
  failed rename — always skipped.
- **Movies**: the `_tNN` group with the most bytes is the main feature (a
  split main's `.pN` parts sum; tie goes to the lowest number). Parts merge
  in order via `mkvmerge` — but only when their track sets match; a
  mismatched set quarantines every part unmerged. The merged feature encodes
  as one movie into `library\movies\<Movie (Year)>\<Movie (Year)>.mkv`.
  Extras never merge; they file under the movie's `extras\` keeping their
  `_tNN`/`- Extra NN`/`.pN` names.
- **TV**: no main feature, no merge. Episodes file as
  `library\tv\<Show (Year)>\Season NN\<Show (Year)> - S01E02.mkv`; season 00
  goes to `Specials\`; TV extras under the show's `extras\`.
- **Encode**: `HandBrakeCLI` re-encodes video only to H.265 at the source's
  native resolution — never upscaled (ffprobe picks the preset: 480p/576p
  for DVD, 1080p for Blu-ray, 4K for UHD; DVD also gets detelecine +
  decomb). Audio passes through (`--all-audio --aencoder copy`, nothing
  burned in). GPU encoders add `--encoder <gpu>_h265 --quality 28`.
- **Subtitle pass**: main features and TV episodes get their eng/dan
  subtitle tracks extracted + OCR'd (`seconv`, inline) into SRT sidecars
  beside the encode (`<base>.<lang>[.forced|.sdh].srt`) and the encode
  carries **zero** subtitle tracks (`--subtitle-lang-list zzz`, a language
  no track matches). Extras keep every track embedded — no pass, no
  sidecars. A failed pass ships the encode subless and quarantines the
  lossless source to `issues\<type>\<Name (Year)>\` together with that
  run's log lines and raw log; a split main whose `.pN` parts have
  mismatched track sets never merges — all parts quarantine instead.
- Each file is pulled to a local temp dir first so it encodes off the
  network share.
- **`--keep`**: moves lossless sources to `lossless\<type>\<Name (Year)>\`
  instead of deleting, and never clobbers an existing file there (warns and
  leaves the source).
- **`--retry`**: re-runs items from `issues\`. Success ships and archives
  normally and moves the source out; failure stays quarantined
  (re-quarantine is a no-op).
- Ctrl+C is a graceful interrupt: the child is stopped, the partial temp
  output is deleted, the source stays where it is, exit 130.
- Same JSONL logs as the Linux side (run log + raw log, through the share
  root's `logs\`), always written; `-v`/`-vv` gate the console only.
- Exit code is 0 on a clean run, 1 if any encode/merge failed or a prefix
  matched nothing.

### Library backfill (one-off)

The existing library predates the inline pass. `scripts/backfill.py`
converts it in place on Linux with the same engine and policy: per file,
OCR to sidecars, then a verified `mkvmerge --no-subtitles` strip (temp
beside the original, checked, atomic replace). It probes its tools at
startup (`seconv`, `tesseract` with `eng`+`dan`, `mkvmerge`, `ffprobe`),
skips `extras/` trees and finished files, never strips a file whose
sidecars are missing, and leaves the retry work list in
`<library>/backfill-report.txt`. Expect an overnight-sized run.

```bash
python scripts/backfill.py                # /mnt/videos/library ($VIDEOS/library)
python scripts/backfill.py /some/test/dir # a small trial first
```

## Development

```bash
uv run pytest     # full suite, hardware/network fully stubbed
uv run skive --help
```

Domain vocabulary lives in `CONTEXT.md`; the full rewrite spec in `SPEC.md`.

## Project layout

```text
cd-rip-stack/
├── mise.toml            # tools: python 3.13, uv + install/update tasks
├── pyproject.toml       # deps: rich, beets, pylast, pillow, beetcamp, loguru; scripts: skive
├── README.md            # this file — Linux setup + the Windows install spec
├── CONTEXT.md
├── SPEC.md
├── scripts/
│   ├── backfill.py      # one-off library subtitle backfill
│   └── migrate_layout.py  # one-shot re-tree of the videos share (dry-run by default)
├── skive/
│   ├── main.py          # argparse dispatch, top-level -v/-vv, interrupt install
│   ├── common.py        # JSONL run/raw logs, NFS mount-on-demand, interrupt machinery, title-spec parser
│   ├── scan.py          # disc sniff, full scan and parsers, both fast scans, ambiguity, title menu
│   ├── makemkv.py       # profile injection, rip loop + status block, junk purge, renames, promotion
│   ├── rip_common.py    # shared preamble: mount → gates → TMDB pick → staging tree; interrupt exit
│   ├── tv_match.py      # pure assignment math: floor, play-alls, range, fixes, audit
│   ├── tmdb.py          # search loop, season fetch, id/URL parse
│   ├── rip_movie.py
│   ├── rip_tv.py        # interactive TV flow: prompts, confirm table, renames, staging
│   ├── rip_music.py
│   ├── tag.py
│   └── sync_sd.py
├── config/
│   ├── beets-config.yaml
│   └── custom.mmcp.xml
├── tests/               # pytest: matcher fixtures, naming, title-spec, JSONL logs, fast scans, migration, transcode rules
└── windows/
    ├── transcode.py     # standalone, stdlib-only — the Windows batch
    └── subs.py          # shared subtitle pass (transcode + backfill import it)
```
