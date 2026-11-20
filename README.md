# skive

Rip CDs, DVDs, and Blu-rays to MKV and FLAC.

A two-box, hand-driven pipeline: a ripping machine pulls discs to lossless
MKV and FLAC on two NFS shares and files music into a beets-managed library;
a transcode machine drains video staging into a Jellyfin-ready H.265 library with
SRT sidecars. Eight commands below; each docs page is one job.

## Quickstart — the ripping machine

```bash
git clone <this repo> && cd cd-rip-stack
cp .env.example .env    # fill TMDB_API_KEY
podman compose build    # the skive box — every tool baked in
./skive doctor          # green table before the first disc (host prep: setup docs)
./skive rip movie       # insert the disc, pick from TMDB, set the table
```

`./skive <cmd>` runs any skive command in the box; bare `./skive` opens a
shell. The shares must already be mounted at `/mnt/videos` and
`/mnt/music` — skive never mounts for you. Host prep, env keys, drives
and logs: [setup](docs/setup.md). The box and `skive transcode` —
[transcode](docs/transcode.md).

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
(`transcode`, `rip movie`, `rip tv`, `rip music`) draw rounded boxes that flow
together at the same width: an opener box with the run's scope, one frame per
item (a `╭─ 1/4  Name` rule, its lines behind a `│` gutter, a `╰─ ✓ done 7m56s`
or `╰─ ✗ interrupted` rule), a `now` box under the scroll that redraws the
current step (bar, ETA), and a closing `summary` box with the result line in
its bottom border. Pick tables and short commands use the same rounded tables.

```text
╭─ 1/2  Rogue One (2016) ──────────────────────────────
│ ✓ copy      5744 MB at 63 MB/s                  1m30s
│ ✓ encode    hevc_vaapi                         41m12s
╰─ ✓ done 43m02s ──────────────────────────────────────
```

Piped or redirected output, `NO_COLOR` and `TERM=dumb` give plain lines with no
escape codes, no boxes and no live box. Each line starts with a padded word
instead of a symbol:

```text
info  1/2  Rogue One (2016)
info  copied in 1m30s (5744 MB at 63 MB/s)
info  barren dan (track 4): OCR yielded 0 cues -- dropped
ok    Rogue One (2016)_t00.mkv: copy 1m30s + subs 12s + detect 8s + encode 41m12s
fail  Blade Runner (1982)_t01.mkv: copy 3m02s + subs 4s + detect 5s + encode 7m40s -- FAILED
```

Every prompt reads the answer on a `›` line. A prompt that has a default
shows it in brackets (`[1]`, `[y/N]`); a few, like the sync-sd picker, have
none. The one-answer prompts stay small — the TMDB pick, the TV season — but
the scope of a rip is decided in the [selection
table](docs/video.md#the-selection-table), not at a prompt. The
[transcode](docs/transcode.md#the-console) page shows a full run.

## Docs

One page per job:

- [setup](docs/setup.md) — once per machine: the skive box (host prep,
  build, doctor), env vars, share mounts, drives, logs.
- [nas-layout](docs/nas-layout.md) — the two share trees: staging, libraries,
  mirrors, logs.
- [video](docs/video.md) — `rip movie` / `rip tv` end to end: TMDB picks,
  episode ranges, play-all-only discs.
- [transcode](docs/transcode.md) — the skive box and `skive transcode`:
  subtitle pass, crop gate, quarantine, retry.
- [music](docs/music.md) — `rip music`, `tag`, `sync-sd`: CD to library to
  SD card.
- [tools](docs/tools.md) — `validate` and `search`: library findings, finding
  already-ripped titles.

## Development

Tests: `uv run pytest` (1035 passing). How skive decides things and the
glossary: [CONTEXT.md](CONTEXT.md). Deferred work: [roadmap.md](roadmap.md).
Decisions: [docs/adr/](docs/adr/).
