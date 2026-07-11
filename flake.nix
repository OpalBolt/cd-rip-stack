{
  description = "Disc ripping environment — CD (whipper/cyanrip/beets) + DVD/Blu-ray (makemkv/handbrake)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      # import (not legacyPackages) so config.allowUnfree applies — makemkv
      # is unfree. the free packages (whipper/cyanrip/beets/handbrake) are unaffected.
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
      beets = pkgs.beets.overridePythonAttrs (old: {
        # propagatedBuildInputs (NOT buildInputs): these must be on beets'
        # runtime PYTHONPATH so plugins import and the `beetsplug` namespace
        # (PEP 420) resolves across store paths.
        propagatedBuildInputs = (old.propagatedBuildInputs or [ ]) ++ [
          pkgs.python3.pkgs.pylast # lastgenre
          pkgs.python3.pkgs.pillow # embedart / fetchart image ops
          pkgs.python3.pkgs.pygobject3 # replaygain (gstreamer)
          pkgs.python3.pkgs.beetcamp # bandcamp
        ];
      });
      # rip = standalone script (rip.sh), installed as a `rip` command in the
      # devshell. Copied verbatim so rip.sh stays a directly-runnable script.
      rip = pkgs.runCommandLocal "rip" { } ''
        install -Dm755 ${./rip.sh} "$out/bin/rip"
      '';
      # rip-video = standalone video script, installed like `rip` above.
      rip-video = pkgs.runCommandLocal "rip-video" { } ''
        install -Dm755 ${./rip-video.sh} "$out/bin/rip-video"
      '';
      # titles = readable view over makemkvcon's verbose `info` output.
      titles = pkgs.runCommandLocal "titles" { } ''
        install -Dm755 ${./titles.sh} "$out/bin/titles"
      '';
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          rip
          rip-video
          titles
          pkgs.cyanrip
          pkgs.whipper
          pkgs.python3.pkgs.pycdio
          pkgs.vlc
          beets
          # replaygain backend
          pkgs.gst_all_1.gstreamer
          pkgs.gst_all_1.gst-plugins-good
          pkgs.gst_all_1.gst-plugins-ugly
          # dvd/blu-ray
          pkgs.makemkv
          pkgs.handbrake
        ];

        shellHook = ''
          echo "whipper $(whipper --version 2>/dev/null || echo '?') | cyanrip $(cyanrip -V 2>/dev/null || echo '?') | beets $(beet version 2>/dev/null || echo '?')"
          alias tag='beet -c beets-config.yaml import ~/media/audio/music/rip/'
          echo "rip <offset> [whipper|cyanrip] [flags...]  —  rip to FLAC (default: cyanrip), log elapsed time"
          echo "  engine optional: 'rip 6 -R x' works without naming it"
          echo "  flags pass through: cyanrip -N skips MusicBrainz, -R x force-re-rip; whipper runs -U by default"
          echo "tag                            —  fetch metadata, cover art, genres, replaygain + sort"
          echo
          echo "makemkv + handbrake $(HandBrakeCLI --version 2>&1 | head -1 || echo '?')  —  DVD/Blu-ray"
          echo "titles [disc:N]            —  list DVD/BR titles, longest-first (pick the main feature)"
          echo "rip-video [title]          —  makemkvcon → HandBrakeCLI @ 1080p (all titles default)"
        '';
      };
    };
}
