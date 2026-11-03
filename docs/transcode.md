# Transcode

`skive transcode` drains `rip/` staging into the Jellyfin library: each staged
file is pulled local, merged, subtitle-passed, re-encoded to H.265, and filed
under `library/` (trees: [nas-layout](nas-layout.md)).

## The box

The transcode box is an AMD BC-250 — the PS5-derived Cyan Skillfish board
(Zen 2, 8 cores unlocked, 16 GB GDDR6) running Bazzite. Its video engine
cannot encode: the VCN block is factory-unprovisioned and eFused off, and
Sony blocks the firmware, so no tool ever gets hardware encode on this
machine. HandBrake on Linux has no VAAPI encoder at all, and the community
`bc250-vaapi` shim was evaluated and rejected — its H.264 is libx264 on the
CPU in a costume, its HEVC a CBR streaming encoder shaped for game streaming,
not quality-based archival. The conclusion is one sentence: **only `x265`
exists on this box** — `-e nvenc/qsv/vce` are dead weight here, the GPU idles.

Build it:

```bash
git clone <this repo> && cd cd-rip-stack
mise run install                  # python, uv, skive, seconv, ffmpeg on PATH
distrobox create -n transcode --image fedora:41
distrobox enter transcode         # all skive runs happen in here
sudo dnf install https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
sudo dnf install HandBrake-cli mkvtoolnix \
    tesseract tesseract-langpack-eng tesseract-langpack-dan
```

HandBrakeCLI comes from RPM Fusion; mkvtoolnix and tesseract (with eng+dan
language data) from the Fedora repos — skive finds all of them on PATH inside
the distrobox. The videos share mounts at `/mnt/videos` and the box uses the
same env vars as the ripping machine — see [setup](setup.md). Runs are
invoked by hand; nothing schedules them.

## Running a batch

```bash
skive transcode                     # drain all of rip/
skive transcode 'Film (2001)'       # only titles whose staged name starts with this prefix
skive transcode -k                  # keep lossless sources in lossless/ instead of deleting
skive transcode --retry             # re-run quarantined items from issues/
skive transcode -w /var/tmp/video-work   # workdir override (default: $TMPDIR/video-work)
```

- **Input** — `rip/` staging (`issues/` with `--retry`). The first path
  segment under the root decides movie vs TV; titles without a `(Year)`
  tail are skipped as junk (mid-rip or failed rename). A prefix that
  matches nothing is called out on stderr; if nothing matched at all, exit 1.
- **Work dir** — `-w`, else `$VIDEO_WORK_DIR`, else `$TMPDIR/video-work`.
  Files are pulled local and encoded off the share.
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
- **Encode** — HandBrake re-encodes video to H.265 at native resolution,
  never upscaled (the probed height picks the preset: 480p/576p DVD —
  those also get detelecine+decomb — 720p, 1080p, 4K). Audio passes
  through (`--aencoder copy`, ac3 fallback). The `-e` GPU choices only
  change which HandBrake flags go in; on this box they fail at run time.

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

This box replaced the retired Windows transcode machine — ADR 0010 has why it
can only ever be CPU-only x265.

Related: [setup](setup.md) · [nas-layout](nas-layout.md) · [video](video.md) · [tools](tools.md)
