#!/usr/bin/env bash
# rip-menu — guided entry surface for the ripping toolkit. run inside the nix
# devshell so gum + every command is on PATH. lists the commands grouped by
# workflow with a one-line description, then runs the pick.
set -euo pipefail

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

# stateless: computed on open, nothing stored. pending counts watch the same
# dirs the commands operate on.
mounted() { grep -q " $1 " /proc/mounts; }   # portable — no util-linux dep

audio_rip="$HOME/media/audio/music/rip"
vid="${VIDEOS_MOUNT:-/mnt/videos}"

pending=()
if [ -d "$audio_rip" ]; then
  n=$(find "$audio_rip" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)
  [ "$n" -gt 0 ] && pending+=("$n in ~/media/audio/music/rip — awaiting tag")
fi
# ponytail: mount guard BEFORE any access — a stale/unmounted NFS share would
# hang find; skip the video count entirely rather than risk a hang or show "0".
if mounted "$vid" && [ -d "$vid/rip" ]; then
  n=$(find "$vid/rip" -name '*.mkv' 2>/dev/null | wc -l)
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

pick=$(printf '%s\n' "${entries[@]}" | gum choose --header "$header")

# per-command arg hints: a non-empty hint earns a gum input prompt after pick;
# an empty hint runs the command immediately. the description after the em-dash
# is ignored — only the first token (the command) matters.
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

cmd="${pick%% *}"
hint="${arg_hint[$cmd]:-}"

if [ -n "$hint" ]; then
  # ponytail: intentional word-splitting on $args so "1,3-4" → one arg and
  # "--rename /path" → two. disable shellcheck SC2086 accordingly. globbing is
  # left on intentionally (a "*.mkv" arg is occasionally useful); set -f if it bites.
  # shellcheck disable=SC2086
  args=$(gum input --placeholder "$hint" --char-limit=0)
  exec "$cmd" $args
else
  exec "$cmd"
fi
