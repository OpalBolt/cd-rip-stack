#!/usr/bin/env bash
# rip a CD with whipper or cyanrip, then log elapsed time.
# run inside the nix devshell (whipper/cyanrip/date/tee come from PATH).
# ponytail: manual arg parsing — no getopts for a couple of positional args.
set -euo pipefail

out_dir=~/media/audio/music/rip
log=~/media/audio/music/rip.log

usage() {
  cat <<EOF
usage: rip <offset> [whipper|cyanrip] [extra-flags...]
  offset       drive sample offset (see drive table in README)
  engine       cyanrip (default) | whipper — optional, may be omitted
  extra-flags  passed through to the ripper (e.g. cyanrip -N, -R x)
EOF
}

offset="${1:-}"

if [ -z "$offset" ]; then
  usage
  exit 1
fi
shift

# ponytail: engine is optional — if the next arg is a flag (or nothing),
# default to cyanrip so `rip 6 -R x` works without naming the engine.
case "${1:-}" in
  whipper | cyanrip)
    engine="$1"
    shift
    ;;
  -* | '')
    engine=cyanrip
    ;;
  *)
    echo "unknown engine: $1 (use whipper or cyanrip)" >&2
    usage
    exit 1
    ;;
esac

mkdir -p "$out_dir"

start=$(date +%s)
# log elapsed time on success OR failure (EXIT trap fires either way)
finish() {
  local rc=$?
  local elapsed=$(( $(date +%s) - start ))
  local mm=$(( elapsed / 60 ))
  local ss=$(( elapsed % 60 ))
  local status=ok
  [ "$rc" -ne 0 ] && status="FAIL($rc)"
  printf '[%s] %-8s offset=%-5s %dm%02d  %s\n' \
    "$(date '+%F %T')" "$engine" "$offset" "$mm" "$ss" "$status" | tee -a "$log"
}
trap finish EXIT

case "$engine" in
  whipper)
    # whipper's MusicBrainz lookup is unconditional (no skip flag); -U keeps
    # it from aborting when the lookup finds nothing, since beets re-tags later.
    whipper cd rip -O "$out_dir" --offset="$offset" -U "${@}"
    ;;
  cyanrip)
    # cyanrip has no output-dir flag; it writes under the current directory.
    # -s is the (required) drive offset; extra flags (e.g. -N to skip
    # MusicBrainz, -R x, since beets tags later) pass through from the command line.
    ( cd "$out_dir" && cyanrip -s "$offset" "${@}" )
    ;;
esac
