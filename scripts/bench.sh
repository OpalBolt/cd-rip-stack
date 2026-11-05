#!/usr/bin/env bash
# One-off encoder benchmark: x265 medium (baseline) vs fast vs superfast,
# plus VCN hardware HEVC via ffmpeg VAAPI (full hw pipeline: VCN decode+encode).
# Usage: bash scripts/bench.sh /path/to/staged-title_t00.mkv   (run inside the transcode distrobox)
# Cuts a 5-min 1080p sample from the middle of the title and encodes it with
# each encoder; progress streams live. SVT-AV1 is absent because the 2020 Chromecast with
# Google TV cannot hardware-decode AV1 (jellyfin-androidtv#4838), so HEVC is
# the only codec this library can commit to.
set -euo pipefail
src="$1"; work="$(mktemp -d /tmp/bench-XXXX)"
sample="$work/sample.mkv"
echo "cutting 5-min sample from the middle..."
dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$src")
mid=$(echo "$dur / 2 - 150" | bc)
ffmpeg -hide_banner -loglevel error -y -ss "$mid" -i "$src" -t 300 -c copy "$sample"

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
  printf '%-18s %7.1fs   avg %6s fps   %5d MB\n' "$label" "$secs" "$fps" "$mb"
}

run_ffmpeg() { # label, global_quality — hevc_vaapi CQP (HandBrake has no VAAPI encoder)
  local label="$1" qp="$2"
  local out="$work/$label.mkv" t0 t1
  t0=$(date +%s.%N)
  ffmpeg -hide_banner -y \
    -hwaccel vaapi -hwaccel_output_format vaapi -i "$sample" \
    -map 0:v:0 -map '0:a?' -map '0:s?' -c:a copy -c:s copy \
    -c:v hevc_vaapi -rc_mode CQP -global_quality "$qp" \
    "$out" 2>&1 | tee "$work/$label.log" | tr '\r' '\n' | grep --line-buffered -E 'frame=|rror'
  t1=$(date +%s.%N)
  local secs mb fps
  secs=$(echo "$t1 - $t0" | bc)
  mb=$(( $(stat -c%s "$out") / 1048576 ))
  fps=$(tr '\r' '\n' < "$work/$label.log" | grep -o 'fps=[[:space:]]*[0-9.]*' | tail -1 | grep -o '[0-9.]*' || echo "?")
  printf '%-18s %7.1fs   avg %6s fps   %5d MB\n' "$label" "$secs" "$fps" "$mb"
}

srcmb=$(( $(stat -c%s "$sample") / 1048576 ))
echo "sample: 5 min, ${srcmb} MB -> $work"
echo "--"
run x265-medium
run x265-fast      --encoder-preset fast
run x265-superfast --encoder-preset superfast
# Hardware runs: meaningful on the laptop (Ryzen AI 7 350's VCN); the BC-250's
# VCN is eFused off, so vainfo shows no encode entrypoint there and this skips.
# CQP 24/28 bracket the x265 RF-22 output size — compare speed at matching size,
# then eyeball quality in $work (ADR 0010's streaming-encoder caveat applies).
if ! command -v vainfo >/dev/null 2>&1; then
  echo "-- vainfo missing — re-run scripts/setup-transcode.sh inside the box"
elif va=$(vainfo 2>&1); grep -Eq '^ *VAProfileHEVC[^:]*:[[:space:]]*VAEntrypointEncSlice' <<<"$va"; then
  run_ffmpeg hevc-vaapi-cqp24 24
  run_ffmpeg hevc-vaapi-cqp28 28
else
  echo "-- no VA-API HEVC encode entrypoint — skipping hardware runs. vainfo said:"
  sed -n '1,10p' <<<"$va" | sed 's/^/   | /'
  echo "   | on the BC-250 this skip is expected (VCN eFused off). On the AI 350 it"
  echo "   | means this container's Mesa predates gfx1152 support (needs 25.0+;"
  echo "   | fedora:41 ships 24.x). Recreate the box with a newer image:"
  echo "   |   distrobox rm -f transcode"
  echo "   |   distrobox create -n transcode --image fedora:43 --volume /mnt/videos:/mnt/videos"
fi
echo "--"
echo "full logs and outputs in $work for eyeballing"
