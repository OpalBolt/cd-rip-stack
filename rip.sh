#!/usr/bin/env bash
# rip a CD with whipper or cyanrip, then log elapsed time.
# run inside the nix devshell (whipper/cyanrip/date/tee come from PATH).
# ponytail: manual arg parsing — no getopts for a couple of positional args.
set -euo pipefail
source "$(dirname "$0")/rip-common.sh"

out_dir=~/media/audio/music/rip

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

RIP_SCRIPT=rip.sh
RIP_SW="$engine offset=$offset"
# ponytail: album name isn't known at rip time (beets assigns it later, separately).
RIP_NAME=-
rip_log_start
rip_phase rip

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
    #
    # cyanrip exits 1 when a DiscID matches several MusicBrainz releases and
    # asks for -R <n> — that is not a rip failure. tee captures its output
    # (live progress still shows), and on that message we prompt, then re-run
    # with the picked release instead of bailing out.
    cap=$(mktemp)
    cyan_run() { ( cd "$out_dir" && cyanrip -s "$offset" "$@" 2>&1 ) | tee "$cap"; }
    if cyan_run "${@}"; then
      :
    elif grep -q "Multiple releases found" "$cap"; then
      printf '\nMultiple MusicBrainz releases — pick one (blank to abort): ' >&2
      read -r choice || choice=
      [ -n "$choice" ] && cyan_run -R "$choice" "${@}" || { rm -f "$cap"; exit 1; }
    else
      rm -f "$cap"; exit 1
    fi
    rm -f "$cap"
    ;;
esac

rip_phase_end
