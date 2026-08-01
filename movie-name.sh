#!/usr/bin/env bash
# movie-name — resolve a real movie name for a DVD/Blu-ray disc or a lossless
# MKV. makemkvcon names DVD rips after a useless label code (B1_t00.mkv); this
# returns "<Title>\t<Year>\t<Lang>" (Lang = ISO 639-1, or "-" if unknown) so
# rip/transcode/move name files, folders, and logs, and Task 3 picks audio by
# original_language.
#
# Two-tier, first real hit wins:
#   1. metadata name — makemkvcon's own title name. For a disc, scan `info` and
#      read CINFO:2 / TINFO:0,2 (like titles.sh); for an MKV path, read the
#      file's base — that base IS makemkvcon's output name (TINFO code 27), so
#      it carries the same metadata a scan would, already on disk. Used only if
#      it "looks real": Blu-ray titles pass, DVD label codes (B1) fail.
#   2. TMDB search — prompt for a query, show the top matches, pick one.
# On no $TMDB_API_KEY / no network / cancel / no match: fall back to the label
# and warn — naming must never block a rip.
# Reads $TMDB_API_KEY from the process environment; never opens .env.
# ponytail: one resolver, called by rip-video (and usable by transcode/move/audio).
set -euo pipefail

arg=${1:-disc:0}

usage() {
  cat <<EOF
usage: movie-name [disc:N | /path/to/Movie_tNN.mkv]
  prints "<Title>\t<Year>\t<Lang>" (Lang = ISO 639-1, or "-").
  Blu-ray metadata is used directly (no prompt); a DVD label code (B1) prompts a
  TMDB search. Reads \$TMDB_API_KEY from the env. Falls back to the label with a
  warning if TMDB is unavailable or cancelled — never blocks.
EOF
}

case "${1:-}" in -h|--help) usage; exit 0 ;; esac

warn() { echo "movie-name: $*" >&2; }
emit() { printf '%s\t%s\t%s\n' "$1" "$2" "${3:--}"; }   # title year lang (- if empty)

# looks_real: a makemkvcon name is a real title if it has a space, is longer
# than 3, isn't the DVD file-label pattern ^[A-Z][0-9]+ (B1, C2), and isn't a
# short all-caps generic label. Blu-ray titles ("Rogue One- A Star Wars Story")
# pass; DVD labels ("B1"; "DIE_HARD_4_0_SE_DISC_1" — no space) fail.
# ponytail: heuristic — real titles carry lowercase + spaces; label codes don't.
looks_real() {
  local n=$1
  [ ${#n} -gt 3 ] || return 1
  [[ $n =~ ^[A-Z][0-9]+$ ]] && return 1          # B1, C2 — the DVD file-label pattern
  [[ $n == *" "* ]] || return 1                  # needs at least one space
  [[ $n =~ ^[^a-z]*$ && ${#n} -le 16 ]] && return 1   # short all-caps = generic label
  return 0
}

# cache_hit <path>: a re-run over an already-named "<Title> (Year)_tNN.mkv" must
# not re-prompt. If the base matches "Title (19xx/20xx)", emit and return 0.
cache_hit() {
  local b
  b=$(basename "$1" .mkv); b=${b%_t*}
  if [[ $b =~ ^(.+)\ \((19[0-9]{2}|20[0-9]{2})\)$ ]]; then
    emit "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" -
    return 0
  fi
  return 1
}

# path_raw <path>: makemkvcon's output name = file base minus trailing "_tNN".
path_raw() { local b; b=$(basename "$1" .mkv); printf '%s' "${b%_t*}"; }

# disc_raw <disc>: scan makemkvcon info, prefer title 0's name (TINFO:0,2), fall
# back to the disc volume label (CINFO:2). MAKEMKV_INFO feeds a captured dump in
# the self-test — the same override titles.sh uses.
disc_raw() {
  local disc=$1 info
  if [ -n "${MAKEMKV_INFO:-}" ]; then info=$("$MAKEMKV_INFO" "$disc")
  else info=$(makemkvcon -r info --minlength="${RIP_MINLENGTH:-120}" "$disc"); fi
  # ponytail: mirror titles.sh's quote-strip (handles commas inside the title).
  printf '%s' "$info" | awk '
    /^CINFO:2,/  { v=$0; sub(/^[^"]*"/,"",v); sub(/"$/,"",v); disc=v }
    /^TINFO:0,2,/{ v=$0; sub(/^[^"]*"/,"",v); sub(/"$/,"",v); title=v }
    END { print (title != "") ? title : disc }
  '
}

# tmdb_resolve <fallback>: prompt -> search -> pick. Always emits one line (the
# pick, or the fallback on any failure/cancel) and returns 0 — never blocks.
tmdb_resolve() {
  local fallback=${1:-}

  if [ -z "${TMDB_API_KEY:-}" ]; then
    warn "no \$TMDB_API_KEY in env — using disc label '${fallback:--}'"
    emit "${fallback:--}" - -; return 0
  fi

  printf '\nTMDB search (blank to skip): ' >&2
  local query; read -r query || query=
  [ -n "$query" ] || { warn "no query — using disc label '${fallback:--}'"; emit "${fallback:--}" - -; return 0; }

  local q_enc url json
  q_enc=$(jq -nr --arg q "$query" '$q|@uri')
  url="https://api.themoviedb.org/3/search/movie?api_key=${TMDB_API_KEY}&query=${q_enc}"
  if ! json=$(curl -fsS --max-time 15 "$url"); then
    warn "TMDB request failed (no network?) — using disc label '${fallback:--}'"
    emit "${fallback:--}" - -; return 0
  fi

  # top 10 results: title \t year (release_date[0:4]) \t original_language
  local picks=()
  mapfile -t picks < <(printf '%s' "$json" | jq -r \
      '.results[0:10][] | [ .title, (.release_date[0:4] // "-"), (.original_language // "-") ] | @tsv')
  local n=${#picks[@]}
  [ "$n" -gt 0 ] || { warn "no TMDB matches for '$query' — using disc label '${fallback:--}'"; emit "${fallback:--}" - -; return 0; }

  local i t y l
  for i in "${!picks[@]}"; do
    IFS=$'\t' read -r t y l <<< "${picks[$i]}"
    printf '%d. %s  (%s)  [%s]\n' "$((i+1))" "$t" "$y" "$l" >&2
  done

  printf 'pick one (1-%s, blank to skip): ' "$n" >&2
  local pick; read -r pick || pick=
  if [[ $pick =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "$n" ]; then
    IFS=$'\t' read -r t y l <<< "${picks[$((pick-1))]}"
    emit "$t" "$y" "${l:--}"
  else
    warn "cancelled — using disc label '${fallback:--}'"; emit "${fallback:--}" - -
  fi
}

# --- main --------------------------------------------------------------------
if [[ $arg == disc:* ]]; then
  raw=$(disc_raw "$arg")
else
  if cache_hit "$arg"; then exit 0; fi
  raw=$(path_raw "$arg")
fi

if [ -n "$raw" ] && looks_real "$raw"; then
  emit "$raw" - -          # Blu-ray: real title, no year/lang in metadata
else
  tmdb_resolve "$raw"      # DVD junk label -> TMDB, or fallback
fi
