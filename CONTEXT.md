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

**Volume label**:
The disc's label string from the MakeMKV DRV scan line (`ROGUE_ONE`). A prefilled search query at most — never an output name, never a type.
_Avoid_: disc title, disc name

**Disc title**:
One numbered video blob the disc scan reports — lsdvd/ffprobe on the fast path, MakeMKV when the scan escalates. A disc title is not an episode.
_Avoid_: track, chapter

**Fast scan**:
The default disc read: mounted `lsdvd` (DVD) or `ffprobe bluray:` (BD). Yields titles as durations in seconds, in ~seconds — no sizes, no MakeMKV numbers.
_Avoid_: quick scan, pre-scan

**Full scan**:
The fallback `makemkvcon info` run, fired only when the fast scan finds two titles within ±1s (duplicates/decoy obfuscation) or a movie `-T` menu is requested. Gives MakeMKV title numbers, sizes, languages, and segment maps, so the rip runs per-title with no duration join.
_Avoid_: deep scan, makemkv scan

**Eligible title**:
A disc title that can become an episode: at or above the TV floor (`max(RIP_MINLENGTH, 600)`, default 600s) and not sum-disqualified. Feeds the range prompt's count and the position assignment.
_Avoid_: candidate title

**Play-all title**:
A disc title whose duration equals the sum of a subset of other eligible titles (±120s) — a compilation, never an episode. Auto-marked extra.
_Avoid_: compilation, sum title

**Season**:
A TMDB-numbered season of a TV show. Season 0 is specials. Never inferred from disc structure.
_Avoid_: volume, part

**Episode**:
One aired unit of a TV show, numbered `S02E05` per TMDB. Numbers come from the stated episode range plus disc position — never from disc title numbers, never from command-line flags.
_Avoid_: title

**Ratio audit**:
The per-title sanity check: disc duration ÷ TMDB runtime. Green when the ratio fits the PAL fingerprint (~0.92–1.04 — PAL discs run ~4–5% short); yellow otherwise. Durations grade assignments; they never make them.
_Avoid_: duration matching

**Episode map**:
The disc-title → episode assignment behind a rip (`0=1, 1=2-3`). Built by the range prompt plus position assignment; corrected at the table with fix rows, never hand-authored.
_Avoid_: mapping string

**Season folder**:
The library folder holding one season's episodes: `Season 01` under the show's `library/TV/` dir. Season 0's folder is named `Specials`.

**Double episode**:
Two episodes on one disc title. Kept as one file, named `S01E01-E02`. Never split.
_Avoid_: split episode

**Main feature**:
The biggest-bytes `_tNN` group of a staged movie. Movies only — never computed for a TV show.
_Avoid_: main title

**Part**:
One disc's share of a movie split across discs. Staged `.pN`; a main feature's parts merge into one file at transcode (track sets must match first), extras' parts stay split.
_Avoid_: side, disc file

**Extras**:
Bonus features staged `<Movie (Year)> - Extra NN[.pN].mkv`; filed under `extras/` beside the movie or show. An aired special is not an extra — it is a Season 0 episode.
_Avoid_: bonus, featurette, special

**Junk label**:
MakeMKV's throwaway staging name (`B1_t00.mkv`). Renamed away after the rip; transcode skips any file still wearing one.
_Avoid_: label, code

**Staging**:
Finished lossless rips waiting for their next step. Video stages under `/mnt/videos/rip/movies/` or `rip/tv/` — the subdirectory is what tells transcode a file is TV. Music stages under `/mnt/music/rip/` until `tag` files it into the library. A mid-rip file still wears its junk label.

**Issues folder**:
Quarantine for lossless sources whose transcode or subtitle pass failed: `rip-ISSUES/movies/`, `rip-ISSUES/tv/`. Nothing in the pipeline deletes a source until its sidecars are verified — a failed file waits here for retry, never in `rip-DONE` (which is temp and purged).
_Avoid_: failed folder, retry queue

### Subtitles

**Sidecar**:
A subtitle file next to a library video, named `<base>.<lang>[.<flag>].srt`. The subtitle form on main features and TV episodes; extras keep their subs embedded.
_Avoid_: external subtitle, extracted sub

**Bitmap track**:
An image-based subtitle track (PGS from Blu-ray, VobSub from DVD). Reaches a sidecar only through OCR.
_Avoid_: image sub, PGS track

**Text track**:
A character-coded subtitle track (SRT, ASS/SSA, WebVTT). Converts to a sidecar directly.

**Forced track**:
Subtitle events covering foreign-audio dialogue only, flagged by disposition at rip time. Sidecar suffix `.forced`.

**Full track**:
One language's complete dialogue track. At most one per language per video; duplicates resolve by policy, not by suffixing.

**SDH track**:
A full track carrying sound-effect descriptions, unflagged on disc. Identified by OCR output, sidecar suffix `.sdh`.
_Avoid_: HI track, captions

**Subtitle pass**:
Extraction + OCR + naming that turns a main feature's or TV episode's tracks into sidecars. Runs inline at transcode for new rips; a one-off backfill covers the existing library.
_Avoid_: subtitle extraction, sub rip
