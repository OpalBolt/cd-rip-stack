# cd-ripping

Rip CDs to AccurateRip-verified FLAC. Rip DVDs and Blu-rays to lossless MKV, then transcode to 1080p H.265.

The project is a Nix flake. You need only Nix (with flakes) and `just`. Every recipe hides `nix develop`.

## Get started

Three ways in, all through `just`:

- `just run` — open `rip-menu`, the default. Bare `just` does the same.
- `just rip <args>` — rip a CD fast. Example: `just rip 6`, or `just rip 6 whipper`.
- `just rip-video <args>` — rip a disc fast. Example: `just rip-video 0,5-7`.

`rip-menu` is an fzf menu with a kanagawa theme. It opens on `index`, a workflow overview. Move the cursor to a command and the preview pane shows its full help. Press Enter to run it. If the command needs arguments, it asks for them first.

A `Pending:` line above the list counts what waits — untagged rips and staged MKVs. The count is computed live. Nothing is stored.

## Prerequisites

- A CD drive and/or an optical drive that reads DVD or Blu-ray.
- Nix with flakes.
- `just`.
- For Blu-ray: a MakeMKV key in `~/.MakeMKV/settings.conf` as `app_Key = "..."`. The free beta key rotates every 30 days.
- For DVD naming: a TMDB API key exported as `TMDB_API_KEY`. Blu-rays name from metadata and need no key.

The scripts read keys from the process environment, never from `.env`.

## Drives

AccurateRip needs the drive's CD sample offset, in samples:

- Black Blu-ray drive: `103`
- Sandberg: `6`

## Ripping a CD

### rip

Rip a CD to AccurateRip-verified FLAC.

Usage:

```text
rip <offset> [engine] [flags]
```

- `offset` — the drive's CD sample offset (see Drives).
- `engine` — `cyanrip` (default) or `whipper`.
- `flags` — pass through to the ripper.

Examples:

- `just rip 6` — rip with the default engine, cyanrip.
- `just rip 6 whipper` — rip with whipper.
- `just rip 6 cyanrip -N` — skip the MusicBrainz lookup.
- `just rip 6 -R x` — force a re-rip of track `x`.

The offset is required. The engine is optional, so flags pass straight through to the ripper. If cyanrip's MusicBrainz lookup matches several releases, `rip` lists them, asks for a number, and re-runs with `-R <n>`.

Rips land in `~/media/audio/music/rip/`.

### tag

Run beets over the rip directory. It fetches MusicBrainz metadata, cover art, Last.fm genres, and ReplayGain, then copies the tagged FLAC into `~/media/audio/music/library/`. The originals stay in `rip/`. Clean `rip/` by hand when done. Rip several discs, then `tag` the batch.

## Moving music to the NAS

### sync-music

Move the library onto the NAS so Jellyfin sees it.

```bash
export NFS_MUSIC=192.168.103.4:/export/music
sync-music
```

It mounts `NFS_MUSIC` at `${MUSIC_MOUNT:-/mnt/music}` on demand through the shared `mount-nfs` helper. Then it runs `rsync --remove-source-files` on each top-level folder. It moves the files. It does not copy them.

It logs one line per folder. If a folder fails, it stops and leaves the source in place, so a re-run resumes.

`mount-nfs` runs `sudo mount -t nfs`, which resolves through sudo's `secure_path`. The host needs an NFS client (`nfs-utils`). On NixOS, add it to `environment.systemPackages`.

### sync-sd

Copy the NAS library onto an SD card for a music player.

```bash
export NFS_MUSIC=...
sync-sd [card]
```

It takes an optional `card` argument — a mountpoint that overrides the auto-detected card.

It mounts `/mnt/music`, then runs `rsync -r --size-only` from `/mnt/music/library/` onto the card. The source is kept, so this is a copy, unlike `sync-music`.

It auto-detects a mounted card. It looks for a removable filesystem, or a card under `/media` or `/run/media/$USER` (which catches internal SD readers). If no card is mounted, it lists the removable disks (`sdx` or `mmcblk`) and mounts the one you pick. It unmounts the card on exit — on success, Ctrl-C, or failure. A card that was already mounted stays mounted.

`--size-only` skips files already on the card by size, so re-runs copy only new albums. It also works on any card filesystem. FAT and exFAT choke on the timestamps and permissions that `-a` would set.

## Ripping video

Two machines: rip on the one with a drive, transcode later on the desktop. Both mount the same NAS export at `/mnt/videos`. That mount is the hand-off.

```bash
export NFS_VIDEOS=192.168.103.4:/export/movies
```

Layout on the NAS:

```text
/mnt/videos/
  rip/      lossless staging: <Movie (Year)>_tNN.mkv (movie), _tNN.pN.mkv (split-film half), _eNN[.pN].mkv (extras)
  library/  point Jellyfin here: <Movie (Year)>/<Movie (Year)>.mkv + extras/
  rip-DONE/ where transcode-video --keep parks lossless sources after encoding (verify, then delete)
```

### titles

List a disc's titles, longest first, before you rip. Use it to pick the main feature and skip decoy cuts.

```text
titles [disc:N]   (default disc:0)
```

An `S` marks a scrambled or decoy cut (non-sequential order). Rip the clean title, not all of them. `titles` and `rip-video` share `RIP_MINLENGTH` (default 120s), so a title's number is the same in both.

### rip-video

Rip a DVD or Blu-ray to lossless MKV on the NAS through `makemkvcon`. At rip time it picks the film's original-language audio, plus Danish subtitles when present (else English).

```text
rip-video [--extras] [--part N] [titles]   (NFS_VIDEOS)
```

`titles` selects what to rip: `all` (the default), a single number, or a range like `1,3-4`. `--extras` rips the second disc of a set as extras; `--part N` rips a film split across discs as part `N` (use it on every disc of the split).

Examples:

- `just rip-video` — rip all titles.
- `just rip-video 0` — rip title 0.
- `just rip-video 0,5-7` — rip a spec.
- `just rip-video --extras` — rip the 2nd disc of a set as extras.
- `just rip-video --part 1` then `--part 2` — rip a film split across two discs (same movie at each prompt).

It scans the disc and rips the chosen titles to `/mnt/videos/rip/` as `<Name>_tNN.mkv`. Blu-rays name from metadata with no prompt. DVDs rip as a label code like `B1` and prompt a TMDB search at rip time. With no key, the DVD rip errors after staging. It renames every staged MKV to `<Title> (Year)_tNN.mkv`.

Track selection happens through a MakeMKV profile (`custom.mmcp.xml`). Audio is the film's original language from TMDB. Subtitles keep Danish when present and fall back to English, plus untagged tracks. Both `titles` and `rip-video` scan with `--minlength=$RIP_MINLENGTH` (default 120s) to drop logos and FBI warnings, so they must share the value. MakeMKV renumbers titles by what survives the scan.

Two kinds of multi-disc sets, handled separately. A **film split across discs** (the movie itself continues on disc 2): rip each disc with `--part N`, picking the same movie at the prompt. Halves stage as `_tNN.pN.mkv`, and `transcode-video` merges the main feature's parts in order (p1, p2, ...) into one movie — only the main feature merges; extras stay separate. An **extras disc** (bonus features, not the film): rip it with `--extras`. It stages as `_eNN.mkv`, and `transcode-video` files it under `extras/`. A second extras disc is safe — the rename rolls past any existing `_eNN`.

### transcode-video

Batch-encode every staged MKV in `/mnt/videos/rip/` to 1080p H.265 and file it under `/mnt/videos/library/`.

```text
transcode-video [--keep] [name ...]
```

For each staged MKV, it pulls the file to a local temp dir so it encodes off NFS. It encodes to H.265 1080p and keeps every audio and subtitle track, then uploads the result and deletes the lossless source. The transcode is a pass-through: all tracks stay soft, never burned in. A film ripped in parts has its main feature merged with `mkvmerge` (in part order) before encoding, so it lands as one movie. Run this on the desktop. Re-run for newly ripped discs.

Positional `name`s run only the movies whose staged filenames start with them — `transcode-video "X2 (2003)"` transcodes one movie, `"The Godfather"` grabs the whole trilogy. Use it to try the pipeline on a single film.

`--keep` moves the lossless sources to `rip-DONE/` instead of deleting them. Verify the encodes in the library, then clean `rip-DONE/` by hand.

Files still carrying a junk disc label — a rip in progress, or one whose rename failed (no `(Year)` in the name) — are always skipped, so a running `rip-video` and `transcode-video` never race.

### movie-name

Resolve a real name (`Title`, `Year`, `Language`) for a disc or MKV. It usually runs inside `rip-video`. You rarely run it by hand.

```text
movie-name [--rename DIR | disc:N | file]
```

- Blu-rays: the name comes from disc metadata.
- DVDs: a TMDB search (needs `TMDB_API_KEY`). You pick a result.

Re-run with `--rename <dir>` to fix a botched pick across already-staged MKVs. Files that already have a real name set the pattern. It renames only the junk-labelled ones.

## Logging

`rip`, `rip-video`, `transcode-video`, `titles`, and `sync-music` append to `./rip.log` in the directory you run them from. Each line records start, end, script, software, name, phase, elapsed, and status.

- A CD rip logs only the `total`.
- `rip-video` and `titles` log one phase.
- `transcode-video` and `sync-music` log each phase plus the `total`.

`tag` is not logged. Because rip and transcode run on different machines, `./rip.log` is per machine.

## Drive access

MakeMKV enumerates drives through `/dev/sg*` (SCSI-generic), not `/dev/sr*`.

- If it reports no drives: `sudo modprobe sg`. To persist, run `echo sg | sudo tee /etc/modules-load.d/sg.conf`.
- If it sees drives but cannot open them: add yourself to the `cdrom` group and log in again.

Unmount the disc before you rip.

## beets configuration

`lib/beets-config.yaml` is self-contained. The library path is:

```text
~/media/audio/music/library/%the{$albumartist}/$year $album%aunique{}/$track - $title
```

Soundtracks sort under `_Soundtracks/`, compilations under `_Compilations/`, and singles under `_Singles/`. "The Beatles" sorts under B.

Plugins:

- `musicbrainz` — metadata.
- `fetchart` + `embedart` — cover art.
- `lastgenre` — top three genres.
- `replaygain` — uses gstreamer.
- `ftintitle` — writes `feat. Artist`.
- `scrub` + `zero` — strip junk fields.
- `bandcamp` — uses beetcamp.

## Project layout

The top level holds `flake.nix`, `justfile`, `README.md`, and `lib/`. Everything else lives under `lib/`:

```text
lib/
  rip.sh  rip-video.sh  transcode-video.sh  titles.sh  movie-name.sh
  sync-music.sh  sync-sd.sh  rip-menu.sh  tag.sh
  rip-common.sh  mount-nfs.sh          (sourced helpers)
  rip-menu-help.txt                    (the menu's per-command help)
  custom.mmcp.xml  beets-config.yaml   (configs)
```
