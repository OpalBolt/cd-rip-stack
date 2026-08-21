# TV episodes — spec

Rip, stage, transcode, and file TV discs so Jellyfin shows seasons and episodes natively. Everything below was settled in a design session on this repo; the terms live in `CONTEXT.md`.

## Problem

`rip-video` and `transcode-video` treat every disc as a movie. A TV disc stages its episodes as `<Show (Year)>_tNN.mkv`. Transcode picks the biggest `_tNN` group as the main feature and files the rest as extras. Jellyfin sees one movie with many extras.

## Non-goals

Rejected in the session. Do not build them.

- **Full-disc cloning** (`makemkvcon backup --decrypt`). Skipped. Do not document it either.
- **Chapter splitting** of double episodes. One title stays one file.
- **Episode titles in filenames.** Jellyfin scrapes its own metadata. The `SNNENN` code is enough.
- **A new runtime or language.** The pipeline stays bash + PowerShell.
- **`--map` / `--ep` / `--season` flags.** No user authors a map by hand. Everything goes through prompts with a proposal already on screen.

## Settled decisions

1. **Jellyfin-native tree.** Episodes land as `S01E02`-style files under season folders. TV gets its own `library/TV/` subtree because Jellyfin wants one content type per library.
2. **Episode numbers come from TMDB, matched by duration.** Disc titles carry no episode numbers. Duration matching proposes. The user confirms.
3. **Decisions happen before the disc leaves the drive.** A pre-rip scan and a confirm table run inside `rip-video`. Wrong episode numbers after a rip are always fixable by rename, so no step needs the disc back.
4. **The confirm table is the only correction UI.** Fix rows at the `ok?` prompt re-point single titles. No flags.
5. **The staging dir is the state.** Matching excludes episodes already staged for that show and season. Nothing is stored anywhere else.
6. **The subdirectory is the type.** TV stages under `rip/TV/`. Transcode routes by parent dir, not by filename grammar.
7. **Bash stays.** If `rip-video.sh` passes ~500 lines or needs chapter splitting, move rip orchestration — not transcode — to Python. Record that ceiling as a `ponytail:` comment in the code.
8. **One `rip-video` command, no split.** The disc type is discovered, not known — it emerges from the TMDB pick. The TV flow is a data-driven branch inside `rip-video` after the pick returns `tv`. A `rip-movie`/`rip-tv` split would duplicate the shared mount, media gate, junk purge, rip loop, profile, rename, and logging.
9. **No `--menu` flag, no second UI.** The menu layer (`rip-menu`) and the prompt layer (inside each script) already exist. TV adds prompts, not flags: pick show, pick season, confirm table. The `Pending:` count needs no change — its `find` is already recursive over `rip/`.

## Rip flow

`rip-video` gains a TV branch. The movie branch does not change.

1. `movie-name --tmdb` resolves the disc as today. Its TSV already carries `movie|tv` — emit that type field through to `rip-video`.
2. If the pick is `tv`, prompt for a season number. Enter means 1. Zero means specials (season 0).
3. Refuse `--part` on a TV pick. TV episodes never merge. A two-part story on two discs stays two files; number them E05/E06 through the table.
4. Scan the disc with `makemkvcon info --minlength=$RIP_MINLENGTH`. Honor `MAKEMKV_INFO` the way `titles.sh` does. Keep the shared minlength value in sync.
5. Fetch the season: `GET /3/tv/{id}/season/{N}`. It returns `episode_number`, `name`, `runtime`.
6. Build the candidate set. Drop episodes already staged for this show and season — glob `rip/TV/<Show (Year)>_S<NN>E*.mkv`. A second disc then proposes E05 onward with no stored state.
7. Match greedily. Sort disc titles by duration, longest first. Each title claims the unused episode with the nearest runtime. Compare the distance to the best single against the best consecutive pair — the nearer one wins, and a pair stamps a double episode. A title left with no claim becomes an extra. Leave the exact double threshold as a tuning knob at the top of the matcher.
8. If TMDB returns no runtimes for the season, fall back to sequential. Ask "first episode on this disc?", then propose title order equals episode order. Same table, same fixes.
9. Print the table: title number, duration, size, the proposed episode (with the TMDB episode name, for the eye only), or `→ extra`.
10. At the `ok?` prompt: Enter rips as proposed. Fix rows like `3=7` or `1=1-2` re-point one title, then the table re-prints and asks again.
11. Rip as today — same per-movie audio selection profile, same burned-media gate, same junk purge (extended to `rip/TV/`).
12. Stage under `rip/TV/`. Rename staged files through `movie-name --rename-as` extended with a machine-generated episode map; the confirmed table produces the map string, so the user never types one.

### Staged names

```text
rip/TV/<Show (Year)>_S02E05.mkv          episode
rip/TV/<Show (Year)>_S01E01-E02.mkv      double episode
rip/TV/<Show (Year)>_eNN.mkv             TV extras disc (--extras) or unmatched featurette
```

## Transcode routing

Both transcoders — `lib/transcode-video.sh` and `windows/transcode-video.ps1`.

- A staged file under `rip/TV/` is TV. Route it to `library/TV/`. Everything else is a movie and keeps today's behavior.
- TV mode skips the main-feature logic and all merging. Each episode file transcodes as-is.
- Episode files land at `library/TV/<Show (Year)>/Season NN/<Show (Year)> - S01E02.mkv`. Zero-pad from the staged `SNNENN`. Season 00 uses the folder name `Specials`.
- TV `_eNN` files land at `library/TV/<Show (Year)>/extras/`.
- Extend the junk guard regex to accept the `_SNNENN` pattern alongside `_tNN`/`_eNN`. A file without `(Year)` is still skipped.

## Edge cases

| Case | Behavior |
|---|---|
| Second disc of a season | Staged-episode exclusion proposes the next episodes. |
| TMDB runtimes missing | Sequential fallback after asking the first episode number. |
| Specials disc | Season 0 at the prompt. Files under `Specials/` as `S00E05`. |
| Featurette among episodes | Table lists `→ extra`. Stages `_eNN`. The titles spec can still exclude it. |
| Double episode | One file, `E01-E02` range name. Jellyfin reads the range. |
| `--extras` on a TV disc | Allowed. Stages `_eNN`, files under the show's `extras/`. |
| `--part` on a TV pick | Hard error. |
| Offline / no TMDB key | Hard error before the drive spins. Unchanged. |
| Failed rename after rip | Report it. The junk purge on the next run cleans staging. |

## Files touched

- `lib/movie-name.sh` — emit the `movie|tv` type; extend `--rename-as` with the episode-map form.
- `lib/rip-video.sh` — TV branch: season prompt, scan, season fetch, matcher, table, fixes, staging under `rip/TV/`.
- `lib/transcode-video.sh` — `rip/TV/` routing, episode regex, season folders.
- `windows/transcode-video.ps1` — same routing and filing.
- `lib/rip-menu.sh`, `lib/rip-menu-help.txt` — help text and the `rip-video` arg hint mention the TV prompts (no flags to hint). The `Pending:` count already recurses into `rip/TV/`.
- `README.md` — `rip-video`, `transcode-video`, NAS layout, `movie-name` sections. Each slice updates the sections it touches.

## Checks

- Extend `windows/test-transcode.sh` with TV fixtures: staged episodes, a double, a `_eNN`, and a junk label under `rip/TV/`. Assert the season folders, the `Specials` name, and the routing.
- The matcher is one function. Give it one assert-based check that fails if the greedy order or the double rule breaks.

Rough size: 200–250 lines of bash plus ~40 lines of PowerShell across the files above.
