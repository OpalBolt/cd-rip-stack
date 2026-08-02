#!/usr/bin/env bash
# tag — beets import on the rip dir, sorting into the library.
# beets-config.yaml is installed next to this binary (see flake.nix); resolves the
# config path so `nix develop -c tag` works regardless of cwd (the old shellHook
# alias used a cwd-relative path).
set -euo pipefail
beet -c "$(dirname "$0")/beets-config.yaml" import ~/media/audio/music/rip/
