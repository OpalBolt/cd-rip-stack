# just recipes hide `nix develop` — the user never types it.
# the first recipe is the default, so bare `just` opens the menu.

# guided menu (fzf) over the toolkit — the default entry.
run:
    nix develop -c rip-menu

# rip a CD to FLAC. e.g.  just rip 6   |   just rip 6 whipper
rip *args:
    nix develop -c rip {{args}}

# rip a DVD/Blu-ray to lossless MKV. e.g.  just rip-video 0,5-7
rip-video *args:
    nix develop -c rip-video {{args}}
