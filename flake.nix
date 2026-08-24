{
  description = "skive — rip CDs to FLAC, DVDs/Blu-rays to MKV";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      # allowUnfree: makemkv is unfree.
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
      python = pkgs.python313;

      skive = python.pkgs.buildPythonApplication {
        pname = "skive";
        version = "0.1.0";
        pyproject = true;
        src = ./.;
        propagatedBuildInputs = [
          python.pkgs.rich
          python.pkgs.loguru
          python.pkgs.beets
          python.pkgs.pylast # lastgenre
          python.pkgs.pillow # fetchart / embedart
          python.pkgs.beetcamp # bandcamp
        ];
        # tag.py resolves config/ as Path(__file__).parent.parent / "config",
        # i.e. <site-packages>/config — ship it there.
        postInstall = ''
          cp -r ${./config} "$out/${python.sitePackages}/config"
        '';
      };
    in
    {
      packages.${system} = {
        default = skive;
        inherit skive;
      };

      apps.${system} = {
        default = {
          type = "app";
          program = "${skive}/bin/skive";
        };
        skive = {
          type = "app";
          program = "${skive}/bin/skive";
        };
      };

      # Native tools live here, listed plainly — same as `nix-shell -p`.
      # `nix run .#skive -- rip video -h` builds only skive (no cyanrip);
      # `nix develop` gives skive + makemkv/cyanrip/flac/ffmpeg/rsync/curl.
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          skive
          pkgs.makemkv
          pkgs.cyanrip
          pkgs.flac # metaflac
          pkgs.ffmpeg # ffprobe
          pkgs.rsync
          pkgs.curl
        ];
      };
    };
}
