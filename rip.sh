#!/usr/bin/env bash
# rip a CD with whipper or cyanrip, then log elapsed time.
# run inside the nix devshell (whipper/cyanrip/date/tee come from PATH).
# ponytail: manual arg parsing — no getopts for a couple of positional args.
set -euo pipefail
source "$(dirname "$0")/rip-common.sh"

out_dir=~/media/audio/music/rip

# read ALBUM/ARTIST off the newest produced FLAC — both cyanrip and whipper
# write them on a MusicBrainz match. engine-agnostic: metaflac reads the tag
# the ripper already wrote, so no parsing of per-engine stdout.
# ponytail: - when -N skipped the lookup (no ALBUM tag to read).
rip_album_name() {
  local f album artist
  f=$(find "$out_dir" -name '*.flac' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
  [ -z "$f" ] && { echo -; return; }
  album=$(metaflac --show-tag=ALBUM  "$f" 2>/dev/null | cut -d= -f2-)
  artist=$(metaflac --show-tag=ARTIST "$f" 2>/dev/null | cut -d= -f2-)
  [ -z "$album" ] && { echo -; return; }
  [ -n "$artist" ] && echo "$artist - $album" || echo "$album"
}

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
# MusicBrainz lookup needs the network; cyanrip -N skips it (beets tags
# later), so only gate the path that actually looks up metadata.
case " $* " in *" -N "*) ;; *) require_online "https://musicbrainz.org/" "MusicBrainz" ;; esac

mkdir -p "$out_dir"

RIP_SCRIPT=rip.sh
RIP_SW="$engine offset=$offset"
# ponytail: name is read from the ripped FLAC's tags after the rip (below); - until then.
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

RIP_NAME=$(rip_album_name)
rip_phase_end
