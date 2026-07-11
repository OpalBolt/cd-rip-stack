# cd-ripping

Rip CDs to tagged FLAC and DVDs/Blu-rays to 1080p H.265. Each pipeline is two commands, run inside a Nix devshell.

## What you get

- **CDs** — AccurateRip-verified FLAC, tagged from MusicBrainz with cover art, Last.fm genres, ReplayGain normalization, and sorted into a library.
- **DVD/Blu-ray** — decrypted MKV transcoded to 1080p H.265. UHD is downscaled (no 4K TV here); DVD and Blu-ray stay at native resolution.

## Prerequisites

- A CD drive, and/or an optical drive that reads DVD/Blu-ray
- [Nix](https://nixos.org) with flakes enabled
- For Blu-ray: a [MakeMKV key](https://forum.makemkv.com/forum/) in `~/.MakeMKV/settings.conf` as `app_Key = "..."`. The free beta key rotates every 30 days.

## Drives

CD sample offset (samples) for AccurateRip verification:

| Drive | Offset |
|-------|--------|
| Black Blu-ray drive | 103 |
| Sandberg | 6 |

## Setup

```sh
nix develop
```

Provides `rip`, `tag`, `rip-video`, `titles`, plus makemkv, handbrake, whipper, cyanrip, and beets.

## Ripping a CD

```sh
rip 6                # rip to FLAC (default engine: cyanrip)
rip 6 whipper        # use whipper (runs -U by default)
rip 6 cyanrip -N     # cyanrip: skip MusicBrainz lookup
rip 6 -R x           # force re-rip track x (engine defaults to cyanrip)
tag                  # tag + sort into the library
```

`rip <offset>` is required; the engine (`cyanrip` or `whipper`) is optional and defaults to cyanrip, so flags pass straight through — `rip 6 -R x` works without naming the engine. Any args after the engine go to the ripper.

Rips land in `~/media/audio/music/rip/`. `tag` runs beets, which tags the files and **copies** them into `~/media/audio/music/library/` (it does not delete the originals — clean `rip/` when done). Each run logs engine, offset, elapsed time, and status to `~/media/audio/music/rip.log`.

## Ripping a DVD / Blu-ray

```sh
titles               # list titles, longest-first (S = scrambled/decoy cut)
rip-video 0          # rip title 0 → 1080p H.265
rip-video            # rip all titles
rip-video 1-3        # rip titles 1 through 3
```

Two phases: **makemkvcon** decrypts and remuxes each title to a large lossless MKV in `~/media/video/rip/`, then **HandBrakeCLI** transcodes each to 1080p H.265 in `~/media/video/library/` and deletes the intermediate. The `H.265 MKV 1080p30` preset caps at 1080p; swap it for an x264 preset in `rip-video.sh` if encoding is too slow.

Pick the main feature with `titles`. When two titles share a length (Rogue One ships the movie as both title 0 and 1), `titles` flags a non-sequential segment order with `S` — that's the alternate/decoy cut. Rip the clean one, not `all`.

## Drive access

MakeMKV enumerates drives through `/dev/sg*` (SCSI-generic), not `/dev/sr*`. If it reports no drives:

```sh
sudo modprobe sg                                # load the sg module
echo sg | sudo tee /etc/modules-load.d/sg.conf  # persist across reboots
```

If it then sees drives but can't open them, add yourself to the `cdrom` group and re-login. Unmount the disc before ripping.

## Config (beets)

`beets-config.yaml` is self-contained. Library path:

```
~/media/audio/music/library/%the{$albumartist}/$year $album%aunique{}/$track - $title
```

Soundtracks → `_Soundtracks/`, compilations → `_Compilations/`, singles → `_Singles/`. ("The Beatles" sorts under **B**.)

Key plugins:

| Plugin | Does |
|--------|------|
| musicbrainz | Metadata from MusicBrainz |
| fetchart + embedart | Download and embed cover art |
| lastgenre | Top 3 genres from Last.fm |
| replaygain | Volume normalization (gstreamer backend) |
| ftintitle | `feat. Artist` in track titles |
| scrub + zero | Strip empty and junk tag fields |
| bandcamp | Tag Bandcamp releases (beetcamp) |

## Files

```
flake.nix          devShell: makemkv, handbrake, whipper, cyanrip, beets (+ plugin deps)
rip.sh             CD rip — engine choice, timing, log
rip-video.sh       DVD/Blu-ray rip — makemkvcon → HandBrakeCLI @ 1080p, log
titles.sh          makemkvcon info → readable title list with scramble flag
beets-config.yaml  beets config: tagging, art, genres, replaygain, paths
README.md          this file
```
