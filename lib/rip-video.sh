#!/usr/bin/env bash
# rip a DVD/Blu-ray to lossless MKV on the NAS (/mnt/videos/rip/). makemkvcon
# decrypts + remuxes with a PER-MOVIE track-selection profile so the film's
# original-language audio is picked at rip time (Task 3); the desktop transcode
# is then a dumb pass-through re-encode (video only). no transcode, no delete —
# run transcode-video on the desktop later. run inside the nix devshell.
#
# Task 3 audio flow — the title and audio come from a TMDB lookup the user drives,
# NOT from scanning the disc (the scan is slower than typing the title):
#   refuse if offline -> movie-name --tmdb (user types the title, picks a result)
#   -> original_language from TMDB -> build a selection string -> sed it into a
#   temp custom.mmcp.xml -> rip -> rename staged MKVs with the resolved name.
# ponytail: manual arg parsing — one optional positional, no getopts.
set -euo pipefail
source "$(dirname "$0")/rip-common.sh"
shopt -s nullglob

# ── Task 3: audio selection ──

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

# build the MakeMKV selection string. keep-lang = ISO 639-2/B to keep as the
# original-language audio; "" = keep-all fallback (TMDB had no original_language).
# MakeMKV's selection string is type-AGNOSTIC — audio(<lang>)/subtitle(<lang>)
# qualifiers are INVALID tokens (confirmed on a real disc), so one language list
# applies to every track type. we keep the UNION of what each needs: the film's
# original-language audio (Task 3) + Danish/English subtitles (Task 4) + the
# nolang/single safety nets. cost: a few harmless extras (dan/eng audio dubs,
# origlang subs); the player picks defaults. strict dub-dropping needs a post-rip
# re-mux, not this string. video survives via the nolang catch (no language tag).
build_selection() {  # $1 = keep-lang (2/B) or ""
  local k=$1 langs="dan|eng|nolang|single"
  # prepend the film's original language unless it's already dan/eng (dup guard)
  case "$k" in ''|dan|eng) ;; *) langs="${k}|${langs}" ;; esac
  printf '%s' "-sel:all,+sel:(${langs}),-sel:(havemulti|havecore),-sel:mvcvideo"
}

# the selection string baked into custom.mmcp.xml; rip-video sed-replaces it with
# build_selection's output at rip time. MUST stay byte-identical to the string in
# custom.mmcp.xml — it is both the substitution target and the manual-use default.
BASELINE='-sel:all,+sel:(eng|dan|nolang|single),-sel:(havemulti|havecore),-sel:mvcvideo'

# ── burned-media gate (Task: no bootleg burns from the thrift store) ──
# makemkvcon prefixes the DRV drive name with the media type: "DVD+R-DL hp
# CDDVDW TS-L633R …" for a burn vs "DVD-ROM hp CDDVDW …" for a pressed disc.
drive_media() {  # print disc:0's media-type token ("" = no disc / not reported)
  local drv
  drv=$({ if [ -n "${MAKEMKV_INFO:-}" ]; then "$MAKEMKV_INFO" disc:0
          else makemkvcon -r info disc:0; fi } 2>/dev/null \
        | sed -n 's/^DRV:0,2,[0-9]*,[0-9]*,"\([^"]*\)".*/\1/p')
  printf '%s' "${drv%% *}"
}
is_recordable() {  # $1 = media token; returns 0 when it is a burnable type
  case "$1" in
    # pressed or unknown — the gate fails OPEN: an unrecognized token never
    # blocks a rip (only known-recordable families are refused).
    DVD-ROM|BD-ROM|CD-ROM|HD-DVD|"CD"|"DVD"|"BD"|"") return 1 ;;
    # recordable families. beware globs: "DVD-R*"/"BD-R*"/"CD-R*" all swallow
    # *-ROM too, so the colliding bases are exact + explicit trailing variants.
    DVD+R*|DVD+RW*|DVD-RW*|DVD-RAM*|BD-RE*|CD-RW*) return 0 ;;
    DVD-R|CD-R|BD-R|"DVD-R "*|"DVD-R-"*|"BD-R "*) return 0 ;;
    *) return 1 ;;
  esac
}

# require_online is shared (rip-common): it probes a URL and bails if offline.
# rip-video checks TMDB_API_KEY inline first (clear message), then probes the TMDB
# API — the title + audio language both come from TMDB, so an offline rip is wrong.

# ── TV duration matcher (decisions before the disc leaves the drive) ──
# disc titles carry no episode numbers; duration matching PROPOSES, the confirm
# table decides. data model: TT_ORDER = scanned title numbers (ascending),
# tt_secs_of/tt_size_of keyed by title number; EP_ORDER = fetched episode
# numbers (ascending), ep_name_of/ep_rt_of keyed by episode number (minutes,
# 0 = TMDB had none). tv_lo/tv_hi hold the assignment per title (equal = single,
# lo=0 = extra).

# ponytail: tuning knobs — TV_SINGLE_EPS is how far (seconds) a title may sit
# from a single episode's runtime and still claim it. 600 ≈ PAL speedup + recap
# slack; a featurette past it lists as extra (fixable at the table with N=<ep>).
# TV_DOUBLE_EPS is how close a title must sit to a pair's combined runtime for
# the double to compete with the best single. 300s ≈ 5 min of recap/cold-open
# slack; tighten if pairs steal singles, loosen if doubles split into two wrong
# episodes.
TV_SINGLE_EPS=600
TV_DOUBLE_EPS=300

declare -A tt_secs_of tt_size_of ep_name_of ep_rt_of M_avail STAGED_EP tv_lo tv_hi
TT_ORDER=; EP_ORDER=

# tv_scan: fill TT_ORDER/tt_secs_of/tt_size_of from a makemkvcon info scan.
# same source rule as titles.sh (MAKEMKV_INFO replays a captured dump) and the
# same --minlength as the rip loop: makemkv renumbers titles by what survives
# the scan, so the table must see exactly what the rip will.
tv_scan() {
  TT_ORDER=; tt_secs_of=(); tt_size_of=()
  local out t s z
  if [ -n "${MAKEMKV_INFO:-}" ]; then out=$("$MAKEMKV_INFO" disc:0)
  else out=$(makemkvcon -r info --minlength="$minlen" disc:0); fi || return 1
  while IFS=$'\t' read -r t s z; do
    [ -n "$t" ] || continue
    tt_secs_of[$t]=$s; tt_size_of[$t]=$z; TT_ORDER="$TT_ORDER $t"
  done < <(printf '%s' "$out" | awk -F'"' '
    /^TINFO:[0-9]+,(9|10),"/ {
      n=split($1,a,","); sub(/^TINFO:/,"",a[1]); t=a[1]+0; c=a[2]+0
      if (c==9)  { split($2,h,":"); dur[t]=(h[3]!="")?h[1]*3600+h[2]*60+h[3]:h[1]*60+h[2] }
      if (c==10) sz[t]=$2
    }
    END { for (t in dur) if (t in sz) printf "%s\t%s\t%s\n", t, dur[t], sz[t] }')
  TT_ORDER=$(printf '%s\n' $TT_ORDER | sort -n | tr '\n' ' ')
  [ -n "${TT_ORDER// /}" ]
}

# tv_fetch_season <id> <season>: fill EP_ORDER/ep_name_of/ep_rt_of from
# GET /3/tv/<id>/season/<n>. hard error on curl failure (offline was gated
# earlier, but this is a different endpoint).
tv_fetch_season() {
  EP_ORDER=; ep_name_of=(); ep_rt_of=()
  local json n nm rt
  json=$(curl -fsS --max-time 15 "https://api.themoviedb.org/3/tv/$1/season/$2?api_key=${TMDB_API_KEY}") || {
    echo "rip-video: TMDB season fetch failed (tv/$1/season/$2) — check network/key." >&2; return 1; }
  while IFS=$'\t' read -r n nm rt; do
    [ -n "$n" ] || continue
    ep_name_of[$n]=$nm; ep_rt_of[$n]=$rt; EP_ORDER="$EP_ORDER $n"
  done < <(printf '%s' "$json" | jq -r '.episodes[] | [(.episode_number|tostring), (.name // ""), ((.runtime // 0)|tostring)] | @tsv')
  EP_ORDER=$(printf '%s\n' $EP_ORDER | sort -n | tr '\n' ' ')
}

# tv_staged_eps <dir> <display> <season>: print the episode numbers already
# staged for this show and season (the staging dir IS the state — settled).
# "S02E05" -> 5; a double "S01E05-E06" -> 5 and 6.
tv_staged_eps() {
  local s2 f b lo hi
  s2=$(printf '%02d' $((10#$3)))
  for f in "$1/${2}_S${s2}E"*.mkv; do
    b=$(basename "$f" .mkv)
    [[ $b =~ _S[0-9]{2}E([0-9]+)(-E([0-9]+))?$ ]] || continue
    lo=$((10#${BASH_REMATCH[1]})); hi=${BASH_REMATCH[3]:-}
    printf '%s\n' "$lo"; [ -n "$hi" ] && printf '%s\n' "$((10#$hi))"
  done
}

# tv_build_avail: the candidate set — episodes TMDB gave a runtime for, minus
# the ones already staged (the staging dir is the state).
tv_build_avail() {
  M_avail=()
  local e
  for e in $EP_ORDER; do
    [ "${ep_rt_of[$e]:-0}" -gt 0 ] && [ -z "${STAGED_EP[$e]:-}" ] && M_avail[$e]=1
  done
}

# tv_match: greedy duration matching. titles longest-first each claim the
# unused episode (M_avail) with the nearest runtime (a single only competes
# within TV_SINGLE_EPS); a consecutive pair competes while within TV_DOUBLE_EPS,
# and the nearer of the two wins (tie -> single). a pair stamps a double. a
# title with no claim — no episode near enough, or none left — becomes an extra.
tv_match() {
  tv_lo=(); tv_hi=()
  local order t e rt rt2 d bs bd bl bh pd
  order=$(for t in $TT_ORDER; do printf '%s\t%s\n' "${tt_secs_of[$t]}" "$t"; done | sort -k1,1rn -k2,2n | cut -f2)
  for t in $order; do
    bs=; bd=                                          # best single episode
    for e in $EP_ORDER; do
      [ -n "${M_avail[$e]:-}" ] || continue
      rt=$(( ${ep_rt_of[$e]} * 60 ))
      d=$(( ${tt_secs_of[$t]} - rt )); [ "$d" -lt 0 ] && d=$((-d))
      if [ "$d" -le "$TV_SINGLE_EPS" ] && { [ -z "$bs" ] || [ "$d" -lt "$bd" ]; }; then bs=$e; bd=$d; fi   # strict < keeps the lower ep on a tie
    done
    bl=; bh=; pd=                                      # best consecutive pair
    for e in $EP_ORDER; do
      [ -n "${M_avail[$e]:-}" ] && [ -n "${M_avail[$((e+1))]:-}" ] || continue
      rt=$(( (${ep_rt_of[$e]} + ${ep_rt_of[$((e+1))]}) * 60 ))
      d=$(( ${tt_secs_of[$t]} - rt )); [ "$d" -lt 0 ] && d=$((-d))
      if [ "$d" -le "$TV_DOUBLE_EPS" ] && { [ -z "$bl" ] || [ "$d" -lt "$pd" ]; }; then bl=$e; bh=$((e+1)); pd=$d; fi
    done
    if [ -n "$bl" ] && { [ -z "$bs" ] || [ "$pd" -lt "$bd" ]; }; then
      tv_lo[$t]=$bl; tv_hi[$t]=$bh; unset "M_avail[$bl]" "M_avail[$bh]"
    elif [ -n "$bs" ]; then
      tv_lo[$t]=$bs; tv_hi[$t]=$bs; unset "M_avail[$bs]"
    else
      tv_lo[$t]=0; tv_hi[$t]=0                         # no claim -> extra
    fi
  done
}

# tv_map_str: serialize the assignment as the episode map ("0=2,1=1-2,2=e").
tv_map_str() {
  local m= t lo hi
  for t in $TT_ORDER; do
    lo=${tv_lo[$t]:-0}; hi=${tv_hi[$t]:-0}
    if   [ "$lo" = 0 ];    then m+="${m:+,}${t}=e"
    elif [ "$lo" = "$hi" ]; then m+="${m:+,}${t}=${lo}"
    else                          m+="${m:+,}${t}=${lo}-${hi}"; fi
  done
  printf '%s' "$m"
}

# hsize: bytes -> "4.0 GiB" for the table column.
hsize() { awk -v b="$1" 'BEGIN{split("KiB MiB GiB TiB",u," ");s="B";for(i=1;b>=1024&&i<=4;i++){b/=1024;s=u[i]}printf "%.1f %s",b,s}'; }

# tv_print_table: the confirm table (title order for readability; the matcher
# works longest-first, the eye reads title numbers).
tv_print_table() {
  local s2 t dur sz code name
  s2=$(printf '%02d' $((10#$season)))
  printf '\n%s — season %s\n' "$display" "$s2"
  for t in $TT_ORDER; do
    dur=$(printf '%d:%02d:%02d' $(( ${tt_secs_of[$t]} / 3600 )) $(( ${tt_secs_of[$t]} % 3600 / 60 )) $(( ${tt_secs_of[$t]} % 60 )))
    sz=$(hsize "${tt_size_of[$t]}")
    if [ "${tv_lo[$t]:-0}" = 0 ]; then
      printf '  title %-3s %-9s %9s  → extra\n' "$t" "$dur" "$sz"
    else
      code=$(printf 'S%02dE%02d' $((10#$season)) "${tv_lo[$t]}")
      [ "${tv_lo[$t]}" != "${tv_hi[$t]}" ] && code+=$(printf -- '-E%02d' "${tv_hi[$t]}")
      name=${ep_name_of[${tv_lo[$t]}]:-}
      [ -n "$name" ] && name="  ($name)"
      printf '  title %-3s %-9s %9s  → %s%s\n' "$t" "$dur" "$sz" "$code" "$name"
    fi
  done
}

# apply_fixes "3=7" / "1=1-2,2=e": grammar via movie-name --parse-ep-map (the
# ONE parser), semantics here. rows apply left-to-right; a re-pointed row's
# old episodes are already released by the time later rows validate (the row's
# own claim is excluded from the clash check). a rejected row changes nothing
# for that title and the table re-prints.
apply_fixes() {
  local tsv line t kind lo hi ok=1 e maxep t2 l2 h2 bad
  tsv=$(movie-name --parse-ep-map "$1") || {
    echo "rip-video: bad fix row '$1' (want N=ep, N=ep-ep, or N=e, comma-separable)" >&2; return 1; }
  maxep=0
  if [ "$matchmode" = match ]; then for e in $EP_ORDER; do [ "$e" -gt "$maxep" ] && maxep=$e; done; fi
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    IFS=$'\t' read -r t kind lo hi <<< "$line"
    if [ -z "${tt_secs_of[$t]:-}" ]; then
      echo "rip-video: no title $t on this disc" >&2; ok=0; continue
    fi
    if [ "$kind" != extra ]; then
      if [ "$matchmode" = match ]; then
        [ "$hi" -le "$maxep" ] || { echo "rip-video: episode $hi is past the season's last episode ($maxep)" >&2; ok=0; continue; }
      fi
      bad=
      for (( e=lo; e<=hi; e++ )); do
        [ -n "${STAGED_EP[$e]:-}" ] && { echo "rip-video: episode $e is already staged" >&2; bad=1; }
        for t2 in $TT_ORDER; do
          [ "$t2" = "$t" ] && continue
          l2=${tv_lo[$t2]:-0}; h2=${tv_hi[$t2]:-0}
          [ "$l2" = 0 ] && continue
          if (( e >= l2 && e <= h2 )); then echo "rip-video: episode $e is already claimed by title $t2" >&2; bad=1; fi
        done
      done
      [ -z "$bad" ] || { ok=0; continue; }
    fi
    tv_lo[$t]=$lo; tv_hi[$t]=$hi                        # extra rows carry lo=hi=0
  done <<< "$tsv"
  [ "$ok" = 1 ]
}

# rip-video --self-test: assert-based matcher check (fixtures in, assignment
# out; no drive, no network). a developer hook — deliberately not in usage().
if [ "${1:-}" = "--self-test" ]; then
  TV_FAIL=0
  tva() { if [ "$1" = "$2" ]; then echo "  ok: $3"; else echo "  FAIL: $3 (want '$1' got '$2')"; TV_FAIL=1; fi; }
  # greedy order: the longest title claims first, so t1 (21:30) takes the
  # nearer E21 and t0 (21:00) falls to E22 — title-number order would swap them.
  tt_secs_of=([0]=1260 [1]=1290); tt_size_of=([0]=1 [1]=1); TT_ORDER="0 1"
  ep_rt_of=([1]=21 [2]=22); EP_ORDER="1 2"; M_avail=([1]=1 [2]=1)
  tv_match; tva "0=2,1=1" "$(tv_map_str)" "greedy: longest title claims the nearest episode"
  # nearest single: 21:00 sits nearer the 20-min episode than the 25-min one.
  tt_secs_of=([0]=1260); TT_ORDER="0"; ep_rt_of=([1]=20 [2]=25); EP_ORDER="1 2"; M_avail=([1]=1 [2]=1)
  tv_match; tva "0=1" "$(tv_map_str)" "nearest single wins"
  # double rule: 44:00 against {E1 22min, E2 23min} -> the pair (45min, 60s
  # off) beats any single; a 21:30 title on the same pool takes a single.
  tt_secs_of=([0]=2640); TT_ORDER="0"; ep_rt_of=([1]=22 [2]=23); EP_ORDER="1 2"; M_avail=([1]=1 [2]=1)
  tv_match; tva "0=1-2" "$(tv_map_str)" "double: a near pair beats the best single"
  tt_secs_of=([0]=1290); TT_ORDER="0"; M_avail=([1]=1 [2]=1)
  tv_match; tva "0=1" "$(tv_map_str)" "double: a 21:30 title takes a single, not a pair"
  # leftover -> extra: three titles, one episode.
  tt_secs_of=([0]=1260 [1]=1300 [2]=1400); TT_ORDER="0 1 2"; ep_rt_of=([1]=21); EP_ORDER="1"; M_avail=([1]=1)
  tv_match; tva "0=e,1=e,2=1" "$(tv_map_str)" "leftover titles become extras"
  # featurette -> extra: a 10:00 title sits 720s from the 22/25-min episodes,
  # past TV_SINGLE_EPS, so it proposes extra; against a 12-min episode (d=120)
  # it claims. (a 21:30 vs 22-min title — d=90 — still claims, per the asserts above.)
  tt_secs_of=([0]=600); TT_ORDER="0"; ep_rt_of=([1]=22 [2]=25); EP_ORDER="1 2"; M_avail=([1]=1 [2]=1)
  tv_match; tva "0=e" "$(tv_map_str)" "single: a featurette too far from every runtime -> extra"
  ep_rt_of=([1]=12); EP_ORDER="1"; M_avail=([1]=1)
  tv_match; tva "0=1" "$(tv_map_str)" "single: within TV_SINGLE_EPS it still claims"
  # staged exclusion: the staging dir is the state — E01 (and both halves of a
  # staged double) leave the candidate set, so a second disc proposes onward.
  std=$(mktemp -d)
  printf x > "$std/Show (2000)_S01E01.mkv"; printf x > "$std/Show (2000)_S01E05-E06.mkv"
  tva "$(printf '1\n5\n6')" "$(tv_staged_eps "$std" "Show (2000)" 1 | sort -n)" "staged: singles and doubles both parsed"
  tt_secs_of=([0]=1260); TT_ORDER="0"; ep_rt_of=([1]=21 [2]=21); EP_ORDER="1 2"
  STAGED_EP=([1]=1); tv_build_avail
  tv_match; tva "0=2" "$(tv_map_str)" "staged episode no longer proposed"
  rm -rf "$std"
  [ "$TV_FAIL" = 0 ] && { echo OK; exit 0; } || exit 1
fi

# ── main ──

vid="${VIDEOS_MOUNT:-/mnt/videos}"
rip_dir="$vid/rip"
# 2 min drops logos/FBI warnings but keeps extras (user decision: keep all extras,
# including <10-min ones, rather than the task's 600s suggestion).
minlen="${RIP_MINLENGTH:-120}"

usage() {
  cat <<EOF
usage: rip-video [--extras] [--part N] [title]   (disc is always disc:0)
  --extras  rip this disc's titles as extras (staged as <Name>_eNN.mkv;
            transcode files them under extras/, never as the main feature).
            use for a set's bonus disc — a movie's or a show's (a TV pick
            stages under rip/TV/ and asks no season).
  --part N  this disc holds part N of a film split across multiple discs.
            stages titles as <Movie (Year)>_tNN.pN.mkv; transcode merges the
            main feature's parts in order (p1, p2, ...). pick the same movie
            title at the prompt for every disc. only the main feature merges —
            extras stay separate, filed under extras/.
  --tmdbid ID
          use this TMDB movie or TV id directly instead of the title-search
          prompt — for discs whose title the search can't find. ID is a bare
          movie id, "tv:N" for a TV show, or a pasted themoviedb.org/movie/N
          or /tv/N URL.
  --verified  override the burned-media gate: the rip REFUSES recordable media
            (DVD+R/±RW/BD-R/… — thrift-store discs are often bootleg burns).
            pass this only after physically verifying the disc is an original;
            genuinely-original recordable releases exist (e.g. Warner Archive
            DVD-R), so the escape hatch is manual, never automatic.
  title   which disc titles to rip: "all" (default), a number, a range (N-M),
          or a comma list (e.g. 0,5-7). list them first with: titles.
          asks for the movie title (TMDB), rips lossless MKVs with the original-
          language audio to $rip_dir; run transcode-video on the desktop to finish.

TV: pick a TV show at the TMDB prompt (results show [lang/tv]) and the flow
switches — it asks for a season (Enter=1, 0=specials), scans the disc, and
proposes a title→episode table matched by duration against the season's TMDB
runtimes (episodes already staged are skipped, so a second disc proposes the
next episodes; a double episode stays one file as S01E01-E02; a title with no
episode left lists as extra). At the ok? prompt Enter rips as proposed, rows
like 3=7 / 1=1-2 / 2=e re-point one title (the table re-prints), q aborts.
With no TMDB runtimes it falls back to asking the first episode number and
numbering titles in order. Episodes stage under rip/TV/ as
<Show (Year)>_SNNENN.mkv. --part is refused: TV episodes never merge.
--extras rips a show's bonus disc instead: no season, no table, staged _eNN
under rip/TV/ (an aired special is not an extra — it is a season 0 episode).
EOF
}

extras=0
part=
verified=0
tmdbid=
# ponytail: manual arg parsing — optional flags then one positional.
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --extras) extras=1; shift ;;
    --verified) verified=1; shift ;;
    --tmdbid)
      [ -n "${2:-}" ] || { echo "rip-video: --tmdbid needs a TMDB id, tv:N, or a themoviedb.org URL" >&2; exit 1; }
      tmdbid=$2; shift 2 ;;
    --part)
      [[ ${2:-} =~ ^[0-9]+$ ]] || { echo "rip-video: --part needs a number, got '${2:-}'" >&2; exit 1; }
      part=$2; shift 2 ;;
    *) break ;;
  esac
done
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

[ -n "${TMDB_API_KEY:-}" ] || { echo "rip-video: no \$TMDB_API_KEY in env — set it and re-run." >&2; exit 1; }
require_online "https://api.themoviedb.org/3/configuration?api_key=${TMDB_API_KEY}" "TMDB"

# ponytail: shared mount helper (Task 2) — the same one sync-music uses for /mnt/music.
mount-nfs "$vid" "${NFS_VIDEOS:-}"
mkdir -p "$rip_dir"

RIP_SCRIPT=rip-video.sh
RIP_SW=makemkvcon
RIP_NAME=-
rip_log_start
# layer temp cleanup onto the EXIT trap rip_log_start set (it still logs the total).
TMP_PROFILE=
rip_video_cleanup() { [ -n "$TMP_PROFILE" ] && rm -f "$TMP_PROFILE"; }
trap 'rip_video_cleanup; rip_log_finish' EXIT

# ── burned-media gate ──
media=$(drive_media)
[ -n "$media" ] || { echo "rip-video: no disc detected in the drive — insert one and re-run." >&2; exit 1; }
if is_recordable "$media"; then
  if [ "$verified" = 1 ]; then
    rip_note media "recordable media ($media) — override by --verified"
  else
    {
      echo "rip-video: RECORDABLE media in the drive ($media) — this looks like a burned copy, not a"
      echo "  pressed original (thrift-store discs are often bootlegs). check the disc by eye: burns"
      echo "  have purple/green/blue dye and often a stick-on or inkjet label; originals are silver"
      echo "  with printed artwork. genuinely-original recordable releases exist (e.g. Warner Archive"
      echo "  DVD-R) — if you verify it is one, re-run with --verified."
    } >&2
    exit 1
  fi
else
  rip_note media "media=$media"
fi

# purge junk-labeled leftovers (B1_t00.mkv …) from a failed/interrupted earlier
# session: makemkvcon always stages under an ^[A-Z][0-9]+ label, and the post-rip
# rename would adopt any leftover into THIS movie (that is how Watchmen disc
# content ended up filed as "Triple Cross (1966)_t1.mkv"). a fresh session starts
# from an empty staging dir — both rip/ and its rip/TV/ subtree (TV and movie
# junk purge by the same rule).
for f in "$rip_dir"/*.mkv "$rip_dir"/TV/*.mkv; do
  b=$(basename "$f" .mkv)
  [[ $b =~ ^[A-Z][0-9]+_t[0-9]+$ ]] || continue
  echo "rip-video: removing stale staged file from an earlier session: $b" >&2
  rm -f "$f"
done

# ── Task 3: ask for the title, TMDB-resolve it (no disc scan before rip) ──
# --tmdbid skips the search prompt entirely (exact /movie/<id> fetch). the TSV's
# 4th field is the pick's type: a tv pick branches into the TV flow below.
mn=(--tmdb); [ -n "$tmdbid" ] && mn=(--tmdbid "$tmdbid")
if line=$(movie-name "${mn[@]}"); then
  IFS=$'\t' read -r TITLE YEAR origlang picktype pick_id <<< "$line"
else
  echo "rip-video: title not resolved — aborting (re-run to retry)." >&2
  exit 1
fi
display=$TITLE; [ "$YEAR" != - ] && display="$TITLE ($YEAR)"
display=${display//\//-}     # a '/' would split the path; transcode sanitizes the rest
RIP_NAME=$display

# ── TV branch: the type is discovered here, not known up front (settled: one ──
# rip-video, prompts not flags). episodes stage under rip/TV/ as SNNENN files;
# the subdirectory is what tells transcode a file is TV. duration matching
# proposes title→episode, the confirm table decides, and the sequential prompt
# survives only as the no-runtimes fallback.
# ponytail: bash stays for rip orchestration — if this script passes ~500 lines
# or needs chapter splitting, move rip orchestration (not transcode) to Python.
season=; epmap=; stage_dir=$rip_dir
# a TV extras disc (--extras): bonus features, not episodes — no season, no
# scan, no table. rip straight into the TV staging dir and roll _eNN past
# existing (the movie-extras semantics, a different library subtree). an
# aired special is NOT an extra — it is a season 0 episode through the branch
# below.
if [ "$picktype" = tv ]; then
  [ -z "$part" ] || { echo "rip-video: --part is for a film split across discs — TV episodes never merge; rip them as separate episodes instead." >&2; exit 1; }
fi
# a TV extras disc (--extras): bonus features, not episodes — no season, no
# scan, no table. rip straight into the TV staging dir and roll _eNN past
# existing (the movie-extras semantics, a different library subtree). an
# aired special is NOT an extra — it is a season 0 episode through the branch
# below.
if [ "$picktype" = tv ] && [ "$extras" = 1 ]; then
  stage_dir="$rip_dir/TV"
  mkdir -p "$stage_dir"
  rip_note type "type=tv extras"
elif [ "$picktype" = tv ]; then
  [ "$pick_id" != - ] && [ -n "$pick_id" ] || { echo "rip-video: the TMDB pick carries no id — cannot fetch episodes. re-pick or use --tmdbid tv:N." >&2; exit 1; }
  printf 'season [1] (0 = specials): '
  read -r season || season=1
  season=${season:-1}
  [[ $season =~ ^[0-9]{1,2}$ ]] || { echo "rip-video: season must be a number 0-99, got '$season'" >&2; exit 1; }
  stage_dir="$rip_dir/TV"
  mkdir -p "$stage_dir"
  rip_note type "type=tv season=$season"

  # scan the disc — decisions happen before it leaves the drive (settled).
  tv_scan || { echo "rip-video: disc scan failed — re-run (drive/media issue? see the output above)." >&2; exit 1; }
  if [ "${titles_to_rip[0]}" != all ]; then
    # narrow the table to the titles the user asked to rip — makemkvcon rips
    # exactly those, so the map must not promise the rest.
    keep=; for t in $TT_ORDER; do
      case " ${titles_to_rip[*]} " in *" $t "*) keep="$keep $t" ;; esac
    done
    TT_ORDER=$keep
    [ -n "${TT_ORDER// /}" ] || { echo "rip-video: none of the requested titles survived the scan (minlength=$minlen)." >&2; exit 1; }
  fi

  tv_fetch_season "$pick_id" "$season" || exit 1
  STAGED_EP=()
  while read -r e; do [ -n "$e" ] && STAGED_EP[$e]=1; done < <(tv_staged_eps "$stage_dir" "$display" "$season")

  matchmode=match
  nrt=0; for e in $EP_ORDER; do [ "${ep_rt_of[$e]:-0}" -gt 0 ] && nrt=$((nrt+1)); done
  if [ "$nrt" -eq 0 ]; then
    # no runtimes at all (empty season or TMDB gaps) — sequential fallback.
    matchmode=sequential
    echo "rip-video: no TMDB runtimes for this season — numbering titles in order instead." >&2
    printf 'first episode number on this disc [1]: '
    read -r firstep || firstep=1
    firstep=${firstep:-1}
    { [[ $firstep =~ ^[0-9]+$ ]] && (( 10#$firstep >= 1 )); } || { echo "rip-video: episode must be a number >= 1, got '$firstep'" >&2; exit 1; }
    ep=$((10#$firstep)); tv_lo=(); tv_hi=()
    for t in $TT_ORDER; do tv_lo[$t]=$ep; tv_hi[$t]=$ep; ep=$((ep+1)); done
  else
    tv_build_avail
    tv_match
  fi

  # the confirm table is the only correction UI (settled): Enter rips as
  # proposed; fix rows re-point one title at a time; q aborts pre-rip.
  while true; do
    tv_print_table
    printf 'ok? (Enter=rip, fixes like 3=7 / 1=1-2 / 2=e, q=quit): '
    read -r ans || ans=q
    case "$ans" in
      '') break ;;
      q|Q) echo "rip-video: quit — nothing ripped." >&2; exit 1 ;;
      *)  apply_fixes "$ans" || true ;;
    esac
  done
  epmap=$(tv_map_str)
else
  rip_note type "type=movie"
fi

if [ "$origlang" != "-" ] && [ -n "$origlang" ]; then keep=$(iso2b "$origlang")
else                                                      keep=""; fi
sel=$(build_selection "$keep")
rip_note audio "kept=${keep:-all} origlang=${origlang:-?}"

# inject the selection string into a temp copy of the profile. BASELINE is the
# literal substitution target (see custom.mmcp.xml); '#' delimiter because neither
# BASELINE nor sel contains '#', and as a BRE pattern BASELINE is fully literal.
TMP_PROFILE=$(mktemp --suffix=.mmcp.xml)
sed "s#${BASELINE}#${sel}#g" "$(dirname "$0")/custom.mmcp.xml" > "$TMP_PROFILE"

# decrypt + remux to lossless MKV with the per-movie profile. --minlength drops
# short junk (logos/FBI warnings) but keeps extras. must match titles.sh: makemkv
# renumbers titles by what survives the scan, so a different value moves them.
# a failed title must not kill the script mid-loop (set -e) — a flaky disc would
# then skip the rename below and strand everything under junk labels. collect
# the worst rc, always rename, and exit with it at the end.
rip_phase rip
rip_rc=0
for t in "${titles_to_rip[@]}"; do
  makemkvcon mkv -r --profile="$TMP_PROFILE" --minlength="$minlen" disc:0 "$t" "$stage_dir/" || rip_rc=$?
done
[ "$rip_rc" -eq 0 ] || rip_note rip "makemkvcon rc=$rip_rc — partial rip, renaming what completed"

if [ "$picktype" = tv ] && [ "$extras" = 1 ]; then
  # the TV extras disc: the same _eNN-rolling rename a movie extras disc
  # gets, aimed at the TV staging dir.
  movie-name --rename-as --extras "$display" "$stage_dir" \
    || echo "rip-video: rename failed — re-run: movie-name --rename $stage_dir" >&2
elif [ "$picktype" = tv ]; then
  # the confirmed table already produced the map before the rip; junk titles
  # that failed to rip simply have no file for their entry.
  movie-name --rename-as --season "$season" --ep-map "$epmap" "$display" "$stage_dir" \
    || echo "rip-video: rename failed — re-run: movie-name --rename $stage_dir" >&2
else
  # rename staged MKVs to "<Title (Year)>_tNN.mkv" — or _eNN.mkv with --extras, so a
  # set's extras disc never collides with the movie's _tNN files; transcode files
  # _eNN as extras. the disc is ejected by hand; transcode-video finishes on the desktop.
  ra=(--rename-as); [ "$extras" = 1 ] && ra+=(--extras); [ -n "$part" ] && ra+=(--part "$part"); ra+=("$display" "$rip_dir")
  movie-name "${ra[@]}" \
    || echo "rip-video: rename failed — re-run: movie-name --rename $rip_dir" >&2
fi

# Task 4: log which subtitle languages landed in the staged MKVs (ffprobe). Danish
# preferred, English fallback; the note records the outcome so a missing-da disc is
# visible without opening the file. (a disc with only non-da/non-eng tagged subs
# and none untagged yields none — re-rip permissively if you need them.)
# glob the whole staging dir (not ${display}*) so this still reports even if rename failed.
sub_langs=$(for mk in "$stage_dir"/*.mkv; do
  ffprobe -v error -select_streams s -show_entries stream_tags=language -of csv=p=0 "$mk" 2>/dev/null
done | sed 's/^$/und/' | sort -u | paste -sd, -)
sub_langs=${sub_langs:-none}
if   [[ $sub_langs == *dan* ]]; then rip_note subtitle "subs=dan langs=$sub_langs"
elif [[ $sub_langs == *eng* ]]; then rip_note subtitle "subs=eng langs=$sub_langs (no dan)"
else                                 rip_note subtitle "subs=$sub_langs (no dan/eng)"; fi

rip_phase_end
exit "$rip_rc"
