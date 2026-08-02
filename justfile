# just recipes hide `nix develop` — the user never types it.
# the first recipe is the default, so bare `just` opens the menu.

# guided menu over the ripping toolkit.
run:
    nix develop -c rip-menu

# fast path: rip a CD (whipper/cyanrip) → FLAC.
rip:
    nix develop -c rip

# fast path: rip a DVD/Blu-ray → lossless MKV on the NAS.
rip-video:
    nix develop -c rip-video
