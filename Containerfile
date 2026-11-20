# The skive box — Fedora 43 with the full rip/transcode toolchain and a
# baked skive. Package list translated from scripts/setup-transcode.sh
# (proven on real hardware); that script is the source of truth for what
# the box needs.
FROM fedora:43

# RPM Fusion first: HandBrake lives in free, MakeMKV/cyanrip in nonfree.
RUN dnf install -y \
      "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" \
      "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm" \
    && dnf install -y handbrake-cli || dnf install -y HandBrake-cli

# Fedora's mesa-va-drivers is patent-stripped (no H.264/H.265 encode);
# swap in RPM Fusion's freeworld build for VA-API hardware encode.
RUN dnf swap -y mesa-va-drivers mesa-va-drivers-freeworld \
    || dnf install -y mesa-va-drivers-freeworld

RUN dnf install -y \
      mkvtoolnix ffmpeg libva-utils tesseract \
      tesseract-langpack-eng tesseract-langpack-dan libicu git \
      makemkv cyanrip flac rsync \
      gstreamer1-plugins-good \
      gobject-introspection-devel gstreamer1-devel cairo-devel \
      gcc pkgconf-pkg-config \
      uv \
    && dnf clean all

# seconv, pinned (matches scripts/setup-transcode.sh SECONV_VER; mise.toml
# pins latest for the dev machine).
RUN mkdir -p /opt/seconv-5.3.0 \
    && curl -fsSL \
      "https://github.com/SubtitleEdit/subtitleedit/releases/download/v5.3.0/SeConv-Linux-x64.tar.gz" \
      | tar xz -C /opt/seconv-5.3.0 \
    && ln -sf /opt/seconv-5.3.0/seconv /usr/local/bin/seconv

# Bake skive (not editable, not bind-mounted). --with pygobject builds gi
# into the tool env so beets' replaygain runs on the same interpreter.
COPY . /src
RUN cd /src \
    && uv tool install . --with pygobject --force \
    && rm -rf /src
