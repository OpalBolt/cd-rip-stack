#!/usr/bin/env bash
# One-off encoder benchmark: x265 medium (baseline) vs fast vs superfast,
# plus VCN hardware HEVC via ffmpeg VAAPI (full hw pipeline: VCN decode+encode).
# Usage: bash scripts/bench.sh /path/to/staged-title_t00.mkv   (run inside the transcode distrobox)
# Cuts a 5-min 1080p sample from the middle of the title and encodes it with
# each encoder; progress streams live. SVT-AV1 is absent because the 2020 Chromecast with
# Google TV cannot hardware-decode AV1 (jellyfin-androidtv#4838), so HEVC is
# the only codec this library can commit to.
set -euo pipefail
# pin the container's tools: the host's mise shims (~/.local) bleed into the
# box through the shared home and shadow /usr/bin — mise's ffmpeg does not
# see the freeworld VA-API driver (no HEVC/H264 encode entrypoints)
export PATH="/usr/bin:$PATH"
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

run_ffmpeg() { # label, global_quality, extra encoder args — hevc_vaapi CQP
  local label="$1" qp="$2"; shift 2
  local out="$work/$label.mkv" t0 t1
  t0=$(date +%s.%N)
  # device named explicitly (auto-probe fails in containers: "unknown libva
  # error"); sw decode + hwupload because VCN 4 has no MPEG-2 decode, so DVD
  # sources can't take the full-hw path — and the x265 runs sw-decode too
  ffmpeg -hide_banner -y \
    -vaapi_device /dev/dri/renderD128 -i "$sample" \
    -map 0:v:0 -map '0:a?' -map '0:s?' -c:a copy -c:s copy \
    -vf 'format=nv12|p010,hwupload' \
    -c:v hevc_vaapi -rc_mode CQP -global_quality "$qp" "$@" \
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
else
  # ask the render node directly; wayland/x11 display probing inside
  # containers can fail after the driver loads and leave the entrypoint
  # list unprinted
  va=$(vainfo --display drm 2>&1 || true)
  if grep -Eq '^ *VAProfileHEVC[^:]*:[[:space:]]*VAEntrypointEncSlice[[:space:]]*$' <<<"$va"; then
    run_ffmpeg hevc-vaapi-cqp24 24
    run_ffmpeg hevc-vaapi-cqp28 28
    # VCN HEVC needs width%64/height%16 (AMD mesa patch: "width alignment for
    # HEVC encoding should be 64 due to hardware requirement"); unaligned
    # frames get hw-padded and the padding can bleed in as a stretched right
    # edge (720-wide DVDs: 720->768 pad). Crop to alignment to compare clean —
    # for 1080p this also drops 8 bottom lines, so eyeball both variants.
    run_ffmpeg hevc-vaapi-cqp24-al64 24 \
      -vf 'crop=floor(iw/64)*64:floor(ih/16)*16,format=nv12|p010,hwupload'
  elif grep -Eq '^ *VAProfileHEVC[^:]*:[[:space:]]*VAEntrypointEncSliceLP[[:space:]]*$' <<<"$va"; then
    # mobile APUs expose encode only as the low-power entrypoint; ffmpeg
    # needs -low_power 1 to reach it
    run_ffmpeg hevc-vaapi-lp-cqp24 24 -low_power 1
    run_ffmpeg hevc-vaapi-lp-cqp28 28 -low_power 1
  else
    echo "-- no VA-API HEVC encode entrypoint (full or low-power) — vainfo said:"
    sed -n '1,40p' <<<"$va" | sed 's/^/   | /'
    echo "   | on the BC-250 this skip is expected (VCN eFused off); JPEG/VP9/AV1"
    echo "   | but no H264/HEVC anywhere = Fedora's patent-stripped mesa — the"
    echo "   | mesa-va-drivers-freeworld swap in setup-transcode.sh fixes it"
  fi
fi
echo "--"
echo "full logs and outputs in $work for eyeballing"
