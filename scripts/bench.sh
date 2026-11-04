#!/usr/bin/env bash
# One-off encoder benchmark: x265 medium (baseline) vs x265 fast.
# Usage: bash scripts/bench.sh /path/to/staged-title_t00.mkv   (run inside the transcode distrobox)
# Cuts a 5-min 1080p sample, encodes it both ways, prints a speed/size table.
# Progress streams live; SVT-AV1 is absent because the 2020 Chromecast with
# Google TV cannot hardware-decode AV1 (jellyfin-androidtv#4838), so HEVC is
# the only codec this library can commit to.
set -euo pipefail
src="$1"; work="$(mktemp -d /tmp/bench-XXXX)"
sample="$work/sample.mkv"
echo "cutting 5-min sample..."
ffmpeg -hide_banner -loglevel error -y -i "$src" -t 300 -c copy "$sample"

run() { # label, extra encoder args...
  local label="$1"; shift
  local out="$work/$label.mkv" t0 t1
  t0=$(date +%s.%N)
  HandBrakeCLI -i "$sample" -o "$out" --preset "H.265 MKV 1080p30" \
    --all-audio --aencoder copy --all-subtitles --subtitle-burned=none "$@" \
    2>&1 | tee "$work/$label.log" | tr '\r' '\n' | grep --line-buffered -E 'Encoding:.*%'
  t1=$(date +%s.%N)
  local secs mb fps
  secs=$(echo "$t1 - $t0" | bc)
  mb=$(( $(stat -c%s "$out") / 1048576 ))
  fps=$(grep 'Encoding:' "$work/$label.log" | tr '\r' '\n' | grep -o 'avg [0-9.]* fps' | tail -1 | grep -o '[0-9.]*' || echo "?")
  printf '%-14s %7.1fs   avg %6s fps   %5d MB\n' "$label" "$secs" "$fps" "$mb"
}

srcmb=$(( $(stat -c%s "$sample") / 1048576 ))
echo "sample: 5 min, ${srcmb} MB -> $work"
echo "--"
run x265-medium
run x265-fast    --encoder-preset fast
echo "--"
echo "full logs and outputs in $work for eyeballing"
