{
  description = "CD ripping environment with whipper + beets";

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
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = [
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
          echo "whipper $(whipper --version 2>/dev/null || echo '?') | beets $(beet version 2>/dev/null || echo '?')"
          rip() {
            if [ -z "$1" ]; then
              echo "usage: rip <offset>  (see drive table in README)"
              return 1
            fi
            whipper cd rip -O ~/media/audio/music/rip --offset="$1"
          }
          alias tag='beet -c beets-config.yaml import ~/media/audio/music/rip/'
          echo "rip <offset>  —  rip CD to FLAC with AccurateRip verification"
          echo "tag           —  fetch metadata, cover art, genres, replaygain + sort"
        '';
      };
    };
}
