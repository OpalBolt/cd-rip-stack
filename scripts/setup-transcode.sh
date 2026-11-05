#!/usr/bin/env bash
# Transcode box setup — run ONCE inside the container (see docs/transcode.md).
#
#   distrobox create -n transcode --image fedora:41 --volume /mnt/videos:/mnt/videos
#     (the videos share: an NFS mount on the host, bind-mounted at the same
#     path inside; adjust the host side if your mountpoint differs)
#   distrobox enter transcode
#   bash ~/git/cd-rip-stack/scripts/setup-transcode.sh
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
# mesa-va-drivers + libva-utils: VCN hardware encode for scripts/bench.sh's
# VAAPI runs (the laptop's Ryzen AI 7 350). Harmless on the BC-250.
sudo dnf install -y mkvtoolnix ffmpeg mesa-va-drivers libva-utils tesseract \
  tesseract-langpack-eng tesseract-langpack-dan libicu git

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
va=$(vainfo 2>&1 || true)
case "$va" in
  *VAEntrypointEncSlice*) echo "VA-API encode: available ($(grep -c 'VAEntrypointEncSlice' <<<"$va") entrypoints)" ;;
  *) echo "note: no VA-API encode — expected on the BC-250 (VCN eFused off); the laptop's VCN exposes them" ;;
esac
test -d /mnt/videos || { echo "ERROR: /mnt/videos missing — recreate the box with --volume /mnt/videos:/mnt/videos (see docs/transcode.md)"; exit 1; }
echo "OK — the box is ready. All skive runs happen inside this container."
