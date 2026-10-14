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
Both share one NAS mount: `rip/` stages the lossless MKVs, `library/` holds the
encodes. `windows/transcode.py` is the whole Windows install, copied over by
hand.

## Linux setup

Requires Python 3.13 and uv. If you use mise:

```bash
mise install          # reads mise.toml: python 3.13, uv
mise exec -- uv tool install --editable .
```

or, without mise, install uv by its own means and then:

```bash
uv tool install --editable .
```

That puts `skive` on your PATH from this repo — re-run it after pulling. The
venv carries rich (prompts/tables) plus the beets stack (tagging) and loguru.

Non-Python prerequisites, installed once by hand:

- `makemkv` (AUR) with a MakeMKV key in `~/.MakeMKV/settings.conf` as
  `app_Key = "..."` — the free beta key rotates every 30 days
- `cyanrip` (AUR) for CD rips
- `flac` (provides `metaflac`)
- `ffmpeg` (provides `ffprobe`, used for the subtitle note)
- `rsync` for `sync-sd`
- NFS mounts reachable (see below)
- for DVD naming: a TMDB API key

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

### One-time migration

If you are upgrading from the old bash scripts, move the existing staging into
the new subdirectories:

```bash
mv /mnt/videos/rip/TV /mnt/videos/rip/tv
mv /mnt/videos/rip/*.mkv /mnt/videos/rip/movies/
```

The staging subdirectory is what tells transcode a file is TV — hence `tv/`.

## Layout on the NAS

```text
/mnt/videos/
  rip/movies/      movie staging: <Movie (Year)>_tNN.mkv, _tNN.pN.mkv (split-film half), <Movie (Year)> - Extra NN[.pN].mkv (extras)
  rip/tv/          TV staging: <Show (Year)>_S01E02.mkv, _S01E01-E02.mkv (double), <Show (Year)> - Extra NN.mkv (extras)
  library/         point Jellyfin here: <Movie (Year)>/<Movie (Year)>.mkv + extras/
  library/TV/      <Show (Year)>/Season NN/<Show (Year)> - S01E02.mkv, season 0 as Specials/, plus extras/
  rip-DONE/        where transcode --keep parks lossless sources (verify, then delete)
```

The library layout is unchanged: `library/<Movie (Year)>/`, TV under
`library/TV/<Show (Year)>/Season NN/` (season 0 = `Specials`), extras in
`extras/` beside the movie or show.

## Ripping video

Both `rip movie` and `rip tv` share the same behavior:

1. **Burned-media gate** — recordable media (DVD±R/RW, BD-R) refuses the rip
   unless you verified the disc by eye and pass `--verified`. Thrift-store
   discs are often bootlegs; genuinely-original recordable releases exist
   (e.g. Warner Archive DVD-R).
2. **Junk purge** — stale junk-labeled leftovers (`B1_t00.mkv`) from a failed
   earlier session are deleted before the rip.
3. **Online gate** — TMDB must answer before anything spins.
4. **TMDB search** — the disc's volume label searches TMDB automatically and
   the results table appears; pick a hit, or `s` drops to a typed search. An
   empty result re-prompts typed, `q` aborts. `--tmdbid` (bare id, `tv:N`, or
   a pasted themoviedb.org URL) skips the search.
5. **Rip** — one `makemkvcon` call per title at `--minlength=$RIP_MINLENGTH`.
   A failed title never kills the run: the worst return code is collected and
   becomes the exit code.
6. **Rename** — junk labels become `<Title> (Year)_tNN[.pN].mkv` (titles),
   `<Title> (Year) - Extra NN[.pN].mkv` (extras), or `<Show (Year)>_SNNENN[-ENNN].mkv` (episodes).

`-T/--titles` takes `all` (the default), a single number, a range `N-M`, or a
comma list like `0,5-7`. Bare `-T` (no value) opens an interactive menu to
check the titles you want — space toggles a box, Enter rips the checked ones,
`↑/↓` or `j/k` move, `q` quits, and every box starts checked. The drive is
always `disc:0`; a stray `disc:N` is rejected before the drive spins.

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
then the season (Enter = 1, `0` = specials).
skive scans the disc, rips all titles by default, and proposes a
title→episode table matched by duration against the season's TMDB runtimes —
a bare `-T` opens the title menu first, or a `-T` spec narrows straight to
those titles. Longest title first, each title claims the
nearest unused episode; a title close to two consecutive runtimes becomes a
double (`S01E01-E02`, one file). Episodes already staged for that season are
skipped, so a second disc proposes the next episodes. A title with no episode
left lists as `→ extra` — fix it at the table with a row like `3=7`, `1=1-2`,
or `2=e`; `q` aborts before the drive spins. When TMDB has no runtimes, skive
asks for the first episode number and numbers the titles in order.

`--part` is refused for TV: episodes never merge. An aired special is not an
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

Rips land in `/mnt/music/rip/` (mounted on demand from `NFS_MUSIC`).

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

Every command appends to `./rip.log` in the directory you run it from, one
line per phase plus a total on exit, in the same 8 columns as ever
(`start end script software name phase m:ss status`). `tag` is not logged.
Because rip and transcode run on different machines, `./rip.log` is per
machine.

## Windows transcode box

Transcoding happens on a separate Windows machine; ripping stays on Linux.
The whole Windows install is one file, `windows/transcode.py` — stdlib-only
Python, no skive imports, runs on a clean `python.exe`.

### Install

1. `winget install Python.Python.3` (add `python` to PATH).
2. Install **HandBrake** (the CLI must be reachable), **MKVToolNix**
   (`mkvmerge.exe`), and **ffmpeg** (`ffprobe.exe`, `winget install
   Gyan.FFmpeg`). Each is found on PATH, else under its default Program
   Files dir.
3. Map the NAS share: `net use Z: \\nas\videos` (or any drive letter). Copy
   `windows/transcode.py` to the box.
4. Open an SSH session and run:

```powershell
python transcode.py                    # transcode everything staged
python transcode.py -Keep              # park sources in rip-DONE instead of deleting
python transcode.py "X2 (2003)"        # only movies whose staged name starts with the prefix
python transcode.py -Encoder nvenc     # NVIDIA GPU encode (qsv/vce for Intel/AMD)
```

`$env:VIDEOS` overrides the default `Z:\` root (drive letter or UNC both
work). An SSH session does not inherit desktop drive mappings, so a missing
drive fails loudly with a `net use` hint — it never silently scans "no MKVs".

### What it does

- Scans `rip\movies` and `rip\tv` (the staging subdirectory decides the type,
  never the episode files' presence). A name without a `(YYYY)` tail is a rip
  in progress or a failed rename — always skipped.
- **Movies**: the `_tNN` group with the most bytes is the main feature (a
  split main's `.pN` parts sum; tie goes to the lowest number). Parts merge
  in order via `mkvmerge`, then encode as one movie into
  `library\<Movie (Year)>\<Movie (Year)>.mkv`. Extras never merge; they file
  under the movie's `extras\` keeping their `_tNN`/`- Extra NN`/`.pN` names.
- **TV**: no main feature, no merge. Episodes file as
  `library\TV\<Show (Year)>\Season NN\<Show (Year)> - S01E02.mkv`; season 00
  goes to `Specials\`; TV extras under the show's `extras\`.
- **Encode**: `HandBrakeCLI` re-encodes video only to H.265 at the source's
  native resolution — never upscaled (ffprobe picks the preset: 480p/576p
  for DVD, 1080p for Blu-ray, 4K for UHD; DVD also gets detelecine +
  decomb). Every audio and subtitle track passes through (`--all-audio
  --aencoder copy`, `--all-subtitles`, nothing burned in). GPU encoders add
  `--encoder <gpu>_h265 --quality 28`.
- Each file is pulled to a local temp dir first so it encodes off the
  network share.
- **`-Keep`**: moves lossless sources to `rip-DONE` instead of deleting, and
  never clobbers an existing file there (warns and leaves the source).
- Same 8-column `rip.log` lines as the Linux side, written UTF-8-safe.
- Exit code is 0 on a clean run, 1 if any encode/merge failed or a prefix
  matched nothing.

## Development

```bash
uv run pytest     # full suite, hardware/network fully stubbed
uv run skive --help
```

Domain vocabulary lives in `CONTEXT.md`; the full rewrite spec in `SPEC.md`.

## Project layout

```text
cd-rip-stack/
├── mise.toml            # tools: python 3.13, uv. Nothing else.
├── pyproject.toml       # deps: rich, beets, pylast, pillow, beetcamp, loguru; scripts: skive
├── README.md            # this file — Linux setup + the Windows install spec
├── CONTEXT.md
├── SPEC.md
├── skive/
│   ├── main.py          # argparse dispatch
│   ├── common.py        # rip.log, NFS mount-on-demand, title-spec parser
│   ├── tmdb.py          # search loop, season fetch, duration matcher
│   ├── makemkv.py       # disc info scan, rip loop, profile injection, rename
│   ├── rip_movie.py
│   ├── rip_tv.py
│   ├── rip_music.py
│   ├── tag.py
│   └── sync_sd.py
├── config/
│   ├── beets-config.yaml
│   └── custom.mmcp.xml
├── tests/               # pytest: matcher fixtures, naming, title-spec, transcode rules
└── windows/
    └── transcode.py     # standalone, stdlib-only — the Windows install
```
