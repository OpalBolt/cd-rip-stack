# skive — rewrite spec

One Python app replaces the Nix flake and the `lib/*.sh` pile. Five commands a human types. Everything else is a helper behind them. KISS wins every trade-off in this document.

The domain terms are defined in `CONTEXT.md`. This spec uses that vocabulary.

## Why

The current stack grew into eleven shell scripts, a menu nobody uses, a Nix flake, and a PowerShell port. The logic that matters (episode assignment, staged naming) hides inside 600 lines of bash with inline parsers. The rewrite keeps every earned behavior and moves it into one small, testable Python package.

## The command surface

```text
skive [-v|-vv] rip movie [-T SPEC] [--part N] [-E] [--tmdbid ID|URL] [-V] [-d N]
skive [-v|-vv] rip tv    [-T SPEC] [-E] [--tmdbid tv:N|URL] [-V] [-d N]
skive [-v|-vv] rip music [offset] [cyanrip flags…]
skive tag
skive sync-sd   [mountpoint]
skive search    QUERY
```

- The `rip` subcommand is the declared type. No mid-flow movie-or-TV prompt.
- `-v`/`-vv` sit on the top-level parser, so they apply to every subcommand (`skive -v rip movie`, not `skive rip movie -v`). See Verbosity.
- `-T/--titles` is `all` (default), a number, a range `N-M`, or a comma list `0,5-7`. Bare `-T` (no value) opens an interactive checkbox menu: space toggles a box (or the "Rip all titles" header), Enter rips the checked titles, `↑/↓` or `j/k` move, `q` quits, and every box starts checked. A stray `disc:N` in a title spec is rejected before the drive spins.
- `-d/--disc` names the drive (default 0), so two discs can rip at once. The fast read addresses the device the drive report names, not a `disc:N → /dev/srN` guess that can point at the other drive.
- `-E/--extras` rips a bonus disc; `-V/--verified` overrides the burned-media gate.
- `--part` exists on movie only. TV episodes never merge.
- One executable, installed once: `uv tool install --editable .` puts `skive` on PATH. No `python cdrip/...` ever.
- Arguments parse with argparse. The interactive layer is `rich` (tables, prompts). These are the only UI dependencies.

## Destinations

| Path | Role |
|---|---|
| `/mnt/videos/rip/<type>/<Name (Year)>/d{disc}/` | Staging while a drive rips — `d{disc}` is the ripping drive, so parallel drives never collide on junk labels |
| `/mnt/videos/rip/<type>/<Name (Year)>/` | Resting staging — promotion moves finished files up out of `d{disc}` |
| `/mnt/videos/library/movies/<Movie (Year)>/` | Finished movie encodes |
| `/mnt/videos/library/tv/<Show (Year)>/[Season NN\|Specials]/` | Finished TV encodes; season 0 = `Specials`; `extras/` beside the movie or show |
| `/mnt/videos/lossless/<type>/<Name (Year)>/` | Where transcode `--keep` parks lossless sources (verify, then purge by hand) |
| `/mnt/videos/issues/<type>/<Name (Year)>/` | Quarantine for failed transcodes: the source plus that run's log evidence |
| `/mnt/videos/logs/rip.jsonl` | The run log — one JSON line per phase, appended by both machines |
| `/mnt/videos/logs/raw/<run>.jsonl` | One run's raw tool output; deleted on a clean exit |
| `/mnt/music/rip/<source>/<album>/` | Music staging by source (`cd`, `bandcamp`, `other`) |
| `/mnt/music/library/` | Beets-sorted music library |
| `/mnt/music/music.db` | Beets library database |
| `/mnt/music/logs/import.log` | Beets import log |

`<type>` is `movies` or `tv`. The first path segment under `rip/` is what tells transcode a file is TV, at either depth — the title folder's promoted files or a fresh `d{disc}` rip.

Jellyfin points at the single library root `/mnt/videos/library`. Nothing else in the tree is a library root.

NFS mounts happen on demand, as today: `NFS_VIDEOS` and `NFS_MUSIC` name the exports, a mount helper mounts them if needed and never unmounts. Environment names stay as they are (`NFS_VIDEOS`, `NFS_MUSIC`, `MUSIC_MOUNT`, `RIP_MINLENGTH`, `TMDB_API_KEY`, `VIDEOS_MOUNT`). Keys come from the process environment, never from `.env`.

Migration from the old flat trees (`library/` at its root, `library/TV/`, flat `rip-DONE/`, flat staging) is a script, not a hand `mv`: `scripts/migrate_layout.py` re-trees the share, dry-run by default. The README documents it.

## Shared rip behavior (movie + TV)

1. **Burned-media gate** — refuse recordable media (DVD±R/RW, BD-R) unless `--verified`. Thrift-store bootlegs are common; the escape hatch stays manual.
2. **Fast read first** — an early-killed `makemkvcon info` DRV line names the media, the volume label, and the device. A pressed DVD or Blu-ray with no `-T` and no `--extras` gates on the fast scan (ADR 0002): `lsdvd` straight on the device (no mount, no sudo) or `ffprobe bluray:`, titles as durations in seconds. Any fast-scan failure, or two titles within ±1s (duplicate/decoy playlists), escalates to the full `makemkvcon` scan. Flagged runs (`-T`, `--extras`) start on the full scan.
3. **Junk purge** — stale junk-labeled leftovers (`B1_t00.mkv`) are deleted before the rip. The purge covers exactly the title's staging tree: the title folder and its `d{disc}` subdirs — never a sibling title, never another drive's staging.
4. **Online gate** — TMDB must answer before anything spins.
5. **TMDB search with a volume-label prefill** — the disc's volume label (underscores → spaces, trailing DISC/SIDE set marker stripped) runs as the first query; a `rich` table of results appears, pick one. `s` at the table drops to a typed query, which re-prompts on typos and empty results. `q` aborts. `--tmdbid` (bare id, `tv:N`, or a pasted themoviedb.org URL) skips the search.
6. **Staging is per title** — the title folder (`rip/<type>/<Name (Year)>/`) is created only after the TMDB pick names it, with `d{disc}` inside. Preamble order: mount, sniff, gates, TMDB pick, mkdir, purge.
7. **Scan and rip at the same minlength** — the full scan and its rip both use `--minlength=$RIP_MINLENGTH` (default 120, configurable via `RIP_MINLENGTH`). MakeMKV renumbers titles by what survives the scan, so the table and the rip must see the same disc. Fast-scan runs skip MakeMKV's scan entirely.
8. **Profile injection** — `custom.mmcp.xml` ships a BASELINE selection string. `skive` copies the profile, replaces the BASELINE with the per-title language list (original-language audio plus Danish and English subtitles), and passes the copy to `makemkvcon`.
9. **Rip loop** — one `makemkvcon` call per title (a fast-scan run rips all titles in one call). A failed title must not kill the run: collect the worst return code, always rename what completed, exit with it at the end.
10. **Promotion** — finished files move from `d{disc}` up into the title folder before the rename. A name collision leaves the file in `d{disc}` with a note — never clobbered.
11. **Rename** — junk labels become `<Name (Year)>_tNN[.pN].mkv` (titles), `<Name (Year)> - Extra NN[.pN].mkv` (extras), or `<Show (Year)>_SNNENN[-ENNN].mkv` (episodes). A fast-scan TV run joins staged files back to scanner titles by duration (±1s) before the rename, and aborts rather than guess; movies and full-scan TV keep MakeMKV's own `_tNN` numbers.
12. **Subtitle note** — after the rip, `ffprobe` reads which subtitle languages landed in the staged MKVs. The note goes to stderr and the run log.
13. **Logging** — every run appends JSONL lines to the run log and its raw log (see Logging).
14. **Eject is manual** — a clean rip (exit 0) just finishes; the user swaps discs by hand. Auto-eject assumed `disc:N` equals `/dev/srN`, which opens the wrong tray with parallel drives.

## Verbosity

One integer level, set by `-v` (1) and `-vv` (2) — on skive's top-level parser, and on the transcoder's own parser. Level 0 is the default.

- **Level 0** — only skive's own console lines: headers, notes, tables, prompts, the status block. Tool output is captured, never shown, and replayed verbatim when the tool fails.
- **Level 1** — adds the tool messages worth babysitting: MakeMKV MSG lines, cyanrip progress, HandBrake and mkvmerge error lines.
- **Level 2** — raw passthrough of everything the tools emit.

Verbosity gates the console only. Both logs are always written.

## Logging (JSONL, replaces the eight-column text format)

- **Run log** — `logs/rip.jsonl` on the videos share, one JSON object per line, appended by both machines over NFS:

  `{ts, run, script, software, name, phase, status, elapsed_s}`

  `run` is a short UUID minted per invocation. `status` carries `started`, `ok`, `FAIL(rc)`, `interrupted`, or note text. One line per phase plus a total on exit; an interrupt writes the total itself, so `finish()` stays silent after it. The lone-phase exception is skive's alone: skive skips the total when a run has a single successful phase (that phase line would be duplicated), but the Windows transcoder always writes its total, even for a single-phase run. `tag` is not logged.
- **Raw log** — `logs/raw/<run>.jsonl`, one `{ts, run, tool, line}` object per verbatim tool output line. Deleted on a clean exit (return code 0); kept on failure or interrupt.
- **Evidence on quarantine** — when transcode quarantines an item to `issues/`, the run's own lines (selected by the `run` field) are appended to the item folder's `rip.jsonl`, and the raw log is copied in beside the source. Once per run and folder, never doubled on a `--retry` re-quarantine.
- **Concurrency** — every append opens, writes, and closes per line, the same discipline the old parallel-drive text log used, so concurrent runs (and the Windows box over NFS) interleave whole lines instead of clobbering each other.
- **Old text `rip.log` files** on disk are left in place and read by nothing.

## Status display

During a makemkvcon rip, a three-line persistent block sits under the static disc summary header (titles, sizes, audio, subs):

- **Line 1** — what is being ripped: the display title and the drive.
- **Line 2** — the detail: phase · title x/y · current/total size · disc total · ETA. A segment appears only when it is known, so a fast-scan run (no size preview) gets a shorter line instead of invented numbers.
- **Line 3** — the bar.

The block lives in a `rich` Live region that clears on exit; one static summary line (`done` or `failed (rc=N)`, title count, bytes) records the outcome, so no stale bar is left on screen. At `-v` and above, tool lines print above the block.

## Interrupt (Ctrl+C)

- **First Ctrl+C** — the child tool is signalled, waits up to five seconds, then is killed. The in-flight partial is deleted: every junk-labeled file from the current run in the title folder and its `d{disc}`. Completed, renamed titles are kept. The run log records `status=interrupted`. The process exits 130 with one summary line.
- **Second Ctrl+C** — instant exit, no cleanup, no traceback. The next run's junk purge is the safety net.
- **Prompts** — an interrupt at any interactive prompt (TMDB search, title menu, season, episode range, confirm table) exits with one line. An interrupt before the rip leaves nothing of ours behind, but a run that already started logs its closing line before exiting.
- **Music** — cyanrip gets the same signal-then-kill. cyanrip writes final-named FLACs straight into staging, so there is no partial of ours to purge; the run log records the interrupt and skive exits 130.
- **Windows transcode** — the child is stopped (Windows has no cross-process SIGINT: terminate, then kill). The partial temp output is deleted — the local copy, the merge output, the encode, the sidecar dir. The source stays where it is: sources move only on success. `status=interrupted`, exit 130.

## TV flow

1. Pick the show at the TMDB prompt, then the season (Enter = 1, 0 = specials). Episodes stage under `rip/tv/<Show (Year)>/d{disc}/`.
2. The gate scan is reused for the menu, the table, and the rip — no second scan. Rip all titles by default; a bare `-T` opens the title menu first, and a `-T` spec narrows the table to those titles. A fast-scan run rips all in one call and joins staged files to scanner titles by duration (±1s) at rename time; the full-scan path keeps MakeMKV numbers and rips per the table with no join.
3. **Eligibility** — titles under the TV floor (`max(RIP_MINLENGTH, 600)` seconds) drop entirely: not eligible, not in the table, not ripped. A title whose duration is within ±120s of the sum of two to six other eligible titles is a play-all; it always rips as an extra, whatever the stated range said.
4. **Stated range** — one pre-filled prompt: the first episode not already staged, one episode per eligible title. Enter takes it. Eligible titles are assigned to the stated range in disc order; the rest become extras. Episode identity is the stated range plus disc position — never a disc title number, never a duration. Durations grade the assignment, never make it: the table shows a per-title ratio audit (disc ÷ TMDB runtime, green ~0.92–1.04 — PAL discs run ~4–5% short). A staged double releases both its numbers — the show's staging folder is the state; nothing persists elsewhere.
5. **Confirm table** — printed in title-number order, columns: duration, size, audit, assignment. Enter rips as proposed. Fix rows (`3=7`, `1=1-2`, `2=e`) re-point one title at a time, apply left-to-right, clash-checked; a rejected row changes nothing and the table re-prints. `n` re-asks the episode range. `c` lists the show's staged files (episodes and extras), deletes them after a y/N, and refreshes the staged set — the in-flow reset for mis-filed staging. `q` aborts before the drive spins. Two notes fire when they apply: eligible titles that do not fill the stated range, and a range that runs past the season's last TMDB episode.
6. **No runtimes is not a special case** — when TMDB lists no episodes for the season, the range is still stated and the assignment still goes by disc position; only the audit column stays empty.
7. **`--extras`** — a bonus disc: no season, no table. Rips straight into the show's title folder as `<Show (Year)> - Extra NN`, rolling past existing extras. An aired special is not an extra; it is a season 0 episode through the normal branch.

The assignment math lives in `tv_match.py` (floor, play-all sums, range parsing, position assignment, fix rows, ratio audit) with its own pytest coverage, separate from the interactive flow in `rip_tv.py`.

## Music pipeline

Whipper is dropped. cyanrip is the only engine (secure rip, AccurateRip, drive offset — and it writes ReplayGain 2.0 true-peak tags at rip time). Music staging is `/mnt/music/rip/<source>/<album>/` where source is `cd`, `bandcamp`, or `other`.

1. `skive rip music [offset] [flags…]` mounts `/mnt/music` on demand, then runs cyanrip inside `/mnt/music/rip/cd/` with `-s <offset>` and any passed-through flags (`-N`, `-R x`, …). No local `~/media/audio` tree exists anymore. The expected layout is `<album>/` subfolders under `rip/cd/`, but this is unconfirmed until ticket 09; flat FLACs directly in `rip/cd/` also work until then. A clean exit just finishes; the user swaps discs by hand. Downloads are unzipped or copied into `rip/bandcamp/<album>/` or `rip/other/<album>/`.
2. When cyanrip exits 1 on "Multiple releases found", prompt for a release and re-run with `-R <n>`. That exit is not a failure.
3. The run log's name comes from the newest FLAC's ALBUM tag recursively under `rip/cd/`, and a note logs the folder it landed in.
4. `skive tag [cd|bandcamp|other]` runs beets on `/mnt/music/rip/<source>/`, moving and sorting straight into `/mnt/music/library/`. Without an argument, it files all three in order: `cd`, `other`, `bandcamp`. `cd` and `other` use normal MusicBrainz autotagging; `bandcamp` runs with `-A` (as-is). Beets DB (`/mnt/music/music.db`) and import log (`/mnt/music/logs/import.log`) live on the music share. Existing libraries can be seeded into the DB once via `beet import -A -C /mnt/music/library`. beets (plus `pylast`, `pillow`, `beetcamp` for the plugins) lives in this project's venv. The config is `config/beets-config.yaml`.

## sync-sd

Ported as-is: auto-detect a mounted removable device (or one auto-mounted under `/media` or `/run/media`), else list the unmounted removable disks and mount the pick, unmounting it again on any exit. An already-mounted card stays mounted. `rsync --size-only` copies `/mnt/music/library/` to the card — size-only works on any card filesystem. A mountpoint argument skips all detection.

## Windows transcode

`windows/transcode.py` — one standalone file, stdlib only, copied to the box by hand (`windows/subs.py` beside it, shared with the backfill). Python 3 via winget is its only new prerequisite versus the old PowerShell script. It duplicates the run-log and sanitize helpers on purpose: no skive imports, runs on a clean `python.exe`.

- Reads `<VIDEOS>\rip\` (`Z:\` default, `VIDEOS` env overrides) — or `<VIDEOS>\issues\` with `--retry`. The first path segment under the input root decides the type: `tv` (or legacy `TV`) is TV, anything else a movie. Titles rest at either depth — promoted in the title folder, or fresh in its `d{disc}` — and one walk finds both.
- Junk guard: a name without a `(YYYY)` tail is a rip in progress or a failed rename — never encode it.
- Movies: the `_tNN` group with the most bytes is the main feature (a split main's `.pN` parts sum; tie to the lowest number). Parts merge in order via `mkvmerge` — but only when their track sets match; a mismatched set quarantines every part unmerged. The merged feature encodes as one movie into `library\movies\<Movie (Year)>\<Movie (Year)>.mkv`. Extras never merge; they file under the movie's `extras\`, keeping their `_tNN`/`- Extra NN`/`.pN` names.
- TV: no main feature, no merge. Episodes file as `library\tv\<Show (Year)>\Season NN\<Show (Year)> - S01E02.mkv`, season 00 as `Specials`. TV extras file under the show's `extras\`. The first path segment decides the type — never the presence of episode files.
- `HandBrakeCLI` re-encodes video only to H.265 at the source's native resolution, never upscaled (ffprobe picks the preset; DVD also gets detelecine + decomb). Audio passes through with a copy mask and an ac3 fallback. `-e nvenc` (or `qsv`/`vce`) switches to GPU flags at quality 28.
- Subtitle pass: main features and TV episodes get their eng/dan tracks extracted + OCR'd into SRT sidecars beside the encode (`<base>.<lang>[.forced|.sdh].srt`), and the encode carries zero subtitle tracks. Extras keep every track embedded. A failed pass ships the encode subless and quarantines the lossless source to `issues\<type>\<Name (Year)>\` — with that run's log lines and raw log copied in beside it (see Logging). A split main whose parts have mismatched track sets never merges; all parts quarantine instead.
- Each file is pulled to a local temp dir first so it encodes off the network share.
- **`--keep`** moves lossless sources to `lossless\<type>\<Name (Year)>\` instead of deleting, never clobbering an existing file there (warns and leaves the source).
- **`--retry`** re-runs items from `issues\` as the input tree. Success ships and archives normally and moves the source out; failure stays quarantined (re-quarantine is a no-clobber no-op). No scheduler, no automatic retry — the operator runs it.
- Verbosity `-v`/`-vv` as in skive (see Verbosity). Both JSONL logs are always written, through the share root's `logs\` directory.
- Ctrl+C follows the Interrupt contract above.
- Exit code is 0 on a clean run, 1 if any encode/merge failed or a prefix matched nothing.

Linux never transcodes. `lib/transcode-video.sh` is dead code and goes.

## Project layout

```text
cd-rip-stack/
├── mise.toml            # tools: python 3.13, uv + install/update tasks
├── pyproject.toml       # deps: rich, beets, pylast, pillow, beetcamp, loguru; scripts: skive
├── README.md            # Linux setup + the Windows install spec, one document
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
│   ├── search.py        # ripped search: name walk over every on-disk root; the rip preflight lookups
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
├── tests/               # pytest: matcher fixtures, naming, title-spec, JSONL logs, interrupt,
│                        # fast scans, migration plan, transcode staging rules (imports windows/transcode.py by path)
└── windows/
    ├── transcode.py     # standalone, stdlib-only — the Windows batch
    └── subs.py          # shared subtitle pass (transcode + backfill import it)
```

mise supplies Python and uv versions, plus two lifecycle tasks (`install`,
`update`) that wrap `uv tool install --editable .` (`update` ff-pulls first).
Still no per-command aliases — a mise task per skive command would be a
second door to the same room.

Non-Python prerequisites, installed once by hand: `makemkv` (AUR, key in `~/.MakeMKV/settings.conf`), `cyanrip` (AUR), `flac` (metaflac), `lsdvd` (the DVD fast scan), `ffmpeg` (ffprobe), `rsync`, NFS mounts reachable. HandBrake and MKVToolNix are Windows-side only now.

## Deleted

`flake.lock`, `justfile`, every file in `lib/`, `windows/transcode-video.ps1`, `windows/test-transcode.sh`, `README_WINDOWS.md`, `.venv/`, `.pytest_cache/`. Git history keeps them if anything needs exhuming.
