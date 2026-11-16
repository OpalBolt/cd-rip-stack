#!/usr/bin/env bash
# Universal box setup — run ONCE inside the container (see docs/transcode.md).
# One box, every skive command: rip movie|tv|music, transcode, tag, sync-sd.
#
#   distrobox create -n transcode --image fedora:43 \
#     --volume /mnt/videos:/mnt/videos --volume /mnt/music:/mnt/music
#     (the shares: NFS mounts on the host, bind-mounted at the same paths
#     inside — adjust to your mountpoints. A box that works local paths via
#     exported VIDEOS_MOUNT/MUSIC_MOUNT needs no --volume at all.
#     Drives need nothing: distrobox shares the host's /dev by default, so
#     /dev/sr* and /dev/sg* (what makemkv enumerates) are already inside.
#     Only a box created --unshare-devsys/--unshare-all needs
#     --additional-flags "--device /dev/sr0 --device /dev/sg0".)
#   distrobox enter transcode
#   bash ~/git/cd-rip-stack/scripts/setup-transcode.sh
#
#   On the AI 350 laptop, --image fedora:43 (or newer): gfx1152 (Radeon
#   860M) needs Mesa 25.0+ for VCN encode; fedora:41's Mesa 24.x cannot
#   drive it. The BC-250 is fine on fedora:41.
#
# Idempotent: safe to re-run (a git pull + re-run also heals a broken box).
# No mise on this machine — mise.toml belongs to the rip box only.
set -euo pipefail

echo "== root packages (needs sudo inside the box) =="
# RPM Fusion first (HandBrake lives in free, MakeMKV in nonfree)
if ! rpm -q rpmfusion-free-release >/dev/null 2>&1; then
  sudo dnf install -y "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm"
fi
if ! rpm -q rpmfusion-nonfree-release >/dev/null 2>&1; then
  sudo dnf install -y "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm"
fi
# package name changed case across releases; accept either
sudo dnf install -y handbrake-cli 2>/dev/null || sudo dnf install -y HandBrake-cli
# VA-API for scripts/bench.sh's hardware runs: Fedora's mesa-va-drivers is
# built with H.264/H.265 removed (patents) — vainfo then lists only JPEG/VP9/
# AV1 — so swap in RPM Fusion's freeworld build (repo enabled above, needed
# for HandBrake anyway). Harmless on the BC-250 (its VCN is eFused off).
sudo dnf swap -y mesa-va-drivers mesa-va-drivers-freeworld \
  || sudo dnf install -y mesa-va-drivers-freeworld
sudo dnf install -y mkvtoolnix ffmpeg libva-utils tesseract \
  tesseract-langpack-eng tesseract-langpack-dan libicu git

echo "== rip stack (the box rips too) =="
# makemkvcon (RPM Fusion nonfree) for movie/tv discs, cyanrip for CDs,
# metaflac for tag, rsync for sync-sd.
sudo dnf install -y makemkv cyanrip flac rsync
# beets' replaygain plugin (gstreamer backend, config/beets-config.yaml)
# imports gi inside skive's env: GStreamer runtime here, plus the -devel
# set that pygobject/pycairo build against when uv installs them below.
sudo dnf install -y gstreamer1-plugins-good \
  gobject-introspection-devel gstreamer1-devel cairo-devel \
  gcc pkgconf-pkg-config

echo "== MakeMKV key =="
if ! grep -q '^app_Key' ~/.MakeMKV/settings.conf 2>/dev/null; then
  echo "note: no app_Key in ~/.MakeMKV/settings.conf — BD rips need one;"
  echo "      skive's scan re-warns at rip time. DVD and CD rips work without it."
fi

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
# --with pygobject builds gi into the tool env (beets' replaygain runs on
# the same interpreter as skive); --force lets a re-run refresh it.
uv tool install --editable . --with pygobject --force

echo "== verify =="
command -v HandBrakeCLI mkvmerge ffprobe tesseract seconv skive \
  makemkvcon cyanrip metaflac rsync
tesseract --list-langs   # want eng + dan
ls /dev/sr* >/dev/null 2>&1 \
  || echo "note: no /dev/sr* in the box — no drive on the host, or the box was created --unshare-devsys (drives come with distrobox's default /dev share; header above)"
va=$(vainfo --display drm 2>&1 || true)
if grep -q 'VAEntrypointEncSlice' <<<"$va"; then
  echo "VA-API encode: available ($(grep -c 'VAEntrypointEncSlice' <<<"$va") entrypoints)"
else
  echo "note: no VA-API encode entries — vainfo said:"
  sed -n '1,6p' <<<"$va" | sed 's/^/  | /'
  echo "  | expected on the BC-250 (VCN eFused off); if the AI 350 lists no"
  echo "  | H264/HEVC at all, the freeworld swap above did not take effect"
fi
test -d /mnt/videos || echo "note: /mnt/videos missing — recreate the box with --volume /mnt/videos:/mnt/videos, or export VIDEOS_MOUNT pointing at a mounted share or a local path (docs/setup.md)"
echo "OK — the box is ready. All skive runs happen inside this container."
