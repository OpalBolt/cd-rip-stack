# cd-ripping

CD ripping pipeline: whipper or cyanrip → beets with MusicBrainz metadata.

## What you get

AccurateRip-verified FLACs, tagged from MusicBrainz, with embedded cover
art, genre tags from Last.fm, ReplayGain volume normalization, and clean
metadata — two commands. Rip time is logged to `~/media/audio/music/rip.log`.

## Prerequisites

- A CD drive
- [Nix](https://nixos.org) with flakes enabled

## Drives

| Drive | Offset |
|-------|--------|
| Black Blu-ray drive | 103 |
| Sandberg | 6 |

## Setup (one-time)

```sh
nix develop
```

## Workflow (per CD)

```sh
rip 103            # rip CD to FLAC (default engine: whipper)
rip 103 cyanrip    # same, using cyanrip
tag                # metadata + sort into library
```

whipper/cyanrip output under `~/media/audio/music/rip/`. `beet import` tags
the files then moves them into `~/media/audio/music/library/` (set in
`beets-config.yaml`). Each rip appends a line (engine, offset, elapsed
time, status) to `~/media/audio/music/rip.log`.

## Config

`beets-config.yaml` is self-contained. Key plugins:

| Plugin | Does |
|--------|------|
| musicbrainz | Metadata from MusicBrainz (built-in) |
| fetchart + embedart | Download cover art and embed it in files |
| lastgenre | Top 3 genres from Last.fm tags |
| replaygain | Volume normalization across albums |
| scrub + zero | Removes empty and junk tag fields |

## Files

```
flake.nix          Nix flake: whipper, cyanrip, beets, and plugin deps
rip.sh             Rip wrapper — picks engine, times the rip, logs elapsed time
beets-config.yaml  Beets configuration with all plugins
README.md          This file
```
