#!/usr/bin/env bash
# rip-menu — guided entry surface for the ripping toolkit. run inside the nix
# devshell so fzf + every command is on PATH. lists the commands grouped by
# workflow with a one-line description; the focused item shows its full
# description in a preview pane, then runs the pick.
set -euo pipefail

# kanagawa palette for fzf. exported so the read-prompt below (and any nested
# fzf) shares it. roles: fg fujiWhite, bg/gutter sumiInk1, bg+ waveBlue2
# (hovered/selected row), hl springBlue, hl+ crystalBlue, info fujiGray,
# prompt oniViolet, header crystalBlue, pointer autumnRed, marker autumnGreen,
# border sumiInk4.
export FZF_DEFAULT_OPTS="--color=fg:#DCD7BA,bg:#1F1F28,bg+:#2D4F67,hl:#7FB4CA,hl+:#7E9CD8,info:#727169,prompt:#957FB8,header:#7E9CD8,pointer:#C34043,marker:#76946A,spinner:#957FB8,border:#54546D,gutter:#1F1F28"

entries=(
  # CD
  "rip — Rip a CD to FLAC"
  "tag — Tag ripped music and sort into the library"
  "sync-music — Move the library to the NAS"
  "sync-sd — Copy the library to an SD card"
  # Video
  "rip-video — Rip a disc to a lossless MKV"
  "transcode-video — Encode staged MKVs into the library"
  "titles — List a disc's titles, longest first"
  # Tools
  "movie-name — Resolve a movie name (metadata or TMDB)"
)

# full descriptions for the hover-preview pane. keyed by the command (first
# token of each entry); fzf shows field 1 in the list and field 2 on focus.
declare -A detail=(
  [rip]="Rip a CD to AccurateRip-verified FLAC. <offset> is the drive's CD sample offset (Black Blu-ray drive: 103, Sandberg: 6). Optional engine cyanrip (default) or whipper; flags pass through (cyanrip -N skips MusicBrainz, -R x re-rips; whipper runs -U). Output lands in ~/media/audio/music/rip/."
  [tag]="Run beets on the rip dir: MusicBrainz tags, cover art, Last.fm genres, ReplayGain, then copy into ~/media/audio/music/library/ (originals kept — clean rip/ after). Batch-friendly: rip many discs, then tag once."
  [sync-music]="Move the tagged library from ~/media/audio/music/library/ to the NAS (needs NFS_MUSIC). rsync --remove-source-files per folder; re-run resumes where it stopped. Mounts /mnt/music on demand."
  [sync-sd]="Copy the NAS library onto an inserted SD card (rsync --size-only; source kept). Auto-detects a mounted card, or lists removable disks to mount and unmounts on exit. Optional arg: a card mountpoint to override."
  [rip-video]="Rip a DVD/Blu-ray to a lossless MKV in /mnt/videos/rip/ via makemkvcon (needs NFS_VIDEOS). Default all titles; pass a spec like 1,3-4, or --extras for the 2nd disc of a set. DVDs prompt for a TMDB name; Blu-rays name from metadata."
  [transcode-video]="Batch-encode every staged MKV in /mnt/videos/rip/ to 1080p H.265 in /mnt/videos/library/ — run this on the desktop. Pulls each file locally to encode off NFS, uploads the result, deletes the lossless source."
  [titles]="List a disc's titles via makemkvcon, longest-first (S = scrambled/decoy cut). Run before rip-video to pick the main feature. Optional arg disc:N (default disc:0)."
  [movie-name]="Resolve a real movie name (Title, Year, Lang) for a disc or MKV — Blu-ray from disc metadata, DVD via TMDB. Usually called by rip-video; re-run with --rename <dir> to fix a botched pick."
)

# stateless: computed on open, nothing stored. pending counts watch the same
# dirs the commands operate on.
mounted() { grep -q " $1 " /proc/mounts; }   # portable — no util-linux dep

audio_rip="$HOME/media/audio/music/rip"
vid="${VIDEOS_MOUNT:-/mnt/videos}"

pending=()
if [ -d "$audio_rip" ]; then
  # find can exit non-zero (unreadable entry / NFS hiccup); the count is
  # advisory, so || true keeps a failed find (→ 0) from aborting the menu.
  n=$(find "$audio_rip" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l || true)
  [ "$n" -gt 0 ] && pending+=("$n in ~/media/audio/music/rip — awaiting tag")
fi
# ponytail: mount guard BEFORE any access — a stale/unmounted NFS share would
# hang find; skip the video count entirely rather than risk a hang or show "0".
if mounted "$vid" && [ -d "$vid/rip" ]; then
  n=$(find "$vid/rip" -name '*.mkv' 2>/dev/null | wc -l || true)
  [ "$n" -gt 0 ] && pending+=("$n staged MKVs — transcode on desktop")
fi

header="CD · Video · Tools — pick an action"
if [ "${#pending[@]}" -gt 0 ]; then
  # join with " · ": ${pending[*]} only uses the first char of IFS, so build
  # the join by hand rather than rely on IFS trickery.
  joined=""
  for p in "${pending[@]}"; do joined+="${joined:+ · }$p"; done
  header="Pending: $joined"$'\n'"$header"
fi

# feed fzf "display<TAB>detail" lines: --with-nth=1 hides the detail from the
# list (only the one-liner shows), while {2} in --preview prints the focused
# row's full description. fzf returns the full line on Enter, so ${pick_line%% *}
# still yields the command.
lines=$(
  for e in "${entries[@]}"; do
    printf '%s\t%s\n' "$e" "${detail[${e%% *}]}"
  done
)

pick_line=$(printf '%s' "$lines" \
  | fzf --ansi \
        --delimiter='\t' --with-nth=1 \
        --header="$header" \
        --preview 'echo {2}' \
        --preview-window='right:60%:wrap' \
        --height=70% --reverse --cycle)

# per-command arg hints: a non-empty hint earns a read prompt after pick; an
# empty hint runs the command immediately. the description after the em-dash is
# ignored — only the first token (the command) matters.
declare -A arg_hint=(
  [rip]="offset (required) + optional engine/flags — e.g. 6  or  6 whipper"
  [rip-video]="titles — e.g. 1,3-4  or  --extras  (blank = all)"
  [titles]="disc — e.g. disc:1 (blank = disc:0)"
  [movie-name]="args — e.g. --rename /mnt/videos/rip"
  [sync-sd]="card mountpoint (blank = auto-detect)"
  [tag]=""
  [sync-music]=""
  [transcode-video]=""
)

cmd="${pick_line%% *}"
hint="${arg_hint[$cmd]:-}"

if [ -n "$hint" ]; then
  # ponytail: plain read replaces gum input — one fewer dep. word-splitting on
  # $args is intentional (1,3-4 → one arg; --rename /p → two). SC2086 disabled.
  # ▸ in crystalBlue (#7E9CD8), hint in fujiGray (#727169); \033[0m resets.
  printf '\033[38;2;126;156;216m▸\033[0m \033[38;2;114;113;105m%s\033[0m ' "$hint"
  read -r args || exit 0          # Ctrl-D / EOF aborts cleanly
  # shellcheck disable=SC2086
  exec "$cmd" $args
else
  exec "$cmd"
fi
