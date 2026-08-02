# cd-ripping

Rip CDs to tagged FLAC and DVDs/Blu-rays to 1080p H.265. Each pipeline is two commands, run inside a Nix devshell.

## What you get

- **CDs** — AccurateRip-verified FLAC, tagged from MusicBrainz with cover art, Last.fm genres, ReplayGain normalization, and sorted into a library.
- **DVD/Blu-ray** — ripped to a lossless MKV on the NAS, then transcoded to 1080p H.265. UHD is downscaled (no 4K TV here); DVD and Blu-ray stay at native resolution. Rip on whatever machine has a drive; transcode later on the desktop.

## Prerequisites

- A CD drive, and/or an optical drive that reads DVD/Blu-ray
- [Nix](https://nixos.org) with flakes enabled
- For Blu-ray: a [MakeMKV key](https://forum.makemkv.com/forum/) in `~/.MakeMKV/settings.conf` as `app_Key = "..."`. The free beta key rotates every 30 days.
- For DVD naming: a [TMDB API key](https://www.themoviedb.org/settings/api) exported as `TMDB_API_KEY`. Blu-rays are named from disc metadata (no key needed); a DVD rips as a label code (`B1`) and prompts a TMDB search at rip time. No key → the rip errors out after staging (export it first).

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

Provides `rip`, `tag`, `rip-video`, `transcode-video`, `movie-name`, `sync-music`, `sync-sd`, `mount-nfs`, `titles`, plus makemkv, handbrake, whipper, cyanrip, beets, curl, and jq.

## Ripping a CD

```sh
rip 6                # rip to FLAC (default engine: cyanrip)
rip 6 whipper        # use whipper (runs -U by default)
rip 6 cyanrip -N     # cyanrip: skip MusicBrainz lookup
rip 6 -R x           # force re-rip track x (engine defaults to cyanrip)
tag                  # tag + sort into the library
```

`rip <offset>` is required; the engine (`cyanrip` or `whipper`) is optional and defaults to cyanrip, so flags pass straight through — `rip 6 -R x` works without naming the engine. Any args after the engine go to the ripper.

If cyanrip's MusicBrainz lookup matches several releases for the disc, `rip` shows the list, prompts for a number, and re-runs with `-R <n>` — no need to retype the command. Pass `-N` to skip the lookup entirely (beets tags later).

Rips land in `~/media/audio/music/rip/`. `tag` runs beets, which tags the files and **copies** them into `~/media/audio/music/library/` (it does not delete the originals — clean `rip/` when done).

## Moving music to the NAS

After `tag` sorts FLACs into `~/media/audio/music/library/`, move them to the NAS so Jellyfin sees them:

```sh
export NFS_MUSIC=192.168.103.4:/export/music
sync-music          # mount /mnt/music on demand, then move the library → /mnt/music/library/
```

`sync-music` mounts `NFS_MUSIC` at `${MUSIC_MOUNT:-/mnt/music}` via the shared `mount-nfs` helper (the same helper `rip-video`/`transcode-video` use for `NFS_VIDEOS`), then `rsync --remove-source-files` each top-level folder across and sweeps the now-empty source dirs. One log line per folder; on any failure it stops and leaves the source in place — re-run picks up where it stopped.

The `mount-nfs` helper runs `sudo mount -t nfs`, which resolves through sudo's `secure_path`, not the devshell — so the host needs an NFS client installed (`nfs-utils`; on NixOS add it to `environment.systemPackages`).

## Copying music to an SD card

Once the library is on the NAS, copy it onto a music player's SD card:

```sh
export NFS_MUSIC=192.168.103.4:/export/music
sync-sd            # mount /mnt/music on demand, then copy the library → the inserted SD card
```

`sync-sd` mounts `NFS_MUSIC` at `${MUSIC_MOUNT:-/mnt/music}` via `mount-nfs`, then `rsync -r --size-only` from `/mnt/music/library/` onto the card — the source is kept, so unlike `sync-music` this is a copy, not a move. A mounted card is auto-detected: any filesystem on a removable device, or one auto-mounted under `/media` or `/run/media/$USER` (the second clause catches internal SD readers whose `mmcblk` device doesn't report removable). If none is mounted, `sync-sd` lists the unmounted removable disks (`sdx`/`mmcblk`) and mounts the one you pick, then unmounts it again on exit — success, Ctrl-C, or any failure (a card that was already mounted is left mounted). When several removable devices are plugged in it lists them and asks which. Pass a path to override or disambiguate: `sync-sd /run/media/$USER/CARD`.

`--size-only` skips files already on the card by size, so re-runs after adding albums only copy the new ones — and it works on any card filesystem (FAT/exFAT choke on the timestamps and permissions that `-a` would try to set).

## Ripping a DVD / Blu-ray

Two commands, two machines. Rip on the laptop (whatever has a drive), transcode on the desktop. Both mount the same NAS export (`192.168.103.4:/export/movies`) at `/mnt/videos`; that mount is the hand-off.

```sh
titles               # list titles, longest-first (S = scrambled/decoy cut)
rip-video 0          # rip title 0 → lossless MKV in /mnt/videos/rip/
rip-video            # rip all titles (default)
rip-video 0,5-7      # main feature + extras 5-7 (skip a decoy title)
rip-video --extras    # 2nd disc of a set: rip as extras (_eNN, never the main feature)
transcode-video      # on the desktop: encode every staged MKV → /mnt/videos/library/
```

Rip writes lossless MKVs straight to `/mnt/videos/rip/` (Jellyfin never scans it). Track selection happens at rip via a MakeMKV profile (`custom.mmcp.xml`): audio is the film's original language (from TMDB), subtitles keep Danish when present and fall back to English, plus any untagged tracks. `transcode-video` pulls each file to a local temp dir to encode off NFS, then uploads the result to its final home and deletes the lossless source — re-run it for newly ripped discs.

`titles` and `rip-video` both scan with `--minlength=$RIP_MINLENGTH` (default 120s = 2 min): drops logos and FBI warnings, keeps extras like deleted scenes. They must share the value — makemkv renumbers titles by what survives the scan, so a different `titles` and `rip-video` make the numbers point at different titles. Set `RIP_MINLENGTH` lower to catch shorter extras.

/mnt/videos/
├── rip/                          ← lossless staging (ripped here)
│   ├── <Movie (Year)>_tNN.mkv       ← movie disc (title 0 = main)
│   └── <Movie (Year)>_eNN.mkv       ← extras disc (rip-video --extras)
└── library/                      ← point Jellyfin here
    └── <Movie (Year)>/
        ├── <Movie (Year)>.mkv        ← title 0 (lowest _tNN)
        └── extras/
            └── <Movie (Year)>_{t,e}NN.mkv ← other titles + the extras disc
```

The transcode is a dumb pass-through re-encode: video → H.265 1080p, every audio and subtitle track kept as-is and soft (never burned in; selection already happened at rip). The `H.265 MKV 1080p30` preset caps at 1080p; swap it for an x264 preset in `transcode-video.sh` if encoding is too slow.

Set the NFS export before ripping (the scripts read the process environment, never `.env`): `export NFS_VIDEOS=192.168.103.4:/export/movies`. Both `rip-video` and `transcode-video` mount `/mnt/videos` on demand via `mount-nfs` if it isn't already — the same helper `sync-music` uses for `/mnt/music`.

Pick the main feature with `titles`. When two titles share a length (Rogue One ships the movie as both title 0 and 1), `titles` flags a non-sequential segment order with `S` — that's the alternate/decoy cut. Rip the clean one, not `all`.

Two-disc sets (movie + extras disc): rip the movie disc normally, swap in the extras disc, and run `rip-video --extras` — pick the same movie at the TMDB prompt. Its titles stage as `<Movie (Year)>_eNN.mkv` (never colliding with the movie's `_tNN`), and `transcode-video` files them under `<Movie (Year)>/extras/`. A second extras disc is safe too: the rename rolls past any `_eNN` already staged instead of clobbering it.
Blu-rays carry their title in disc metadata, so `rip-video` names them straight away (e.g. `Rogue One- A Star Wars Story_t00.mkv`) — no prompt, no network. DVDs have only a label code (`B1`, or per-title codes for extras), so `rip-video` resolves the real name at rip time via `movie-name --rename`: it prompts for a TMDB search, you pick a result (Enter = top match, `s` = skip, `q` = quit), and every staged MKV — main and extras alike — is renamed to `<Title> (Year)_tNN.mkv`; `transcode-video` then files them under `<Title> (Year)/` for free. Export `TMDB_API_KEY` first (read from the process env, never `.env`). Botched the pick? Re-run `movie-name --rename /mnt/videos/rip` — already-named files set the name, only the junk-labelled ones get renamed. With no key (or `q`) the rip errors out after staging; a network failure, blank search, or `s` falls back to the raw label with a warning.

## Logging

`rip`, `rip-video`, `transcode-video`, `titles`, and `sync-music` each append to `./rip.log` in the directory you run them from. A run logs a `started` line first (so a long rip shows up immediately), then one line per phase. A CD rip logs only the `total` — one summary line per disc, so `grep total rip.log` lists every CD rip. `rip-video` and `titles` log one phase line (their `total` is skipped as redundant); `transcode-video` and `sync-music` log each phase plus a `total`. Each line records start, end, script, software, name, phase, elapsed, status:

```
2026-07-12 14:00:00  14:30:00  rip-video.sh        makemkvcon  Rogue One: A Star Wars Story  rip     30m00  ok
2026-07-12 14:30:00  16:45:00  transcode-video.sh  handbrake   Rogue One: A Star Wars Story  encode  135m00 ok
2026-07-12 14:30:00  16:45:00  transcode-video.sh  handbrake   Rogue One: A Star Wars Story  total   135m00 ok
2026-07-12 17:00:00  17:08:05  rip.sh              cyanrip offset=6  Radiohead - OK Computer    total  8m05   ok
```

`transcode-video` logs one `encode` line per file plus the `total`; `rip-video` logs just `rip` (its `total` is skipped — one phase) plus `audio`/`subtitle` notes recording which tracks it kept; CD logs just `total`. The `name` comes from the resolved movie name for video; for CD it's `Artist - Album`, read from the ripped FLAC's tags after the rip — so `-N`, which skips the MusicBrainz lookup, logs `-`. (`tag`, the beets step, is separate and not logged.) Timing logic lives in `rip-common.sh`, sourced by all the scripts.

Since ripping and transcoding now run on different machines, `./rip.log` is per-machine (whichever cwd you ran the command in) — video history is split across two files. Kept simple for now; point both steps at a shared path on the NAS (e.g. `/mnt/videos/rip.log`) only if the split becomes annoying.

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
rip.sh             CD rip — engine choice, phase timing, log
rip-video.sh       DVD/Blu-ray rip — makemkvcon → lossless MKV in /mnt/videos/rip/, resolve movie name, phase timing, log
transcode-video.sh batch transcode — HandBrakeCLI @ 1080p off NFS → /mnt/videos/library/, per-file log
movie-name.sh      resolve a real movie name (Title, Year, Lang) for a disc or lossless MKV — Blu-ray metadata, else TMDB
custom.mmcp.xml    MakeMKV selection profile — picks audio + subtitle tracks at rip (Tasks 3 & 4 narrow it)
rip-common.sh      shared timing + ./rip.log helper, sourced by rip.sh, rip-video.sh, transcode-video.sh, titles.sh, sync-music.sh
mount-nfs.sh       mount-on-demand helper for the NFS shares (called by sync-music + the video scripts)
sync-music.sh      move finished, beets-tagged music to /mnt/music/library/ (rsync --remove-source-files, per-folder log)
sync-sd.sh         copy the NAS music library onto the inserted SD card (rsync -r --size-only, auto-detects the card)
titles.sh          makemkvcon info → readable title list with scramble flag
beets-config.yaml  beets config: tagging, art, genres, replaygain, paths
README.md          this file
```
