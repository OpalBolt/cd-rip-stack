#!/usr/bin/env bash
# list DVD/Blu-ray titles (number, length, size, name) longest-first, so the
# main feature stands out. flags non-sequential segment order with S — a sign of
# an alternate/decoy cut when duplicate-length titles appear.
# thin awk filter over makemkvcon's robotic `info`. run inside the nix devshell.
# ponytail: awk over stdlib tools — no python dependency for a log parse.
set -euo pipefail
disc="${1:-disc:0}"

makemkvcon -r info "$disc" | awk '
BEGIN { FS="," }
/^CINFO:/ { split($1,c,":"); if (c[2]=="2") { v=$0; sub(/^[^"]*"/,"",v); sub(/"$/,"",v); disc=v } }
/^TINFO:/ {
  split($1,b,":"); t=b[2]; code=$2
  v=$0; sub(/^[^"]*"/,"",v); sub(/"$/,"",v)
  if (code==2)  name[t]=v
  if (code==9)  { dur[t]=v; split(v,h,":")
                  sec[t]=(h[3]!="") ? h[1]*3600+h[2]*60+h[3] : h[1]*60+h[2] }
  if (code==10) size[t]=v
  # ponytail: non-decreasing segment order = authored main playlist; a dip = decoy/alt.
  if (code==26 && v!="") {
    n=split(v,s,",")
    for (i=2; i<=n; i++) if (s[i]+0 < s[i-1]+0) { scram[t]="S"; break }
  }
}
END {
  for (t in scram) any=1
  if (disc) print disc > "/dev/stderr"
  if (any)  print "(S = non-sequential segments; likely an alternate or decoy cut)" > "/dev/stderr"
  for (t in sec) printf "%d\t%s\t%s\t%s\t%s\t%s\n", sec[t], t, dur[t], size[t], scram[t], name[t]
}' | sort -t$'\t' -k1,1rn | awk -F'\t' '{printf "title %-3s %-8s %-9s %-2s %s\n",$2,$3,$4,$5,$6}'
