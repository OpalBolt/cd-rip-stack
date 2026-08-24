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
      py = python.pkgs;

      # beets + the plugins skive's config enables (bandcamp, lastgenre,
      # embedart/fetchart, replaygain) so `skive tag`'s `python -m beets`
      # can import them.
      beets = py.beets.overridePythonAttrs (old: {
        propagatedBuildInputs = (old.propagatedBuildInputs or [ ]) ++ [
          py.pylast # lastgenre
          py.pillow # embedart / fetchart
          py.pygobject3 # replaygain (gstreamer backend)
          py.beetcamp # bandcamp
        ];
      });

      skive = py.buildPythonApplication {
        pname = "skive";
        version = "0.1.0";
        pyproject = true;
        src = ./.;
        propagatedBuildInputs = [
          py.rich
          py.loguru
          beets
        ];
        # tag.py resolves config/ as Path(__file__).parent.parent / "config",
        # i.e. <site-packages>/config — ship it there.
        postInstall = ''
          cp -r ${./config} "$out/${python.sitePackages}/config"
        '';
      };

      # Wrapped: the native tools skive shells out to, on PATH.
      # sudo/mount/umount/lsblk are deliberately NOT bundled — nixpkgs'
      # store copies are not setuid and would shadow the host's working
      # wrappers; `nix run` already exposes the host system bin on NixOS.
      gst = pkgs.gst_all_1;
      skive-wrapped = pkgs.symlinkJoin {
        name = "skive";
        paths = [ skive ];
        buildInputs = [ pkgs.makeWrapper ];
        postBuild = ''
          wrapProgram "$out/bin/skive" \
            --prefix PATH : ${pkgs.lib.makeBinPath [
              pkgs.makemkv # makemkvcon
              pkgs.cyanrip
              pkgs.flac # metaflac
              pkgs.ffmpeg # ffprobe
              pkgs.rsync
              pkgs.curl
              gst.gstreamer # replaygain backend
              gst.gst-plugins-base
              gst.gst-plugins-good
              gst.gst-plugins-ugly
            ]} \
            --prefix GST_PLUGIN_SYSTEM_PATH : ${pkgs.lib.makeSearchPath "lib/gstreamer-1.0" [
              gst.gst-plugins-base
              gst.gst-plugins-good
              gst.gst-plugins-ugly
            ]} \
            --prefix GI_TYPELIB_PATH : ${pkgs.lib.makeSearchPath "lib/girepository-1.0" [
              gst.gstreamer
              gst.gst-plugins-base
              gst.gst-plugins-good
            ]}
        '';
      };
    in
    {
      packages.${system} = {
        default = skive-wrapped;
        skive = skive-wrapped;
      };

      apps.${system} = {
        default = {
          type = "app";
          program = "${skive-wrapped}/bin/skive";
        };
        skive = {
          type = "app";
          program = "${skive-wrapped}/bin/skive";
        };
      };
    };
}
