{
  description = "CD ripping environment with whipper + cyanrip + beets";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
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
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          rip
          pkgs.cyanrip
          pkgs.whipper
          pkgs.python3.pkgs.pycdio
          pkgs.vlc
          beets
          # replaygain backend
          pkgs.gst_all_1.gstreamer
          pkgs.gst_all_1.gst-plugins-good
          pkgs.gst_all_1.gst-plugins-ugly
        ];

        shellHook = ''
          echo "whipper $(whipper --version 2>/dev/null || echo '?') | cyanrip $(cyanrip -V 2>/dev/null || echo '?') | beets $(beet version 2>/dev/null || echo '?')"
          alias tag='beet -c beets-config.yaml import ~/media/audio/music/rip/'
          echo "rip <offset> [whipper|cyanrip] [flags...]  —  rip to FLAC (default: cyanrip), log elapsed time"
          echo "  flags pass through: cyanrip -N skips MusicBrainz; whipper runs -U by default"
          echo "tag                            —  fetch metadata, cover art, genres, replaygain + sort"
        '';
      };
    };
}
