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
      # devshell. Copied verbatim so rip.sh stays a directly-runnable script;
      # rip-common.sh is installed alongside as the sourced timing/log helper.
      rip = pkgs.runCommandLocal "rip" { } ''
        install -Dm755 ${./rip.sh} "$out/bin/rip"
        install -Dm644 ${./rip-common.sh} "$out/bin/rip-common.sh"
      '';
      # rip-video = rip only (makemkvcon -> lossless MKV on the NAS). installed
      # like `rip` above; ships custom.mmcp.xml next to it (selection profile).
      rip-video = pkgs.runCommandLocal "rip-video" { } ''
        install -Dm755 ${./rip-video.sh} "$out/bin/rip-video"
        install -Dm644 ${./rip-common.sh} "$out/bin/rip-common.sh"
        install -Dm644 ${./custom.mmcp.xml} "$out/bin/custom.mmcp.xml"
      '';
      # transcode-video = batch transcode of the NAS rip/ folder to the library.
      transcode-video = pkgs.runCommandLocal "transcode-video" { } ''
        install -Dm755 ${./transcode-video.sh} "$out/bin/transcode-video"
        install -Dm644 ${./rip-common.sh} "$out/bin/rip-common.sh"
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
          transcode-video
          titles
          pkgs.cyanrip
          # metaflac: read album/artist tags off ripped FLACs for rip.log
          pkgs.flac
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
          echo "makemkv + handbrake $(HandBrakeCLI --version 2>&1 | head -1 || echo '?')"
          alias tag='beet -c beets-config.yaml import ~/media/audio/music/rip/'
          echo
          echo "CD — rip <offset> [cyanrip|whipper] [flags]  →  FLAC (default cyanrip)"
          echo "     flags pass through: cyanrip -N skips MusicBrainz, -R x re-rips; whipper runs -U"
          echo "     cyanrip prompts to pick a release if MusicBrainz matches several"
          echo "     tag  →  metadata, cover art, genres, replaygain, sort into the library"
          echo
          echo "DVD/Blu-ray — titles [disc:N]            →  title list, longest-first (S = scrambled/decoy)"
          echo "             rip-video [title]            →  makemkvcon → lossless MKV in /mnt/videos/rip/"
          echo "             transcode-video              →  HandBrakeCLI @ 1080p → /mnt/videos/library/ (rip then transcode)"
          echo
          echo "rip, rip-video and transcode-video log each phase + total to ./rip.log"
        '';
      };
    };
}
