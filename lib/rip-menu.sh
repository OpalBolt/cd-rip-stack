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

pick=$(printf '%s\n' "${entries[@]}" | gum choose --header "CD · Video · Tools — pick an action")

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
