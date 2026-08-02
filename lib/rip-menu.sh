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

# exec the command (first token); the description after the em-dash is ignored.
# commands that take args print their own usage when run bare — the menu is a
# launcher, not an argument-gatherer.
exec "${pick%% *}"
