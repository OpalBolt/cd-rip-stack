# cd-rip-stack

Rip CDs to AccurateRip-verified FLAC. Rip DVDs and Blu-rays to lossless MKV, then transcode to 1080p H.265 for Jellyfin. Two machines share one NAS mount: the rip box (Linux) rips; the transcode box encodes. `rip/` stages lossless MKVs, `library/` holds the encodes.

## Language

**Movie**:
A single film. Stages as `<Movie (Year)>_tNN.mkv`; files under `library/movies/<Movie (Year)>/`.
_Avoid_: film, video

**TV show**:
A show with seasons and episodes. Stages as `<Show (Year)>_SNNENN.mkv`; files under `library/tv/<Show (Year)>/Season NN/`.
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
The default disc read for TV (and a movie `-i` rip-all run): `lsdvd` straight on the device (DVD — no mount, no sudo) or `ffprobe bluray:` (BD). Yields titles as durations in seconds, in ~seconds — no sizes, no MakeMKV numbers. A default movie run skips it for the full scan (ADR 0015).
_Avoid_: quick scan, pre-scan

**Full scan**:
The fallback `makemkvcon info` run, fired when the fast scan fails, finds two titles within ±1s (duplicates/decoy obfuscation), or a movie `-T` menu is requested — and for every default movie rip (ADR 0015: the main-only selection needs MakeMKV title numbers). Gives MakeMKV title numbers, sizes, languages, and segment maps, so the rip runs per-title with no duration join.
_Avoid_: deep scan, makemkv scan

**MakeMKV key**:
The `app_Key` activation string in `~/.MakeMKV/settings.conf` on the rip box. The free beta key rotates roughly monthly (forum.makemkv.com topic t=1053); a purchased key is permanent. An expired key blocks BD ripping only — DVDs stay free, CDs never touch MakeMKV.
_Avoid_: license, registration, serial

**Eligible title**:
A disc title that can become an episode: at or above the TV floor (`max(RIP_MINLENGTH, 600)`, default 600s) and not sum-disqualified. Feeds the episode guess's matching pool and the position assignment.
_Avoid_: candidate title

**Play-all title**:
A disc title whose duration equals the sum of a subset of other eligible titles (±120s) — a compilation, never an episode, never ripped: fast-scan detection escalates to the full scan (ADR 0002) so the rip selects around it, and a fix row assigning it an episode still rips it. The lone title of a play-all-only disc never matches this sum — it is not this term.
_Avoid_: compilation, sum title

**Season**:
A TMDB-numbered season of a TV show. Season 0 is specials. Never inferred from disc structure.
_Avoid_: volume, part

**Episode**:
One aired unit of a TV show, numbered `S02E05` per TMDB. Numbers come from the stated episode range plus disc position — never from disc title numbers, never from command-line flags.
_Avoid_: title

**Episode guess**:
The pre-filled episode range at the range prompt, walked greedily from the first unstaged episode (discs are ripped in order, so staged episodes are a contiguous prefix): each episode claims the first remaining eligible title with a green disc÷TMDB ratio, and the walk stops at the first episode with no fitting title or no runtime. A suggestion only — shown in the title overview before the prompt with (est.) marks; the stated range plus disc position still builds the map (ADR 0001). Without runtimes or matches, the prefill falls back to one episode per eligible title.
_Avoid_: auto-detect, count guess

**Ratio audit**:
The per-title sanity check: disc duration ÷ TMDB runtime. Green when the ratio fits the PAL fingerprint (~0.92–1.04 — PAL discs run ~4–5% short); yellow otherwise. Durations grade assignments; they never make them.
_Avoid_: duration matching

**Episode map**:
The disc-title → episode assignment behind a rip (`0=1, 1=2-3`). Built by the range prompt plus position assignment; corrected at the table with fix rows, never hand-authored.
_Avoid_: mapping string

**Season folder**:
The library folder holding one season's episodes: `Season 01` under the show's `library/tv/` dir. Season 0's folder is named `Specials`.

**Double episode**:
Two episodes on one disc title. Kept as one file, named `S01E01-E02` — never split, except by the Chapter split when the disc is a play-all-only disc.
_Avoid_: split episode

**Play-all-only disc**:
A disc whose only eligible title carries every episode as chapter ranges — no per-episode titles exist to sum, so play-all detection never flags it; the shape itself (one eligible title, assigned a range) is the signal. Ripped as one title, then divided by the Chapter split.
_Avoid_: one-title disc, chapter disc

**Chapter split**:
The post-rip divide of a play-all-only disc's combined file into per-episode files. Chapter starts graded against summed TMDB runtimes suggest each boundary; a table after the rip confirms them (`2=14` = episode 2 starts at chapter 14, 1-based); `ffmpeg -c copy` cuts losslessly at the keyframe-aligned chapters; the combined file is deleted once every piece exists. A declined table, a missing runtime, or any failure keeps the combined `_S01E01-E03` file — the split never loses bytes.
_Avoid_: episode split, cutting

**Main feature**:
The staged title chosen as the film itself — the candidate main nearest TMDB runtime when one clearly matches, or the pick recorded in the validation file when several do; biggest-bytes is only the fallback for legacy folders and shorts. Movies only — never computed for a TV show.
_Avoid_: main title

**Part**:
One disc's share of a movie split across discs. Staged `.pN`; a main feature's parts merge into one file at transcode (track sets must match first). Parts holding the same track set in a different order — the Two Towers discs staged their eng/dan subtitles differently — align by stream-copy remux into part 1's order before the merge, the guard re-running on the remuxed files; a different set, or one still mismatched after alignment, quarantines whole. Extras' parts stay split.
_Avoid_: side, disc file

**Extras**:
Bonus features staged `<Movie (Year)> - Extra NN[.pN].mkv`; filed under `extras/` beside the movie or show. A default movie run never rips them (ADR 0015) — they stage only from `-i`, `-E` or `-T` runs. An aired special is not an extra — it is a Season 0 episode.
_Avoid_: bonus, featurette, special

**Extras marker**:
The validation.json `extras` key (`include`, `quality`) that decides a movie's extras at transcode. `rip movie -i` both rips the disc's extras and marks them (ADR 0015) — a default run rips no extras at all, so unmarked extras reach staging only from `-E`/`-T` runs and legacy rips. Unmarked (`include: false`) they never encode: the source is disposed with the main (deleted, or `-k` parks it). Marked, they encode at `quality` — `superfast` (default) or `full` (the main's preset); a hand edit after `scripts/backlog-mark.py` marks older rips. `scripts/sync-marks.py` propagates the library copy's marker to rip and lossless.
_Avoid_: extras flag, keep flag

**Session cap**:
One `skive transcode` run by default encodes every staged main feature — unlimited is the default (`-n N` takes a deliberate slice; the rest stays in staging, deferred loudly with a count). Stated once with a deliberately vague estimate (2–10 min per main on the transcode box) — never per title. A main already encoded in the library never counts against the cap.
_Avoid_: batch size, throttle

**Already-in-library skip**:
Transcode never re-encodes what the library holds: a staged main (or TV episode) whose library file already exists is skipped before any copy or encode — the staged sources park under `lossless/<type>/<Name (Year)>/`, and a source lossless already holds is a duplicate, deleted. Unmarked extras ride the skip's disposal as they ride a shipped main; the skip logs one note per unit naming every file's fate (parked/deleted) and never burns a session-cap slot. Extras (rolled ` - Extra NN` names) and marked extras are not covered.
_Avoid_: dup skip, re-rip skip

**Junk label**:
MakeMKV's throwaway staging name (`B1_t00.mkv`). Renamed away after the rip; transcode skips any file still wearing one.
_Avoid_: label, code

**Transcode box**:
The machine that runs `skive transcode` over the NAS mount — the Ryzen AI 7 350 laptop in its distrobox, the only transcode box: the BC-250 no longer transcodes. Encodes via ffmpeg `hevc_vaapi` on the laptop's VCN hardware (`-e auto`'s resolution; ADR 0010 records the path and its 2026-11 hardware revision). The box is self-contained: every tool it needs — ffmpeg, mkvmerge, tesseract, seconv, and skive itself — lives in the container, built by the repo's `scripts/setup-transcode.sh`; mise stays a rip-box tool.
_Avoid_: encoder box, GPU box, BC-250

**Prefetch**:
The transcode copy overlap: as soon as one unit's own copy finishes, the next unit's source starts pulling from the share into the workdir, overlapping the current unit's subs, crop and encode. One unit of lookahead only — one encode runs at a time, and the finished encode's ship (move to the library, sidecars, doc, source disposal) runs in one background thread, one deep, overlapping the next unit's encode. A failed ship fails that item, keeps the local encode and leaves the source in staging. The ship's time is its own `ship` phase in the run log and summary. Its start and its finish (`ready`, with size, rate and time) are event lines in the current item frame. Without workdir headroom for the next source, that item falls back to the sequential copy with a loud note.
_Avoid_: download, upload, double-buffering

**Crop gate**:
The rule that decides a title's black-bar crop at transcode: sampled detection (~6 probes spread over 10–90% of the file), per-side bar as the median across probes, at most one probe per side dissenting beyond ±4 px (a lone dissent is a credits or dark-scene outlier), sides under 16 px crop as nothing, per-axis totals capped at 30%. Unstable or over-cap detection means no crop plus a loud note; applied values are recorded in the run log and validation.json. Applies to everything encoded — mains, TV episodes, extras — on every encode path.
_Avoid_: autocrop, letterbox removal

**Staging**:
Finished lossless rips waiting for their next step. Video stages under `/mnt/videos/rip/<type>/<Name (Year)>/d<disc>/` — one folder per title, `d<disc>` is the ripping drive so parallel drives never collide on junk labels; promotion moves finished files up into the `<Name (Year)>` folder. The `<type>` level (`movies` or `tv`) is what tells transcode a file is TV. Music stages under `/mnt/music/rip/<music source>/<album>/` — one folder per album, the source level tells `tag` how to file it; `tag` moves it into the library, so an album still in `rip/` is not yet filed. A mid-rip file still wears its junk label.

**Music source**:
Where an album came from: `cd` (a cyanrip rip), `bandcamp` (a download), or `other` (any other download). The first folder under `/mnt/music/rip/`; `cd` and `other` are autotagged by beets against MusicBrainz, `bandcamp` keeps its own tags (`beet import -A`) but still gets the library path, ReplayGain and art.
_Avoid_: ingestion method, origin

**Lossless folder**:
Where `-Keep` parks lossless sources after transcode, until the encode is verified by eye and purged by hand: `lossless/<type>/<Name (Year)>/`. Never clobbered, never auto-deleted. Replaces the old flat `rip-DONE`.
_Avoid_: rip-DONE, done folder

**Issues folder**:
Quarantine for lossless sources whose transcode or subtitle pass failed: `issues/<type>/<Name (Year)>/` — the source plus that run's `rip.jsonl`/raw log lines, contained together. `transcode.py --retry` re-runs items from here. Nothing in the pipeline deletes a source until its sidecars are verified — a failed file waits here for retry, never in `lossless/` (which is verified-and-pending-purge).
_Avoid_: failed folder, retry queue

### Validation

**Validation file**:
`validation.json`, born in the title folder at rip: TMDB id/type/runtime, volume label, per-title durations and check results, plus the chosen main. A TV show holds one document per show — every season with its episodes (the current actuals) and its disc index (volume label, run, the episodes each rip run staged). Travels staging → transcode → library (TV: into each Season folder beside the encodes); transcode appends its encode results to it. Pre-feature rips get theirs reconstructed by `scripts/backfill_validation.py` (dry-run default, `--apply` writes). An album holds a `kind: "album"` document: music source, release identity (MusicBrainz id or Bandcamp URL), per-track file/duration/format, and a disc index (run, drive offset, per-track AccurateRip status, confidence and CRC32 for `cd`). A CD rip's record is born complete in `rip/cd/<album>/` — cyanrip's report names the release, every track, and each checksum; downloads get theirs at filing. The filing step stamps each disc's filing facts, rebuilds the track list from the filed files, and carries the document into the library album folder together with cyanrip's `.log`/`.cue`. AccurateRip results are proof, never a gate: a disc missing from the database still rips and exits clean, with its not_found tracks named in the run log. Pre-feature albums get a `reconstructed` one (source `unknown`, no AccurateRip).
_Avoid_: manifest, metadata file, verification.json

**Candidate main**:
A movie-rip title at or above the main floor (2400 s). One candidate near the TMDB runtime names the main outright; several near it demand a human pick (default: biggest) before the drive spins.
_Avoid_: main title, feature-length title

**Library checker**:
The consistency pass over `library/` (`skive validate`): durations vs TMDB, duplicate ids, stray staging names (`_tNN` and old `_eNN` labels), missing episodes. Ratio rows carry a severity — red once the deviation exceeds what a PAL-vs-NTSC conversion explains — and a Δ-from-norm value. `--json` emits the report as data; `--issues` shows only the problems plus a per-folder action plan. Report-only except `--fix`, the one mutation: it renames staging-labelled `extras/` files to the ` - Extra NN` form.
_Avoid_: lint, audit (that name belongs to the ratio audit)

### Search

**Ripped search**:
The `skive search <scope> QUERY` answer to "have I already ripped this?" — scope is required: `movie`, `tv`, `video` (both), `music`, or `all`. A case-insensitive name match across every on-disk root of the scoped shares (`library/`, `rip/`, `lossless/`, `issues/`), each hit reported with its state (staging, library, lossless, issues); TV hits list their seasons, album hits match artist or album folder names and staging hits name their music source. Zero hits answer with a did-you-mean suggestion. Always exit 0, never a database — the directory walk is the index.
_Avoid_: library search, catalog, index

**Rip preflight**:
The duplicate gate fired once the run's name is settled — after the movie pick (inside the identity gates) and after the TV season prompt: an exact-name title folder on disk escalates the confirm to default-No (bare Enter refuses, explicit `y` rips anyway). TV staging never escalates — staged episodes are the interrupted-re-rip path; a TV show on disk with other seasons only informs; `-E` extras runs skip it — adding to an owned title is the normal case.
_Avoid_: duplicate check, pre-scan

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

**Barren track**:
A kept bitmap track whose OCR yields zero cues — every image dropped as unreadable, seconv reporting no matching tracks. Drops from the pass with a loud note and the encode ships without that sidecar; only a tool failure (seconv/tesseract crash) quarantines. A deterministic re-failure must never sit in `issues/` forever.
_Avoid_: empty track, blank track

**Subtitle pass**:
Extraction + OCR + naming that turns a main feature's or TV episode's tracks into sidecars. Runs inline at transcode for new rips; a one-off backfill covers the existing library.
_Avoid_: subtitle extraction, sub rip

### Logging

**Event line**:
skive's own line about what a run is doing — four kinds, each a symbol on a TTY and a word when plain: `ok` (`✓`, an item finished), `info` (`·`, routine events, skips, deferrals), `warn` (`!`, the loud note: something off, run continues), `fail` (`✗`, something failed). Always stderr and always kept in scrollback; `ok`, `warn` and `fail` also land in the run log, so screen and record never disagree. stdout carries only data answers (`search` hits, `--json`). skive has no bare `print` — every line is an event line, a console table or prompt, or a data answer.
_Avoid_: log message, print

**Run view**:
The console the long-running commands draw while they work — `rip movie`, `rip tv`, `rip music`, `transcode` — as framed scrollback: a run opener box, then one item frame per item streaming its event and tool lines into ordinary scrollback, and a closed live box redrawing beneath them (the situation: current item, progress bar, ETA, background activity such as the prefetch copy, the perf line). Interactive moments — pick tables, fix-row prompts — suspend the live box; the run's end removes it and prints the run summary. Piped, `NO_COLOR` or `TERM=dumb`, the same event stream renders as plain tagged lines with no box characters. Never gates the durable logs — run log and raw log are always written.
_Avoid_: TUI, dashboard, progress screen

**Run opener**:
The closed box a run view prints when its work is settled, stating the run's scope — for transcode the session count against the queue (`4 of 63 this session (cap 4, ~8–40 min) · 59 deferred`), the encoder choice and the scan-skip count, with each skipped path as a `!` line right under the box; for rips `Andor (2022) S01 · disc 2 · /dev/sr0`. Nothing of the run prints above it. Rips print it after their interactive phase (search, picks, map), since the scope is unknown before.
_Avoid_: banner, header

**Item frame**:
The frame a run view draws in scrollback around one item: a header rule `╭─ 1/4  Bad Boys (1995) ───╮` when the item starts (position counts this session's items, not the whole queue — a rip's disc title, a music track), its event lines between `│ … │` sides, and a closing rule `╰─ ✓ done 7m56s ───╯` carrying the item's result. Every line is drawn whole at the shared width, so it streams line by line.
_Avoid_: item heading, section, separator

**Run summary**:
What prints when a run view's live box clears: warn/fail event lines replayed, then one rounded box titled `summary` holding a table of the run's items — transcode: item × copy/merge/subs/encode timings × total × result (a prefetched copy shows its real time marked `⇢`); rip movie/tv: title × duration × size × result; rip music: track × duration × result — columns empty for every item dropped, the result column a symbol plus word. The box's bottom title is the closing result line: counts, total time, and after a failure where to look next (`✗ 1 failed · 3 ok · 2h41m — see logs/run/….log`). The `--keep` banner prints under the box. The one-glance outcome of a run; the full story stays in the run log.
_Avoid_: final report, recap

**Perf line**:
The transcode run view's live-box lane: whole-system CPU % (a /proc/stat delta) plus the CPU and GPU package temperatures (hwmon), whichever sensors are readable — `CPU 62% · cpu 78°C · gpu 61°C` — red at ≥ 95 °C (thermal-throttle territory on the encode box). Transcode only. Never prints in the plain-line fallback; an unreadable sensor is omitted, never warned — a missing number is not a failure.
_Avoid_: system monitor, sensor readout

**MusicBrainz miss**:
A `rip music` run that stops before ripping because cyanrip can't resolve the disc's DiscID to one release. There are three cases. **Not found**: the DiscID is on no release. **Stub**: it matches only a CD stub. **Multiple releases**: it is attached to several releases. skive never prompts. It fails the run with a yellow box naming the case, the DiscID, and the way forward: the attach URL (attach the disc to its release on MusicBrainz, then re-run), or for multiple releases, the candidates, the disc's MusicBrainz page to detach wrong releases, and the `-R <n>` re-run command. Once MusicBrainz is fixed, later rips of that disc resolve on their own.
_Avoid_: lookup error, metadata failure

**Run log**:
The durable event record both machines append to over NFS: `logs/rip.jsonl` on the videos share for video runs, on the music share for music runs (`rip music`, `tag` — a `tag` run also logs one `filed`/`left` line per album) — one JSON object per line (`ts, run, script, software, name, phase, status, elapsed_s`). A `rip music` run logs `name` as `-` until cyanrip reports the disc's identity, then carries `Album artist - Album` from cyanrip's header on every later line; a failed run keeps `-`. A transcode run logs one timed line per part — `copy`, `merge`, `subs`, `encode` — each with its own `elapsed_s` and the part's numbers in `status` (`5744 MB at 64 MB/s`), so copy slippage and encode slippage are separable trends; a phase that dies mid-run closes with `FAIL` and its elapsed. Replaces the old 8-column text `rip.log`.
_Avoid_: verification log, event log

**Raw log**:
Verbatim tool output for one run, captured while it runs: `logs/raw/<run>.jsonl`. Deleted on a clean exit; kept when a run fails or is interrupted. A rip that stages nothing fails even at makemkvcon rc 0 — MakeMKV reports aborted title saves (BD read/hash errors) as messages, not exit codes — so the raw log naming the error survives. Verbosity (`-v`, `-vv`) only gates the console — both logs are always written.

**Scan dump**:
A full scan's verbatim `makemkvcon info` output, persisted the moment the scan runs: `logs/scan/<run>_<volume label>.txt`. Never deleted — a disc's structure is in hand exactly once, while questions about it surface later at a table or a stuck prompt, when the disc may already be out of the drive. Replays through the scanner with `MAKEMKV_INFO="cat <dump>"`, so a questioned table is reproducible without the disc.
_Avoid_: scan log, issue log

**Interrupt**:
What Ctrl+C means everywhere: the child tool is signalled (up to 5 s, then killed), the in-flight partial file is deleted — no partial rip is ever left hanging — completed renamed titles are kept, `rip.jsonl` records `status=interrupted`, and the process exits 130. A second Ctrl+C exits instantly without cleanup; the next run's junk purge sweeps what's left.
_Avoid_: cancel, abort
