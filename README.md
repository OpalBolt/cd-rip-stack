# skive

Rip CDs, DVDs, and Blu-rays to MKV and FLAC.

A two-box, hand-driven pipeline: a ripping machine pulls discs to lossless
MKV and FLAC on two NFS shares and files music into a beets-managed library;
a transcode box drains video staging into a Jellyfin-ready H.265 library with
SRT sidecars. Eight commands below; each docs page is one job.

## Quickstart — the ripping machine

```bash
git clone <this repo> && cd cd-rip-stack
mise run install    # skive on PATH, editable from this repo (or: uv tool install --editable .)
export TMDB_API_KEY=... NFS_VIDEOS=host:/export/videos NFS_MUSIC=host:/export/music
skive rip movie     # shares mount on demand; insert the disc and answer the prompts
```

Env keys, dependencies, drives and logs: [setup](docs/setup.md). The
transcode box is a separate build — [transcode](docs/transcode.md).

## Commands

| Command | One-liner (`--help`) | Docs |
|---|---|---|
| `skive rip movie` | rip movie | [video](docs/video.md) |
| `skive rip tv` | rip TV show | [video](docs/video.md) |
| `skive rip music` | rip CD | [music](docs/music.md) |
| `skive tag` | tag and file music | [music](docs/music.md) |
| `skive sync-sd` | sync music library to SD card | [music](docs/music.md) |
| `skive transcode` | transcode staged lossless MKVs into the Jellyfin library | [transcode](docs/transcode.md) |
| `skive validate` | report library findings (read-only, never changes anything) | [tools](docs/tools.md) |
| `skive search` | search selected on-disk roots for an already-ripped title | [tools](docs/tools.md) |

## The console

Every command prints ordinary lines that stay in scrollback. Each line starts
with a symbol: `✓` done, `·` routine, `!` warning, `✗` failure. Long commands
open with one line that states their scope, give each item a `▸` heading,
and redraw a footer of at most two lines (progress bar, ETA) under the scroll.
They end on a summary table and one result line.

```text
✗ 1 failed · 1 ok · 1h36m — see /mnt/videos/logs/rip.jsonl (run 93721d6e)
```

Piped output, `NO_COLOR` and `TERM=dumb` give plain lines with no escape
codes. Each line starts with a padded word instead of a symbol:

```text
info  1/3  Rogue One (2016)
ok    encoded  41m12s
warn  subtitle track 3 barren, dropped
fail  Blade Runner (1982): encode failed (ffmpeg exit 1)
```

Every prompt shows the default in brackets and reads the answer on a `›`
line. The [transcode](docs/transcode.md#the-console) page shows a full run.

## Docs

One page per job:

- [setup](docs/setup.md) — once per ripping machine: dependencies, env vars,
  NFS mounts, drives, logs.
- [nas-layout](docs/nas-layout.md) — the two share trees: staging, libraries,
  mirrors, logs.
- [video](docs/video.md) — `rip movie` / `rip tv` end to end: TMDB picks,
  episode ranges, play-all-only discs.
- [transcode](docs/transcode.md) — the laptop-only transcode box and `skive transcode`:
  subtitle pass, crop gate, quarantine, retry.
- [music](docs/music.md) — `rip music`, `tag`, `sync-sd`: CD to library to
  SD card.
- [tools](docs/tools.md) — `validate` and `search`: library findings, finding
  already-ripped titles.

## Development

Tests: `uv run pytest` (933 passing). How skive decides things and the
glossary: [CONTEXT.md](CONTEXT.md). Deferred work: [roadmap.md](roadmap.md).
Decisions: [docs/adr/](docs/adr/).
