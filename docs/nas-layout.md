# NAS layout

Two NFS shares hold everything: `/mnt/videos` for video, `/mnt/music` for
music. Each tree has the same shape — `rip/` staging feeds `library/`, with
mirrors beside it and `logs/` for the run logs. Where a file sits tells you
where it is in its pipeline.

## Videos share — `/mnt/videos/`

```text
/mnt/videos/
  rip/<type>/<Name (Year)>/         staging: ripped lossless files waiting for transcode
    d<disc>/                        the ripping drive's subdir — parallel drives never collide on junk labels
  library/movies/<Movie (Year)>/    finished encodes + extras/
  library/tv/<Show (Year)>/         Season NN/ (season 0 = Specials/) + extras/
  lossless/<type>/<Name (Year)>/    lossless sources parked by transcode --keep (verify the encode, then purge by hand)
  issues/<type>/<Name (Year)>/      failed transcodes: the source + that run's rip.jsonl lines and raw log
  logs/rip.jsonl                    the run log — video rips, transcode and sync-sd all append here
  logs/raw/<run>.jsonl              one run's raw tool output (deleted on a clean exit)
  logs/scan/                        scan dumps, never deleted
```

`<type>` is `movies` or `tv`, and it is positional: the first path segment
under `rip/` is what tells transcode a file is TV.

Staged file names:

- `<Movie (Year)>_tNN.mkv` — a title; `_tNN.pN.mkv` is one half of a
  split-film part
- `<Movie (Year)> - Extra NN[.pN].mkv` — extras
- `<Show (Year)>_S01E02.mkv` — an episode; `_S01E01-E02.mkv` is a double
  episode kept as one file

Point Jellyfin at `/mnt/videos/library` — the single library root.

## Music share — `/mnt/music/`

```text
/mnt/music/
  rip/<music source>/<album>/       staging: albums not yet filed
  library/<Artist>/<Year Album>/    filed albums — one flat folder of tracks per album, by album artist
    _Singles/<Artist>/              one-off tracks, filed by track artist
    _Soundtracks/<Year Album>/      soundtracks
    _Compilations/<Year Album>/     compilations
  music.db                          the beets database
  logs/rip.jsonl                    the run log — rip music and tag append here
  logs/raw/<run>.jsonl              one run's raw tool output
  logs/import.log                   beets' own import log
```

The library is by artist, not by music source: `tag` files every album under
its album artist ("The"-prefixes sort under the next word), with the year
prefixed so folders sort chronologically. Tracks drop straight into the album
folder — disc subfolders are dropped.

The `<music source>` level is one of `cd` (a cyanrip rip), `bandcamp` (a
Bandcamp download) or `other` (any other download). It decides how `tag`
files the album: `cd` and `other` are autotagged against MusicBrainz,
`bandcamp` keeps its own tags as-is. Either way the album gets the library
path, ReplayGain and art.

## Staging: junk label → rename → promotion

The video rip's three steps, once a disc is in the drive:

1. MakeMKV writes files under their junk label — its throwaway staging name
   like `B1_t00.mkv` — inside the drive's `d<disc>/` subdir.
2. When the rip finishes, files are renamed away from the junk label to
   their staged names (above).
3. Promotion moves each finished file up out of `d<disc>/` into the title
   folder. A name collision leaves the file where it is — never clobbered.

Transcode skips any file still wearing a junk label, so a mid-rip file never
enters the queue. What "still in `rip/`" means per pipeline:

- Video: ripped lossless, not yet transcoded — `rip/` is transcode's input
  queue.
- Music: not yet filed — `tag` moves the album into the library (beets
  `move: yes`), so an album still in `rip/` is not in the library yet.
  Music has no junk labels or `d<disc>/`; only the album folder under its
  music source.

## The mirrors: lossless/ and issues/

Both mirror the staging tree (`<type>/<Name (Year)>/`), and both exist so a
lossless source is never destroyed before its encode is proven.

- `lossless/` — `skive transcode --keep` parks the lossless source here
  after the encode ships. Nothing in the pipeline ever touches it again:
  verify the encode by eye, then purge by hand.
- `issues/` — a transcode or subtitle-pass failure quarantines the lossless
  source here together with that run's `rip.jsonl` lines and raw log, so
  everything about one failure sits in one folder. `skive transcode
  --retry` walks `issues/` instead of `rip/`; the source leaves only on
  success.

The `logs/` files are explained on [setup](setup.md). Vocabulary and the
per-title tree decision live in CONTEXT.md and
[ADR 0007](adr/0007-per-title-folder-tree.md) — the one pointer from here.

Related: [setup](setup.md) · [video](video.md) · [music](music.md) · [transcode](transcode.md)
