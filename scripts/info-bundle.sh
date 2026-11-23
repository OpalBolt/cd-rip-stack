#!/usr/bin/env bash
# info-bundle — gather this machine's encode-capability facts into one file.
#
# Usage: bash scripts/info-bundle.sh     (run on the box in question, then
# bring the info-bundle.txt it writes back to the dev machine)
#
# Works on any Linux box, host or container, repo not required. Probes are
# read-only; each bench encodes 10 s of testsrc2 to null output (CPU/GPU
# cycles only, nothing written but the bundle). Missing tools are recorded,
# never fatal — the bundle should say what a box lacks as clearly as what
# it has.
set -u

BUNDLE=info-bundle.txt
: > "$BUNDLE"

say() { printf '%s\n' "$*" >>"$BUNDLE"; }
hdr() { say ""; say "## $*"; say ""; }
have() { command -v "$1" >/dev/null 2>&1; }

hdr "machine"
say "date:  $(date -Is 2>/dev/null || date)"
say "uname: $(uname -a)"
say "distro: $(grep PRETTY_NAME /etc/os-release 2>/dev/null || echo unknown)"

hdr "cpu"
if have lscpu; then lscpu >>"$BUNDLE" 2>&1; else say "lscpu: not found"; fi

hdr "gpu (pci)"
if have lspci; then
  lspci | grep -iE 'vga|3d controller|display' >>"$BUNDLE" 2>&1 || say "no display devices listed"
else
  say "lspci: not found"
fi

hdr "render nodes"
ls -l /dev/dri >>"$BUNDLE" 2>&1 || say "/dev/dri: missing (no GPU access)"

hdr "va-api entrypoints"
if have vainfo; then
  vainfo --display drm >>"$BUNDLE" 2>&1 || vainfo >>"$BUNDLE" 2>&1 || true
else
  say "vainfo: not found (install libva-tools)"
fi

hdr "nvidia"
if have nvidia-smi; then nvidia-smi >>"$BUNDLE" 2>&1; else say "nvidia-smi: not found"; fi

hdr "ffmpeg"
if have ffmpeg; then
  ffmpeg -hide_banner -hwaccels >>"$BUNDLE" 2>&1
  say ""
  ffmpeg -hide_banner -encoders 2>/dev/null | grep -iE 'vaapi|nvenc|qsv|libx26' >>"$BUNDLE"
else
  say "ffmpeg: not found"
fi

hdr "va drivers installed"
if have rpm; then
  rpm -qa | grep -iE 'libva|mesa-va|vaapi' >>"$BUNDLE" 2>&1 || say "no libva/mesa-va packages listed"
elif have dpkg; then
  dpkg -l 2>/dev/null | grep -iE 'libva|vaapi|mesa-va' >>"$BUNDLE" || say "no libva packages listed"
else
  say "neither rpm nor dpkg found"
fi

hdr "10s 1080p30 bench per candidate backend (testsrc2 -> null)"
say "fps = encode throughput; the input is 300 synthetic frames in 10 s."
run_bench() { # run_bench <label> <ffmpeg output args...>
  local label=$1; shift
  have ffmpeg || { say "$label: skipped (no ffmpeg)"; return; }
  local stats fps speed
  stats=$(ffmpeg -hide_banner -nostdin -f lavfi -i testsrc2=size=1920x1080:rate=30 \
    -t 10 "$@" -f null - 2>&1 | tr '\r' '\n')
  fps=$(printf '%s' "$stats" | grep -oE 'fps=[ ]*[0-9]+' | tail -1)
  speed=$(printf '%s' "$stats" | grep -oE 'speed=[ ]*[0-9.]+x' | tail -1)
  if [ -n "$speed" ]; then
    say "$label: ok   ${fps:-fps=?}   $speed"
  else
    say "$label: FAILED"
    printf '%s\n' "$stats" | tail -5 | sed 's/^/    /' >>"$BUNDLE"
  fi
}
run_bench "hevc_vaapi CQP 24" -vaapi_device /dev/dri/renderD128 \
  -vf format=nv12,hwupload -c:v hevc_vaapi -rc_mode CQP -global_quality 24
run_bench "h264_vaapi QP 28" -vaapi_device /dev/dri/renderD128 \
  -vf format=nv12,hwupload -c:v h264_vaapi -qp 28
run_bench "hevc_nvenc QP 28" -c:v hevc_nvenc -rc constqp -qp 28
run_bench "h264_nvenc QP 28" -c:v h264_nvenc -rc constqp -qp 28
run_bench "libx265 fast crf 24 (cpu baseline)" -c:v libx265 -preset fast -crf 24

say ""
say "end of bundle"
echo "wrote $BUNDLE" >&2
