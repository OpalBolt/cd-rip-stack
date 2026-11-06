# Transcode

`skive transcode` drains `rip/` staging into the Jellyfin library: each staged
file is pulled local, merged, subtitle-passed, re-encoded to H.265, and filed
under `library/` (trees: [nas-layout](nas-layout.md)).

## The box

There is one transcode box: the Ryzen AI 7 350 laptop's `transcode`
distrobox — the BC-250 no longer transcodes (ADR 0010 records the path and
its 2026-11 hardware revision). The BC-250's story is history that explains
why the laptop's VCN matters: the PS5-derived Cyan Skillfish board's VCN
block is factory-unprovisioned and eFused off, so no tool ever got hardware
encode there — and the community `bc250-vaapi` shim stays rejected (its
H.264 is libx264 on the CPU in a costume, its HEVC a CBR streaming encoder
shaped for game streaming, not quality-based archival — ADR 0010). The
laptop's VCN does encode: `-e auto` (the default) resolves to `hevc_vaapi`
on it. Hardware encode goes through ffmpeg VAAPI, not HandBrake — HandBrake
on Linux has no VAAPI encoder.

Running it needs two things on the laptop: a `fedora:43`-era image
(gfx1152/Radeon 860M needs Mesa 25.0+ for VCN encode) and the
`mesa-va-drivers-freeworld` swap that `scripts/setup-transcode.sh`
performs (Fedora strips H.264/H.265 from its Mesa for patents; without
the freeworld build vainfo lists only JPEG/VP9/AV1 and there is no HEVC
encode entrypoint).
The box carries every tool: RPM Fusion's HandBrakeCLI, mkvtoolnix, ffmpeg,
tesseract with eng+dan language data, seconv (pinned at v5.3.0 in the
script), and skive itself (editable install via uv). The videos share
bind-mounts at `/mnt/videos`; all skive runs happen inside the box.

```bash
git clone <this repo> && cd cd-rip-stack
distrobox create -n transcode --image fedora:43 --volume /mnt/videos:/mnt/videos
distrobox enter transcode
bash ~/git/cd-rip-stack/scripts/setup-transcode.sh   # once, inside the box
```

The script is idempotent — a `git pull` plus a re-run heals a broken box.
Updating skive is just `git pull` inside `~/git/cd-rip-stack`; the editable
install picks changes up. Bumping seconv means editing the pinned version
in `scripts/setup-transcode.sh`. No mise here — mise.toml belongs to the
rip box only. The box uses the same env vars as the ripping machine — see
[setup](setup.md). Runs are invoked by hand; nothing schedules them.

## Running a batch

```bash
skive transcode                     # drain all of rip/
skive transcode 'Film (2001)'       # only titles whose staged name starts with this prefix
skive transcode -k                  # keep lossless sources in lossless/ instead of deleting
skive transcode --retry             # re-run quarantined items from issues/
skive transcode -w /var/tmp/video-work   # workdir override (default: ~/.cache/skive)
```

- **Input** — `rip/` staging (`issues/` with `--retry`). The first path
  segment under the root decides movie vs TV; titles without a `(Year)`
  tail are skipped as junk (mid-rip or failed rename). A prefix that
  matches nothing is called out on stderr; if nothing matched at all, exit 1.
- **Work dir** — `~/.cache/skive` (disk, not tmpfs: the staged source plus
  the encode output need GBs); `-w` overrides. Every file is deleted after
  each item ships, fails, or the run is interrupted.
  Files are pulled local and encoded off the share.
- **Prefetch** — while one unit encodes, the next unit's source already
  pulls into the workdir (one unit of lookahead; the run stays synchronous,
  one encode at a time). Without workdir headroom for the next source,
  that item falls back to the sequential copy with a loud note — a starved
  prefetch never fails a run. Ctrl+C drops prefetched partials like any
  in-flight temp.
- **Movies** — the biggest `_tNN` group (parts sum) is the main feature;
  a recorded main in the title's `validation.json` wins over bytes. Split
  mains merge with mkvmerge before encoding — but only when every part's
  track layout matches part 1's; a mismatched set quarantines whole,
  unmerged (the merge guard). Ships
  `library/movies/<Movie (Year)>/<Movie (Year)>.mkv`. Extras never merge;
  they file as ` - Extra NN` under the movie's `extras/`, embedded
  subtitles kept, no subtitle pass.
- **TV** — every episode encodes: `library/tv/<Show (Year)>/Season NN/
  <Show (Year)> - S01E02.mkv` (season 00 → `Specials/`), plus the subtitle
  pass. TV extras file under the show's `extras/` with tracks embedded.
- **Encode** — `-e auto` (default) picks per box: VCN hardware on the
  laptop (`ffmpeg hevc_vaapi` CQP 24, audio and subtitles stream-copied,
  bwdif deinterlaces interlaced frames only, width cropped to VCN's
  64-pixel alignment — the hardware's own padding bleeds a stretched
  right edge on 720-wide DVDs; ~181 fps at 1080p, a feature encodes in
  ~16 minutes), x265 fast otherwise. `-e x265` keeps the software
  baseline: HandBrake at native resolution, never upscaled (the probed
  height picks the preset: 480p/576p DVD — those also get
  detelecine+decomb — 720p, 1080p, 4K; audio `--aencoder copy` with ac3
  fallback). `-e vaapi` demands hardware and fails fast without it.

### The inline subtitle pass

Mains and TV episodes ship with zero subtitle tracks — bitmap PGS/VobSub
makes Jellyfin transcode every subtitled play — so before the encode,
every eng/dan bitmap track is extracted and OCR'd (seconv → tesseract) into
SRT sidecars beside the encode: `<base>.<eng|dan>[.forced|.sdh].srt`.
Per language, the full track with the most OCR'd cues wins; a runner-up
holding ≥30% of that count ships as the SDH duplicate; weaker dupes are
dropped. Forced tracks always ship, flagged `.forced`. The encode itself
selects zero subtitle tracks via a language list no track can match.
A failed pass still ships the (subless) encode, but the source quarantines
below — rerun it with `--retry` to get the sidecars.

### The crop gate

Every encode (mains, TV episodes, extras) is checked for baked-in black
bars: ~6 probes spread over 10–90% of the duration, each side's bar
estimated by the median probe, at most one probe per side allowed to
dissent beyond ±4 px. Sides under 16 px crop as nothing; a per-axis total
crop is capped at 30%; fewer than 3 readable probes means no crop. Applied
crop rides the encode; a refused detection (gate dissent) or unreadable
probes ship the title uncropped with a loud note — the gate never fails
an item. Recording: one timed `detect` line in the run log per item, and
the `validation.json` encodes entry carries `"crop"` and `"size"`. See
[ADR 0011](adr/0011-black-bar-crop-gate.md) for the policy and field
evidence.

## Failure and recovery

- **Quarantine** — a failed subtitle pass or a merge guard hit parks the
  source in `issues/<type>/<Name (Year)>/` together with that run's
  `rip.jsonl` lines and raw log; a failed encode leaves the source where
  it rests (rip/ staging, or issues/ under `--retry`). Nothing clobbers:
  an existing file or folder is left alone.
- **`--retry`** — walks `issues/` instead of `rip/`. A success ships,
  archives, and moves the source out of `issues/`; a failure rests in
  place, its folder re-annotated (no-op copy). End state: `issues/` empty.
- **`--keep`** — parks sources under `lossless/<type>/<Name (Year)>/`
  instead of deleting them; never clobbers, warns and leaves the source.
  Verify the encodes, then clean `lossless/` by hand.
- **Ctrl+C** — the encode/merge child is stopped (grace, then kill), the
  item's partial temp output is deleted, the source stays in staging,
  exit 130. Sources only ever move on success.
- **Exit codes** — 0 clean; 1 if any encode, merge, or subtitle pass
  failed, or when the prefixes matched nothing. `--keep` sources and
  quarantine folders make every failure recoverable by rerun.
- **Logs** — every run writes a JSONL run log plus a raw tool log under
  the videos share's `logs/`; `-v`/`-vv` gate the console only.

### Reading the run

The run log (`logs/rip.jsonl`) gets one timed line per item per part —
`copy`, `detect`, `merge`, `subs`, `encode` — each with its own `elapsed_s` and the
part's numbers in `status`: copy reports `N MB at M MB/s`, encode
`ok, N MB`, merge `ok`, subs `K sidecar(s)[, B barren]`. A
phase that dies closes with `FAIL` and its elapsed; an interrupted run
closes with `interrupted`. A `barren` note means a bitmap subtitle track's
OCR yielded zero cues — the track is dropped with a loud note in the run
log and the encode ships without that sidecar. It is not a failure: no
quarantine, exit stays clean. Only a real seconv/tesseract failure
quarantines (see below). Prefetch shows on the footer's second line during the
previous encode (piped, as a `prefetch` note); when the lookahead lands, the item's own copy phase
reads `prefetched — overlapped with the previous encode`.

### The console

On a terminal, every event stays in scrollback. Each line starts with a
symbol: `✓` done, `·` routine, `!` warning, `✗` failure. A run opens with
one line that states its scope. Each item then gets a bold `▸` heading, and
its events indent beneath it:

```text
skive transcode · 2 to encode
▸ 1/2  Rogue One (2016)
  · copying Rogue One (2016)_t00.mkv to local temp...
  · copied in 1m30s (5744 MB at 63 MB/s)
  · subtitle pass: 3 subtitle track(s), 2 kept: Rogue One (2016)_t00.mkv
  · OCR eng track 3...
  · OCR dan track 4...
  · barren dan (track 4): OCR yielded 0 cues -- dropped
  ✓ Rogue One (2016)_t00.mkv: copy 1m30s + subs 12s + detect 8s + encode 41m12s
▸ 2/2  Blade Runner (1982)
  · copied in 3m02s (6100 MB at 33 MB/s)
  · no crop -- detection failed
  ✗ Blade Runner (1982)_t01.mkv: copy 3m02s + subs 4s + detect 5s + encode 7m40s -- FAILED
```

Each item ends on one line that names its file and lists every phase with its
time. A barren subtitle track is an `info` line in the item, not a warning.

Under the scroll, a footer of at most two lines redraws in place. Line one
holds the position, item, phase, a progress bar and the ETA. Line two holds
the prefetch copy and the perf line (CPU, load, temperatures):

```text
 1/2  Rogue One (2016) · encode  ━━━━━━━━              42%  ETA 31m
      prefetch Blade Runner (1982)_t01.mkv  40%      CPU 62% · cpu 78° · gpu 61°
```

The bar fills with colour on a terminal. Progress shows in the footer
only and never prints to scrollback.

When the run ends, the footer disappears and the run summary prints. It
replays the warnings and failures (not the `info` lines, so a barren track
does not appear again), shows a borderless table of timings and results, and
ends on one result line. After a failure the result line names the run log
and the raw log to read:

```text
✗ Blade Runner (1982)_t01.mkv: copy 3m02s + subs 4s + detect 5s + encode 7m40s -- FAILED

  Item                          copy    merge   subs   encode   total    result
 ─────────────────────────────────────────────────────────────────────────────────
  Rogue One (2016)_t00.mkv      1m30s   -       12s    41m12s   43m02s   ✓ ok
  Blade Runner (1982)_t01.mkv   3m02s   -       4s     7m40s    10m51s   ✗ failed

✗ 1 failed · 1 ok · 1h36m — see /mnt/videos/logs/rip.jsonl (run 93721d6e), raw /mnt/videos/logs/raw/93721d6e.jsonl
```

The `total` column sums every phase, including `detect`, which has no column
of its own.

After Ctrl+C the footer goes, in-flight items show `✗ interrupted`, and the
result line reads `✗ interrupted · 2 ok · 1h12m — partial deleted`.

`NO_COLOR` and `TERM=dumb` only affect the colour: a terminal keeps the
symbols and the footer. Piped or redirected output gives plain lines with
no escape codes. Each line starts with a padded word instead of a symbol
(`ok`, `info`, `warn`, `fail`). There is no indent, no footer and no
replay. Progress prints as lines instead: a copy logs `info  10%  (574/5744 MB)`
every 10% and the encode a throttled tool line, both left out of this run:

```text
info  skive transcode · 2 to encode
info  1/2  Rogue One (2016)
info  copying Rogue One (2016)_t00.mkv to local temp...
info  copied in 1m30s (5744 MB at 63 MB/s)
info  subtitle pass: 3 subtitle track(s), 2 kept: Rogue One (2016)_t00.mkv
info  OCR eng track 3...
info  OCR dan track 4...
info  barren dan (track 4): OCR yielded 0 cues -- dropped
ok    Rogue One (2016)_t00.mkv: copy 1m30s + subs 12s + detect 8s + encode 41m12s
info  2/2  Blade Runner (1982)
info  copied in 3m02s (6100 MB at 33 MB/s)
info  no crop -- detection failed
fail  Blade Runner (1982)_t01.mkv: copy 3m02s + subs 4s + detect 5s + encode 7m40s -- FAILED

  Item                          copy    merge   subs   encode   total    result
 ─────────────────────────────────────────────────────────────────────────────────
  Rogue One (2016)_t00.mkv      1m30s   -       12s    41m12s   43m02s   ok
  Blade Runner (1982)_t01.mkv   3m02s   -       4s     7m40s    10m51s   failed

fail  1 failed · 1 ok · 1h36m — see /mnt/videos/logs/rip.jsonl (run 93721d6e), raw /mnt/videos/logs/raw/93721d6e.jsonl
```

Tool lines show at `-v`/`-vv` (verbosity gates the console only).

## Existing library: subtitle backfill

The library predates the inline pass: encodes still carry embedded bitmap
subtitles. One conversion fixes that in place — same subtitle engine as the
batch, but the strip is verified and atomic:

```bash
uv run python scripts/backfill.py                  # the library at /mnt/videos/library (overnight-sized)
uv run python scripts/backfill.py /tmp/test-lib    # small trial first
```

Per file: OCR to SRT sidecars, then a `mkvmerge --no-subtitles` remux that
is only kept after verifying zero subtitle streams and unchanged
audio+video — a failed file keeps its tracks and lands on the retry report
`backfill-report.txt` in the library root. Extras trees are never touched;
finished files and files with sidecars already present are skipped, so the
run is resumable. Tools are probed up front (seconv, tesseract eng+dan,
mkvmerge, ffprobe) and the run refuses to start without all of them.

The BC-250 is retired from the transcode path; ADR 0010 records the path
and its 2026-11 hardware revision (the laptop-only amendment).

Related: [setup](setup.md) · [nas-layout](nas-layout.md) · [video](video.md) · [tools](tools.md)
