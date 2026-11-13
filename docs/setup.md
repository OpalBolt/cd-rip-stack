# Setup

Once per ripping machine — the Linux box with the disc drive. After this page,
you can run any command; workflows live on the [video](video.md) and
[music](music.md) pages, the share trees on [nas-layout](nas-layout.md).

## Dependencies

Python 3.13 and uv. With mise, the tool versions are pinned in `mise.toml`:

```bash
mise run install      # tools + skive on PATH (editable, from this repo)
mise run update       # ff-pull and refresh the install
```

That installs `skive` on your PATH from this repo (re-run `mise run update`
after pulling) and brings `seconv` — Subtitle Edit's headless converter, the
subtitle-pass engine — plus `ffmpeg` (with `ffprobe`). Without mise, install
uv by its own means and run `uv tool install --editable .` yourself.

By hand, on Arch (`yay` works in place of `paru`):

```bash
sudo pacman -S --needed flac lsdvd mkvtoolnix-cli rsync tesseract tesseract-data-eng tesseract-data-dan
paru -S --needed makemkv cyanrip
```

- `makemkv` — needs a MakeMKV key in `~/.MakeMKV/settings.conf` as
  `app_Key = "..."`; the free beta key rotates every 30 days — an expired key
  refuses BD rips with the fix instructions (ADR 0016), DVD and CD rips are
  unaffected
- `cyanrip` — CD rips (AccurateRip verification)
- `flac` — provides `metaflac`
- `lsdvd` — the DVD fast-scan gate; without it every DVD silently falls back
  to the slow full scan
- `mkvtoolnix` — provides `mkvmerge`
- `tesseract` with the `eng` and `dan` language data — subtitle-pass OCR;
  `tesseract --list-langs` must show both
- `rsync` — for `sync-sd`

The beets stack (beets, beetcamp, the replaygain plugin) arrives with the
venv from `mise run install`. replaygain needs a GStreamer/GI stack on this
box, or beets fails at start-up.

## Environment variables

Keys come from the process environment — skive never reads a `.env` file.
Export them in your shell profile or `mise env`; `.env.example` records the
three primary keys.

| Variable | Meaning |
|---|---|
| `TMDB_API_KEY` | TMDB API key, for movie/TV naming on `rip movie` / `rip tv` |
| `NFS_VIDEOS` | NFS export for the videos share, e.g. `192.168.103.4:/export/videos` |
| `NFS_MUSIC` | NFS export for the music share, e.g. `192.168.103.4:/export/music` |
| `VIDEOS_MOUNT` / `MUSIC_MOUNT` | override the mountpoints (default `/mnt/videos` / `/mnt/music`) |
| `RIP_MINLENGTH` | title-scan minimum length in seconds (default `120`) |

Three names the code reads that are never operator config: `MAKEMKV_INFO`
overrides the makemkv info binary (test seam — also how scan dumps replay,
below); `SKIVE_TAG_RUN` / `SKIVE_TAG_MODE` are set and restored by `skive
tag` around its beets child; `VIDEOS` is a default in the legacy
`scripts/backfill.py` only.

## NFS mounts

Both shares mount on demand: before a command touches the share, skive checks
`/proc/mounts` and, if the mountpoint is not mounted, runs
`sudo mount -t nfs <export> <mountpoint>`. skive never unmounts. The export
comes from `NFS_VIDEOS` / `NFS_MUSIC`; an empty export or a failed mount is a
one-line error and exit 1.

## Drives

`rip music` needs the drive's CD sample offset, in samples, as its first
argument. With both drives attached it also needs `-d N` to pick the drive —
the same makemkv drive number `rip movie`/`rip tv` use; without it skive
resolves drive 0 the same way (cyanrip's first-found drive is never used):

- Black Blu-ray drive: `103`
- Sandberg: `6`

MakeMKV enumerates drives through `/dev/sg*` (SCSI-generic), not `/dev/sr*`:

- No drives reported: `sudo modprobe sg` (persist with
  `echo sg | sudo tee /etc/modules-load.d/sg.conf`).
- Drives seen but not openable: add yourself to the `cdrom` group and log in
  again.
- Unmount the disc before you rip.

Video rips name the drive with `-d N`, which becomes makemkvcon's `disc:N`.
Two discs can rip at once without colliding: makemkvcon rips each title into
a `d<N>` subfolder of the title's staging folder, and finished MKVs are
promoted up out of `d<N>` when the title completes. A name collision leaves
the file in `d<N>/` with a note — never clobbered. Junk-labeled files
(`B1_t00.mkv`-style labels; junk label is CONTEXT.md vocabulary) are purged
by the next run on that drive, and another drive's partials rest untouched in
their own `d<N>`.

## Logging

Logs live on the share each command works on: video commands (`rip movie`,
`rip tv`, `transcode`) under `VIDEOS_MOUNT`, `rip music` and `tag` under
`MUSIC_MOUNT` — `sync-sd` works on the music library but its log lands under
`VIDEOS_MOUNT` too (the log root defaults there). Three things, under `logs/`:

- `rip.jsonl` — the run log: one JSON line per event, append-only. Each line
  is `{ts, run, script, software, name, phase, status, elapsed_s}`; `run` is
  a short id minted per invocation; `status` carries `started`, `ok`,
  `FAIL(rc)`, `interrupted`, or note text. This is the history of every run,
  and it survives everything.
- `raw/<run>.jsonl` — the raw log: that run's verbatim tool output, one line
  per line. Deleted on a clean exit; kept when the run is interrupted or
  fails, and kept by `tag` when an album is left in `rip/` — so one failure's
  full tool output sits next to its run-log lines.
- `scan/<run>_<volume-label>.txt` — scan dumps: the verbatim `makemkvcon
  info` output of every full disc scan, never deleted. A scan is the one
  moment the disc's whole structure is in hand; questions surface later, when
  the disc may already be out of the drive. Replay a dump through the
  scanner with `MAKEMKV_INFO="cat <dump>"`.

Verbosity (`-v`, `-vv`) gates the console's tool lines only — both logs are
always written. Long commands keep every event in scrollback with a symbol
(`✓ · ! ✗`) and redraw a footer of at most two lines beneath it. `NO_COLOR`
and `TERM=dumb` only affect the colour. Piped or redirected, they print plain
tagged lines (`info`, `ok`, `warn`, `fail`) with no escape codes and no
footer. The `skive transcode` section of
[transcode](transcode.md) shows both forms.

Related: [nas-layout](nas-layout.md) · [video](video.md) · [music](music.md)
