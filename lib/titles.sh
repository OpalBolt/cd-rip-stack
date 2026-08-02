#!/usr/bin/env bash
# list DVD/Blu-ray titles (number, length, size, audio + subtitle languages) longest-first,
# so the main feature and its tracks stand out — a movie showing eng(4) hints at a
# commentary; a 3-minute title with one eng track is a featurette. flags non-sequential
# segment order with S — a sign of an alternate/decoy cut when duplicate-length titles appear.
# thin awk filter over makemkvcon's robotic `info`. run inside the nix devshell.
# ponytail: awk over stdlib tools — no python dependency for a log parse.
set -euo pipefail
source "$(dirname "$0")/rip-common.sh"
disc="${1:-disc:0}"

# MAKEMKV_INFO overrides the info source (point it at a script that replays a
# captured `makemkvcon info` dump — inspect a scan without touching the drive).
# RIP_MINLENGTH must match rip-video.sh: makemkv renumbers titles by what survives the
# minlength scan, so a different value here makes the title numbers point elsewhere.
minlen="${RIP_MINLENGTH:-120}"

RIP_SCRIPT=titles.sh
RIP_SW=makemkvcon
RIP_NAME=$disc
rip_log_start
rip_phase titles

if [ -n "${MAKEMKV_INFO:-}" ]; then
  ${MAKEMKV_INFO} "$disc"
else
  makemkvcon -r info --minlength="$minlen" "$disc"
fi | awk '
BEGIN { FS="," }
/^CINFO:/ { split($1,c,":"); if (c[2]=="2") { v=$0; sub(/^[^"]*"/,"",v); sub(/"$/,"",v); disc=v } }
/^TINFO:/ {
  split($1,b,":"); t=b[2]+0; code=$2
  v=$0; sub(/^[^"]*"/,"",v); sub(/"$/,"",v)
  if (code==2)  name[t]=v
  if (code==9)  { dur[t]=v; split(v,h,":"); sec[t]=(h[3]!="") ? h[1]*3600+h[2]*60+h[3] : h[1]*60+h[2] }
  if (code==10) size[t]=v
  # ponytail: non-decreasing segment order = authored main playlist; a dip = decoy/alt.
  if (code==26 && v!="") {
    n=split(v,seg,",")
    for (i=2; i<=n; i++) if (seg[i]+0 < seg[i-1]+0) { scram[t]="S"; break }
  }
}
# per-title audio/subtitle languages. SINFO is title,stream,attr,"value";
# attr 1 = type (Video/Audio/Subtitles), attr 3 = language code.
/^SINFO:/ {
  split($1,b,":"); t=b[2]+0; st=$2+0; code=$3
  v=$0; sub(/^[^"]*"/,"",v); sub(/"$/,"",v)
  if (code==1) { stype[t SUBSEP st]=v; if (st > smax[t]) smax[t]=st }
  if (code==3) slang[t SUBSEP st]=v
}
END {
  for (t in scram) any=1
  if (disc) print disc > "/dev/stderr"
  if (any)  print "(S = non-sequential segments; likely an alternate or decoy cut)" > "/dev/stderr"
  for (t in sec) {
    aorder=""; sorder=""
    for (st=0; st<=smax[t]; st++) {
      ty=stype[t SUBSEP st]; l=slang[t SUBSEP st]
      if (ty=="Audio" && l!="") {
        if (!(l in acnt)) aorder=aorder (aorder?",":"") l
        acnt[l]++
      } else if (ty=="Subtitles" && l!="") {
        if (!(l in sseen)) { sseen[l]=1; sorder=sorder (sorder?",":"") l }
      }
    }
    na=split(aorder,ap,","); astr=""
    for (i=1;i<=na;i++) { l=ap[i]; astr=astr (i>1?",":"") l (acnt[l]>1?"("acnt[l]")":"") }
    printf "%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n", sec[t], t, dur[t], size[t], scram[t], name[t], astr, sorder
    split("",acnt); split("",sseen)
  }
}' | sort -t$'\t' -k1,1rn | awk -F'\t' '{printf "title %-3s %-8s %-10s %-2s A:%-14s subs:%s %s\n",$2,$3,$4,$5,$7,$8,$6}'

rip_phase_end
