#!/usr/bin/env bash
# One-off encoder benchmark: x265 medium (baseline) vs x265 fast vs SVT-AV1 p6/p8/p10.
# Usage: bash bench.sh /path/to/staged-title_t00.mkv   (run inside the transcode distrobox)
# Cuts a 10-min 1080p sample, encodes it 5 ways, prints a speed/size table.
set -euo pipefail
src="$1"; work="$(mktemp -d /tmp/bench-XXXX)"
sample="$work/sample.mkv"
ffmpeg -hide_banner -loglevel error -y -i "$src" -t 600 -c copy "$sample"

run() { # label, extra encoder args...
  local label="$1"; shift
  local out="$work/$label.mkv" t0 t1
  t0=$(date +%s.%N)
  HandBrakeCLI -i "$sample" -o "$out" --preset "H.265 MKV 1080p30" \
    --all-audio --aencoder copy --all-subtitles --subtitle-burned=none "$@" \
    > "$work/$label.log" 2>&1 || { echo "$label: FAILED (see $work/$label.log)"; return; }
  t1=$(date +%s.%N)
  local secs mb
  secs=$(echo "$t1 - $t0" | bc)
  mb=$(( $(stat -c%s "$out") / 1048576 ))
  # fps from the log's last avg line
  local fps
  fps=$(grep -o 'avg [0-9.]* fps' "$work/$label.log" | tail -1 | grep -o '[0-9.]*' || echo "?")
  printf '%-14s %7.1fs   avg %6s fps   %5d MB\n' "$label" "$secs" "$fps" "$mb"
}

srcmb=$(( $(stat -c%s "$sample") / 1048576 ))
echo "sample: 10 min, ${srcmb} MB -> $work"
echo "--"
run x265-medium
run x265-fast    --encoder-preset fast
run av1-p10      --encoder svt_av1 --encoder-preset 10 --quality 30
run av1-p8       --encoder svt_av1 --encoder-preset 8  --quality 30
run av1-p6       --encoder svt_av1 --encoder-preset 6  --quality 30
echo "--"
echo "eyeball the outputs in $work before deleting"
