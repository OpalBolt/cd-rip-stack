# skive — rewrite spec

One Python app replaces the Nix flake and the `lib/*.sh` pile. Five commands a human types. Everything else is a helper behind them. KISS wins every trade-off in this document.

The domain terms are defined in `CONTEXT.md`. This spec uses that vocabulary.

## Why

The current stack grew into eleven shell scripts, a menu nobody uses, a Nix flake, and a PowerShell port. The logic that matters (duration matching, episode maps, staged naming) hides inside 600 lines of bash with inline parsers. The rewrite keeps every earned behavior and moves it into one small, testable Python package.

## The command surface

```text
skive rip movie [-T SPEC] [--part N] [-E] [--tmdbid ID|URL] [-V]
skive rip tv    [-T SPEC] [-E] [--tmdbid tv:N|URL] [-V]
skive rip music [offset] [cyanrip flags…]
skive tag
skive sync-sd   [mountpoint]
```

- The `rip` subcommand is the declared type. No mid-flow movie-or-TV prompt.
- `-T/--titles` is `all` (default), a number, a range `N-M`, or a comma list `0,5-7`. Bare `-T` (no value) opens an interactive checkbox menu: space toggles a box (or the "Rip all titles" header), Enter rips the checked titles, `↑/↓` or `j/k` move, `q` quits, and every box starts checked. The drive is always `disc:0`; a stray `disc:N` is rejected before the drive spins.
- `-E/--extras` rips a bonus disc; `-V/--verified` overrides the burned-media gate.
- `--part` is refused for TV. TV episodes never merge.
- One executable, installed once: `uv tool install --editable .` puts `skive` on PATH. No `python cdrip/...` ever.
- Arguments parse with argparse. The interactive layer is `rich` (tables, prompts). These are the only UI dependencies.

## Destinations

| Path | Role |
|---|---|
| `/mnt/videos/rip/movies/` | Movie staging |
| `/mnt/videos/rip/tv/` | TV staging — the subdirectory is what tells transcode a file is TV |
| `/mnt/videos/library/` | Finished encodes (Jellyfin tree, unchanged) |
| `/mnt/music/rip/` | CD staging |
| `/mnt/music/library/` | Beets-sorted music library |

NFS mounts happen on demand, as today: `NFS_VIDEOS` and `NFS_MUSIC` name the exports, a mount helper mounts them if needed and never unmounts. Environment names stay as they are (`NFS_VIDEOS`, `NFS_MUSIC`, `MUSIC_MOUNT`, `RIP_MINLENGTH`, `TMDB_API_KEY`). Keys come from the process environment, never from `.env`.

The library layout does not change: `library/<Movie (Year)>/`, `library/TV/<Show (Year)>/Season NN/` (season 0 = `Specials`), `extras/` beside the movie or show.

One-time migration: move whatever sits in `rip/` and `rip/TV/` today into `rip/movies/` and `rip/tv/` by hand. The README documents the two `mv` commands.

## Shared rip behavior (movie + TV)

1. **Burned-media gate** — refuse recordable media (DVD±R/RW, BD-R) unless `--verified`. Thrift-store bootlegs are common; the escape hatch stays manual.
2. **Junk purge** — delete stale junk-labeled leftovers (`B1_t00.mkv`) from a failed earlier session in the target staging dir before the rip. A fresh session starts from an empty staging dir. This applies to both `rip/movies/` and `rip/tv/`.
3. **Online gate** — TMDB must answer before anything spins.
4. **TMDB search with a volume-label prefill** — the disc's volume label (from the DRV scan line, underscores → spaces) runs as the first query; a `rich` table of results appears, pick one. `s` at the table drops to a typed query, which re-prompts on typos and empty results. `q` aborts. `--tmdbid` (bare id, `tv:N`, or a pasted themoviedb.org URL) skips the search.
5. **Scan and rip at the same minlength** — `--minlength=120` (configurable via `RIP_MINLENGTH`). MakeMKV renumbers titles by what survives the scan, so the table and the rip must see the same disc.
6. **Profile injection** — `custom.mmcp.xml` ships a BASELINE selection string. `skive` copies the profile, replaces the BASELINE with the per-movie language list (original-language audio plus Danish and English subtitles), and passes the copy to `makemkvcon`.
7. **Rip loop** — one `makemkvcon` call per title. A failed title must not kill the run: collect the worst return code, always rename, exit with it at the end.
8. **Rename** — junk labels become `<Name (Year)>_tNN[.pN].mkv` (titles), `<Name (Year)> - Extra NN[.pN].mkv` (extras), or `<Show (Year)>_SNNENN[-ENNN].mkv` (episodes).
9. **Subtitle note** — after the rip, `ffprobe` reads which subtitle languages landed in the staged MKVs. The note goes to stderr and `rip.log`.
10. **`rip.log`** — the same 8 columns as today (`start end script software name phase m:ss status`), one line per phase, a total line on exit. Simple and resilient.

## TV flow (preserved 1:1)

1. Pick the show at the TMDB prompt, then the season (Enter = 1, 0 = specials). Episodes stage under `rip/tv/`.
2. Scan the disc once (reused for the media gate, menu, and rip). Rip all titles by default; a bare `-T` opens the title menu first, and a `-T` spec narrows the table to those titles.
3. **Candidates** — the season's TMDB runtimes, minus the episodes already staged in the show's staging dir. A staged double releases both its numbers. The staging dir is the state; nothing is persisted elsewhere.
4. **Duration matching** — greedy, titles longest-first. Each title claims the nearest-runtime unmatched episode.
   - A single competes within **600 s** of its runtime.
   - A consecutive pair competes while each half is within **300 s**.
   - If both fit, the nearer wins; a tie goes to the single.
   - A winning pair stamps a double episode: one file, `S01E01-E02`, never split.
   - A title with no claim becomes an extra.
5. **Confirm table** — printed in title-number order. Enter rips as proposed. Fix rows (`3=7`, `1=1-2`, `2=e`) re-point one title at a time, apply left-to-right, clash-checked; a rejected row changes nothing and the table re-prints. `q` aborts before the drive spins.
6. **Sequential fallback** — only when TMDB gives zero runtimes: ask the first episode number, number titles in order.
7. **`--extras`** — a bonus disc: no season, no table. Rips straight into the TV staging dir as `<Show (Year)> - Extra NN`, rolling past existing extras; a bare `-T` opens the title menu first (the only scan on this path). An aired special is not an extra; it is a season 0 episode through the normal branch.

The matcher self-tests port to pytest with the same fixtures: greedy order, nearest single, double-beats-single, 21:30-takes-single, leftovers become extras, featurette-too-far, staged exclusion.

## Music pipeline

Whipper is dropped. cyanrip is the only engine (secure rip, AccurateRip, drive offset — and it writes ReplayGain 2.0 true-peak tags at rip time, so nothing recomputes gain later; the beets `replaygain` plugin is deleted).

1. `skive rip music [offset] [flags…]` mounts `/mnt/music` on demand, then runs cyanrip inside `/mnt/music/rip/` with `-s <offset>` and any passed-through flags (`-N`, `-R x`, …). No local `~/media/audio` tree exists anymore.
2. When cyanrip exits 1 on "Multiple releases found", prompt for a release and re-run with `-R <n>`. That exit is not a failure.
3. The rip-log name comes from the newest FLAC's ALBUM tag.
4. `skive tag` runs beets on `/mnt/music/rip/`, moving and sorting straight into `/mnt/music/library/`. beets (plus `pylast`, `pillow`, `beetcamp` for the plugins) lives in this project's venv. The beets config moves to `config/beets-config.yaml` with the `replaygain` line removed; the plugin set is otherwise unchanged (fetchart, embedart, lastgenre, ftintitle, scrub, the, zero, musicbrainz, bandcamp).

## sync-sd

Ported as-is, the sixth command: auto-detect a mounted removable device (or one auto-mounted under `/media` or `/run/media`), else list the unmounted removable disks and mount the pick, unmounting it again on any exit. An already-mounted card stays mounted. `rsync --size-only` copies `/mnt/music/library/` to the card — size-only works on any card filesystem. A mountpoint argument skips all detection.

## Windows transcode

`windows/transcode.py` — one standalone file, stdlib only, copied to the box by hand. Python 3 via winget is its only new prerequisite versus the old PowerShell script. Full parity:

- Reads `<VIDEOS>\rip\{movies,tv}` (`Z:\` default, `VIDEOS` env overrides).
- Junk guard: a name without a `(YYYY)` tail is a rip in progress or a failed rename — never encode it.
- Movies: the `_tNN` group with the most bytes is the main feature (a split main's `.pN` parts sum; tie to the lowest number). Parts merge in order via `mkvmerge`. Extras never merge.
- TV: no main feature, no merge. Episodes file as `library\TV\<Show (Year)>\Season NN\<Show> - S01E02.mkv`, season 00 as `Specials`. TV extras file under the show's `extras\`. The staging subdirectory decides the type — never the presence of episode files.
- `HandBrakeCLI` re-encodes video only; every audio and subtitle track passes through. `-Encoder nvenc` switches to GPU flags.
- A positional prefix filters the batch (matches every part of a movie).
- `-Keep` parks sources in `rip-DONE` instead of deleting, never clobbering an existing file.
- Same 8-column `rip.log` lines, UTF-8-safe writing for PowerShell-era encodings, reachability check on the share root so a missing drive letter fails loudly.

Linux never transcodes. `lib/transcode-video.sh` is dead code and goes.

## Project layout

```text
cd-rip-stack/
├── mise.toml            # tools: python 3.13, uv. Nothing else — no task aliases.
├── pyproject.toml       # deps: rich, beets, pylast, pillow, beetcamp, loguru; scripts: skive
├── README.md            # Linux setup + the Windows install spec, one document
├── CONTEXT.md
├── SPEC.md
├── skive/
│   ├── main.py          # argparse dispatch
│   ├── common.py        # rip.log, NFS mount-on-demand, junk-label helpers, title-spec parser
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
├── tests/               # pytest: matcher fixtures, naming, title-spec, selection string,
│                        # transcode staging rules (imports windows/transcode.py by path)
└── windows/
    └── transcode.py
```

mise supplies Python and uv versions. Nothing more — a mise task per command would be a second door to the same room.

Non-Python prerequisites, installed once by hand: `makemkv` (AUR, key in `~/.MakeMKV/settings.conf`), `cyanrip` (AUR), `flac` (metaflac), `ffmpeg` (ffprobe), `rsync`, NFS mounts reachable. HandBrake and MKVToolNix are Windows-side only now.

## Deleted

`flake.nix`, `flake.lock`, `justfile`, every file in `lib/`, `windows/transcode-video.ps1`, `windows/test-transcode.sh`, `README_WINDOWS.md`, `.venv/`, `.pytest_cache/`. Git history keeps them if anything needs exhuming.
