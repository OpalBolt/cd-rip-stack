{
  description = "skive — rip CDs to FLAC, DVDs/Blu-rays to MKV";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      # allowUnfree: makemkv is unfree.
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
    in
    {
      # Dev env only: `nix develop`, then `uv run skive ...` or `uv run python -m skive`.
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          (pkgs.python313.withPackages (ps: [
            ps.rich
            ps.beets
            ps.pylast
            ps.pillow
            ps.beetcamp
            ps.loguru
          ]))
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
