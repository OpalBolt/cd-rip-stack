# cd-rip-stack

Rip CDs to AccurateRip-verified FLAC. Rip DVDs and Blu-rays to lossless MKV, then transcode to 1080p H.265 for Jellyfin. Two machines share one NAS mount: `rip/` stages lossless MKVs, `library/` holds the encodes.

## Language

**Movie**:
A single film. Stages as `<Movie (Year)>_tNN.mkv`; files under `library/<Movie (Year)>/`.
_Avoid_: film, video

**TV show**:
A show with seasons and episodes. Stages as `<Show (Year)>_SNNENN.mkv`; files under `library/TV/<Show (Year)>/Season NN/`.
_Avoid_: series

**Declared type**:
The movie-or-TV intent, stated by invoking `rip-movie` or `rip-tv`. Authoritative over disc metadata, which never decides it — for a TV pick, metadata only prefills the search.
_Avoid_: detected type, pick type

**Disc title**:
One numbered video blob MakeMKV reports on a disc after the `--minlength` scan. A disc title is not an episode.
_Avoid_: track, chapter

**Season**:
A TMDB-numbered season of a TV show. Season 0 is specials. Never inferred from disc structure.
_Avoid_: volume, part

**Episode**:
One aired unit of a TV show, numbered `S02E05` per TMDB. Numbers come from duration matching or the sequential fallback — never from disc title numbers, never from command-line flags.
_Avoid_: title

**Duration matching**:
The pre-rip proposal assigning each disc title to the nearest-runtime unmatched episode, largest title first. A proposal only — confirmed or corrected at the table, never final.

**Episode map**:
The disc-title → episode assignment behind a rip (`0=1, 1=2-3`). Built by duration matching or the sequential fallback; corrected at the table with fix rows, never hand-authored.
_Avoid_: mapping string

**Season folder**:
The library folder holding one season's episodes: `Season 01` under the show's `library/TV/` dir. Season 0's folder is named `Specials`.

**Double episode**:
Two episodes on one disc title. Kept as one file, named `S01E01-E02`. Never split.
_Avoid_: split episode

**Main feature**:
The biggest-bytes `_tNN` group of a staged movie. Movies only — never computed for a TV show.
_Avoid_: main title

**Extras**:
Bonus features staged `_eNN[.pN].mkv`; filed under `extras/` beside the movie or show. An aired special is not an extra — it is a Season 0 episode.
_Avoid_: bonus, featurette, special

**Junk label**:
MakeMKV's throwaway staging name (`B1_t00.mkv`). Renamed away after the rip; transcode skips any file still wearing one.
_Avoid_: label, code

**Staging**:
Finished lossless rips waiting for their next step. Video stages under `/mnt/videos/rip/movies/` or `rip/tv/` — the subdirectory is what tells transcode a file is TV. Music stages under `/mnt/music/rip/` until `tag` files it into the library. A mid-rip file still wears its junk label.
