#!/usr/bin/env bash
# rip a DVD/Blu-ray to lossless MKV on the NAS (/mnt/videos/rip/). makemkvcon
# decrypts + remuxes with a PER-MOVIE track-selection profile so the film's
# original-language audio is picked at rip time (Task 3); the desktop transcode
# is then a dumb pass-through re-encode (video only). no transcode, no delete —
# run transcode-video on the desktop later. run inside the nix devshell.
#
# Task 3 audio flow: scan the disc once → detect audio languages → resolve the
# movie name + original_language (movie-name, replaying the scan so the drive is
# not read twice) → decide which audio to keep → inject it into a temp copy of
# custom.mmcp.xml → rip → rename staged MKVs with the already-known name.
# ponytail: manual arg parsing — one optional positional, no getopts.
set -euo pipefail
source "$(dirname "$0")/rip-common.sh"
shopt -s nullglob

# ── Task 3: audio selection. pure helpers (sourced by test_audio_select.sh) ──

# ISO 639-1 (TMDB original_language) → ISO 639-2/B (MakeMKV). unknown codes pass
# through unchanged (MakeMKV won't match them; the nolang/single nets catch all).
# start with the user's library; add rows as needed.
iso2b() {
  case "$1" in
    en) printf eng;; de) printf deu;; da) printf dan;; fr) printf fra;;
    es) printf spa;; it) printf ita;; sv) printf swe;; no) printf nor;;
    fi) printf fin;; nl) printf nld;; *) printf '%s' "$1";;
  esac
}

# distinct audio languages of title 0 (the main feature) from a `makemkvcon -r
# info` dump, space-separated, first-seen order. mirrors titles.sh's SINFO parse
# (attr 1 = stream type, attr 3 = language code).
audio_langs() {  # $1 = info dump file
  awk -F, '
  function strip(s){ sub(/^[^"]*"/,"",s); sub(/"$/,"",s); return s }
  /^SINFO:0,/ { st=$2+0; attr=$3+0; v=strip($0)
    if (attr==1) typ[st]=v
    if (attr==3) lng[st]=v
    if (st>smax) smax=st
  }
  END { for (st=0; st<=smax; st++)
          if (typ[st]=="Audio" && lng[st]!="" && !(lng[st] in seen)) { seen[lng[st]]=1; printf "%s%s",(o?" ":""),lng[st]; o=1 }
        print ""
      }' "$1"
}

# build the MakeMKV selection string. keep-lang = ISO 639-2/B to keep as the
# original-language audio; "" = keep-all fallback (TMDB unknown + user skipped).
# type qualifiers audio(...)/subtitle(...) drop a dub-language AUDIO while keeping
# same-language SUBTITLES; video survives via the nolang catch (docker-ripper's
# trick). subtitle clause is a permissive baseline — Task 4 narrows it to dan.
# ponytail: verify the audio(...)/subtitle(...) qualifier syntax empirically on
# the first real disc (MakeMKV apdefs.h is truth); this is one tunable string.
build_selection() {  # $1 = keep-lang (2/B) or ""
  local k=$1 aud sub
  if [ -n "$k" ]; then aud="audio(${k}|nolang|single)";   sub="subtitle(${k}|dan|nolang|single)"
  else               aud="audio(eng|dan|nolang|single)";  sub="subtitle(eng|dan|nolang|single)"; fi
  printf '%s' "-sel:all,+sel:${aud},-sel:audio(havemulti|havecore),+sel:${sub},+sel:(nolang|single),-sel:mvcvideo"
}

# decide which audio language to keep (ISO 639-2/B), or "" for keep-all fallback.
# tiers, first that fires wins:
#   1. TMDB original_language, if the disc actually carries it (drops the dub)
#   3. the only audio language on the disc (MakeMKV `single` covers one-track discs)
#   2. interactive prompt — list langs, user picks one (safety net)
#   4. keep all (TMDB unknown + user skipped/no-tty) — reversible, logged as "all"
decide_keep() {  # $1 = origlang (ISO 639-1 or "-"), $2 = disc audio langs (space-sep 2/B)
  local orig=$1 langs=$2 k pick w
  k=$(iso2b "$orig")
  if [ "$k" != "-" ] && [ -n "$k" ] && grep -qw -- "$k" <<<"$langs"; then printf '%s' "$k"; return 0; fi
  # shellcheck disable=SC2086  # word-split langs into positional params on purpose
  set -- $langs
  [ "$#" -eq 1 ] && { printf '%s' "$1"; return 0; }
  if [ -t 0 ]; then
    printf '\naudio languages on title 0: %s\n' "$*" >&2
    while true; do
      printf 'keep which audio? (Enter or q = keep all): ' >&2
      read -r pick || pick=''
      [[ $pick =~ ^(q|Q|)$ ]] && return 0          # keep all
      for w in "$@"; do [ "$w" = "$pick" ] && { printf '%s' "$pick"; return 0; }; done
      printf 'rip-video: %s not in [%s]\n' "$pick" "$*" >&2
    done
  fi
  return 0            # tier 4: keep all (empty stdout)
}

# the selection string baked into custom.mmcp.xml; rip-video sed-replaces it with
# build_selection's output at rip time. MUST stay byte-identical to the string in
# custom.mmcp.xml — it is both the substitution target and the manual-use default.
BASELINE='-sel:all,+sel:(eng|dan|nolang|single),-sel:(havemulti|havecore),-sel:mvcvideo'

# ── main (runs only when executed directly; skipped when sourced by the test) ──
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then

vid="${VIDEOS_MOUNT:-/mnt/videos}"
rip_dir="$vid/rip"
# 2 min drops logos/FBI warnings but keeps extras (user decision: keep all extras,
# including <10-min ones, rather than the task's 600s suggestion).
minlen="${RIP_MINLENGTH:-120}"

usage() {
  cat <<EOF
usage: rip-video [title]   (disc is always disc:0)
  title   which titles to rip: "all" (default), a number, a range (N-M),
          or a comma list (e.g. 0,5-7). list them first with: titles.
          rips lossless MKVs to $rip_dir with original-language audio; run
          transcode-video on the desktop to finish.
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac
title="${1:-all}"

# expand the title spec into individual title numbers. makemkvcon rips one title
# per call ("saves a single title"), so a list or range becomes a loop. the disc
# is fixed at disc:0, so a stray "disc:N" here would make makemkvcon dump its
# usage and exit 1 — validate before touching the drive.
titles_to_rip=()
if [ "$title" = all ]; then
  titles_to_rip=(all)
else
  IFS=, read -ra tokens <<< "$title"
  for tok in "${tokens[@]}"; do
    if [[ $tok =~ ^([0-9]+)-([0-9]+)$ ]]; then
      lo=${BASH_REMATCH[1]}; hi=${BASH_REMATCH[2]}
      if (( 10#$lo > 10#$hi )); then
        echo "rip-video: bad range '$tok' (start greater than end)" >&2; exit 1
      fi
      for (( n=10#$lo; n<=10#$hi; n++ )); do titles_to_rip+=("$n"); done
    elif [[ $tok =~ ^[0-9]+$ ]]; then
      titles_to_rip+=("$tok")
    else
      echo "rip-video: invalid title '$tok' — use 'all', a number, N-M, or a comma list" >&2
      usage; exit 1
    fi
  done
fi

# ponytail: shared mount helper (Task 2) — the same one sync-music uses for /mnt/music.
mount-nfs "$vid" "${NFS_VIDEOS:-}"
mkdir -p "$rip_dir"

RIP_SCRIPT=rip-video.sh
RIP_SW=makemkvcon
RIP_NAME=-
rip_log_start
# layer temp cleanup onto the EXIT trap rip_log_start set (it still logs the total).
TMP_DUMP=; TMP_REPLAY=; TMP_PROFILE=
rip_video_cleanup() {
  [ -n "$TMP_DUMP" ]    && rm -f "$TMP_DUMP"
  [ -n "$TMP_REPLAY" ]  && rm -f "$TMP_REPLAY"
  [ -n "$TMP_PROFILE" ] && rm -f "$TMP_PROFILE"
}
trap 'rip_video_cleanup; rip_log_finish' EXIT

# ── Task 3: resolve audio + name BEFORE rip (the profile is baked at rip time) ──
TMP_DUMP=$(mktemp)
if [ -n "${MAKEMKV_INFO:-}" ]; then ${MAKEMKV_INFO} disc:0 > "$TMP_DUMP"
else                                makemkvcon -r info --minlength="$minlen" disc:0 > "$TMP_DUMP"; fi
langs=$(audio_langs "$TMP_DUMP")            # space-separated distinct audio langs
detected=${langs// /,}                      # comma-separated for the log

# replay the captured scan into movie-name so the drive is NOT read a second time.
TMP_REPLAY=$(mktemp)
printf '#!/usr/bin/env bash\ncat "%s"\n' "$TMP_DUMP" > "$TMP_REPLAY"
chmod +x "$TMP_REPLAY"

TITLE=-; YEAR=-; origlang=-
if line=$(MAKEMKV_INFO="$TMP_REPLAY" movie-name disc:0); then
  IFS=$'\t' read -r TITLE YEAR origlang <<< "$line"
else
  echo "rip-video: name not resolved (no \$TMDB_API_KEY? skipped?) — ripping with fallback audio; MKVs keep labels" >&2
fi
display=$TITLE; [ "$YEAR" != - ] && display="$TITLE ($YEAR)"
display=${display//\//-}     # a '/' would split the path; transcode sanitizes the rest
RIP_NAME=$display

keep=$(decide_keep "$origlang" "$langs")     # "" = keep all (tier 4)
sel=$(build_selection "$keep")
rip_note audio "kept=${keep:-all} detected=${detected:-none}"

# inject the selection string into a temp copy of the profile. BASELINE is the
# literal substitution target (see custom.mmcp.xml); '#' delimiter because neither
# BASELINE nor sel contains '#', and as a BRE pattern BASELINE is fully literal.
TMP_PROFILE=$(mktemp --suffix=.mmcp.xml)
sed "s#${BASELINE}#${sel}#g" "$(dirname "$0")/custom.mmcp.xml" > "$TMP_PROFILE"

# decrypt + remux to lossless MKV with the per-movie profile. --minlength drops
# short junk (logos/FBI warnings) but keeps extras. must match titles.sh: makemkv
# renumbers titles by what survives the scan, so a different value moves them.
rip_phase rip
for t in "${titles_to_rip[@]}"; do
  makemkvcon mkv -r --profile="$TMP_PROFILE" --minlength="$minlen" disc:0 "$t" "$rip_dir/"
done

# rename staged MKVs to "<Title (Year)>_tNN.mkv" using the name resolved above
# (no re-prompt). if unresolved, leave labels — re-run: movie-name --rename $rip_dir.
if [ "$display" != "-" ]; then
  movie-name --rename-as "$display" "$rip_dir" \
    || echo "rip-video: rename failed — re-run: movie-name --rename $rip_dir" >&2
else
  echo "rip-video: name unresolved — MKVs keep labels; set \$TMDB_API_KEY and re-run: movie-name --rename $rip_dir" >&2
fi

rip_phase_end

fi   # end main guard
