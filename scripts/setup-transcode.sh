#!/usr/bin/env bash
# Transcode box setup — run ONCE inside the container (see docs/transcode.md).
#
#   distrobox create -n transcode --image fedora:41 --volume /mnt/videos:/mnt/videos
#     (the videos share: an NFS mount on the host, bind-mounted at the same
#     path inside; adjust the host side if your mountpoint differs)
#   distrobox enter transcode
#   bash ~/git/cd-rip-stack/scripts/setup-transcode.sh
#
#   On the AI 350 laptop, create the box with --image fedora:43 (or newer)
#   instead: gfx1152 (Radeon 860M) needs Mesa 25.0+ for VCN encode, and
#   fedora:41's Mesa 24.x cannot drive it. The BC-250 is fine on fedora:41.
#
# Idempotent: safe to re-run (a git pull + re-run also heals a broken box).
# No mise on this machine — mise.toml belongs to the rip box only.
set -euo pipefail

echo "== root packages (needs sudo inside the box) =="
# RPM Fusion first (HandBrake lives there), then the tools.
if ! rpm -q rpmfusion-free-release >/dev/null 2>&1; then
  sudo dnf install -y "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm"
fi
# package name changed case across releases; accept either
sudo dnf install -y handbrake-cli 2>/dev/null || sudo dnf install -y HandBrake-cli
# VA-API for scripts/bench.sh's hardware runs: Fedora's mesa-va-drivers is
# built with H.264/H.265 removed (patents) — vainfo then lists only JPEG/VP9/
# AV1 — so swap in RPM Fusion's freeworld build (repo enabled above, needed
# for HandBrake anyway). Harmless on the BC-250 (its VCN is eFused off).
sudo dnf install -y mkvtoolnix ffmpeg libva-utils tesseract \
  tesseract-langpack-eng tesseract-langpack-dan libicu git
sudo dnf swap -y mesa-va-drivers mesa-va-drivers-freeworld \
  || sudo dnf install -y mesa-va-drivers-freeworld

echo "== uv =="
if ! command -v uv >/dev/null 2>&1; then
  sudo dnf install -y uv 2>/dev/null \
    || curl -LsSf https://astral.sh/uv/install.sh | sh
fi

echo "== seconv (Subtitle Edit headless converter, pinned v5.3.0) =="
SECONV_VER=5.3.0
if [ ! -e /usr/local/bin/seconv ]; then
  sudo mkdir -p "/opt/seconv-${SECONV_VER}"
  curl -fsSL "https://github.com/SubtitleEdit/subtitleedit/releases/download/v${SECONV_VER}/SeConv-Linux-x64.tar.gz" \
    | sudo tar xz -C "/opt/seconv-${SECONV_VER}"
  sudo ln -sf "/opt/seconv-${SECONV_VER}/seconv" /usr/local/bin/seconv
fi

echo "== skive (editable install from this clone) =="
cd "$(dirname "$0")/.."
if ! command -v skive >/dev/null 2>&1; then
  uv tool install --editable .
fi

echo "== verify =="
command -v HandBrakeCLI mkvmerge ffprobe tesseract seconv skive
tesseract --list-langs   # want eng + dan
va=$(vainfo --display drm 2>&1 || true)
if grep -q 'VAEntrypointEncSlice' <<<"$va"; then
  echo "VA-API encode: available ($(grep -c 'VAEntrypointEncSlice' <<<"$va") entrypoints)"
else
  echo "note: no VA-API encode entries — vainfo said:"
  sed -n '1,6p' <<<"$va" | sed 's/^/  | /'
  echo "  | expected on the BC-250 (VCN eFused off); if the AI 350 lists no"
  echo "  | H264/HEVC at all, the freeworld swap above did not take effect"
fi
test -d /mnt/videos || { echo "ERROR: /mnt/videos missing — recreate the box with --volume /mnt/videos:/mnt/videos (see docs/transcode.md)"; exit 1; }
echo "OK — the box is ready. All skive runs happen inside this container."
