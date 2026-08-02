#!/usr/bin/env bash
# rip-menu — guided entry surface for the ripping toolkit. run inside the nix
# devshell so gum + every command is on PATH. shows the toolkit commands via
# gum choose and runs the pick.
# ponytail: plain list for now — grouping, one-line descriptions, and live
# counts arrive in slices 3 and 4.
set -euo pipefail

commands=(
  rip
  rip-video
  transcode-video
  movie-name
  titles
  sync-music
  sync-sd
)

# `tag` is a shell alias, not a binary, so nix develop -c can't see it — left out.
choice=$(printf '%s\n' "${commands[@]}" | gum choose)

exec "$choice"
