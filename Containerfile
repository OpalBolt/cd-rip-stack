# The skive box — Fedora 43 with the full rip/transcode toolchain and a
# baked skive. Package list proven on real hardware (translated from the
# setup script this image replaced).
# spill: setup-transcode.sh deleted by the retire-old-world slice; header cited it
FROM fedora:43

# RPM Fusion first: HandBrake lives in free, MakeMKV in nonfree.
# (cyanrip is in neither — see the source build below.)
# F43's HandBrake package IS the CLI (HandBrakeCLI; the GUI is HandBrake-gui).
# handbrake-cli matches nothing — that's the Arch name; HandBrake-cli is only
# a compat Provides alias on the real package.
RUN dnf install -y \
      "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" \
      "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm" \
    && dnf install -y HandBrake

# Fedora's mesa-va-drivers is patent-stripped (no H.264/H.265 encode);
# swap in RPM Fusion's freeworld build for VA-API hardware encode.
RUN dnf swap -y mesa-va-drivers mesa-va-drivers-freeworld \
    || dnf install -y mesa-va-drivers-freeworld

RUN dnf install -y \
      mkvtoolnix ffmpeg libva-utils tesseract \
      tesseract-langpack-eng tesseract-langpack-dan libicu git \
      makemkv flac rsync \
      gstreamer1-plugins-good \
      gobject-introspection-devel gstreamer1-devel cairo-devel \
      cairo-gobject-devel \
      gcc pkgconf-pkg-config python3-devel \
      uv \
    && dnf clean all

# cyanrip isn't packaged for Fedora 43 or RPM Fusion; build from source, pinned.
RUN dnf install -y meson ninja-build ffmpeg-devel libcdio-paranoia-devel \
      libmusicbrainz5-devel libcurl-devel \
    && git clone --depth 1 --branch v0.9.3.1 https://github.com/cyanreg/cyanrip /tmp/cyanrip \
    && cd /tmp/cyanrip \
    && meson setup build --prefix=/usr/local \
    && ninja -C build install \
    && ldconfig \
    && rm -rf /tmp/cyanrip \
    && dnf clean all

# seconv, pinned at v5.3.0 (mise.toml pins latest for the dev machine).
# spill: version previously cross-referenced the deleted setup-transcode.sh
RUN mkdir -p /opt/seconv-5.3.0 \
    && curl -fsSL \
      "https://github.com/SubtitleEdit/subtitleedit/releases/download/v5.3.0/SeConv-Linux-x64.tar.gz" \
      | tar xz -C /opt/seconv-5.3.0 \
    && ln -sf /opt/seconv-5.3.0/seconv /usr/local/bin/seconv

# Bake skive (not editable, not bind-mounted). --with pygobject builds gi
# into the tool env so beets' replaygain runs on the same interpreter.
# pycairo's meson can't see uv's standalone pythons (no pkg-config/sysconfig
# metadata), so the tool env rides the distro interpreter + python3-devel.
# Tool env under /opt with the executable on PATH: compose run execs bare
# `skive` (fedora's PATH skips /root/.local/bin), and a SKIVE_UID-run box
# cannot traverse the 0700 /root that uv's default location lives under.
COPY . /src
RUN cd /src \
    && UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin \
       uv tool install --python-preference only-system . --with pygobject --force \
    && rm -rf /src
