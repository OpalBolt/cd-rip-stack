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
      # movie-name = resolve a real movie name (Title, Year, Lang) for a disc or
      # lossless MKV; DVDs rip as a label code (B1), so rip-video calls this to
      # rename staged MKVs to "<Title> (Year)_tNN.mkv". standalone helper (like
      # titles/mount-nfs) — it warns to stderr, so it does not source rip-common.
      movie-name = pkgs.runCommandLocal "movie-name" { } ''
        install -Dm755 ${./movie-name.sh} "$out/bin/movie-name"
      '';
      # titles = readable view over makemkvcon's verbose `info` output.
      titles = pkgs.runCommandLocal "titles" { } ''
        install -Dm755 ${./titles.sh} "$out/bin/titles"
        install -Dm644 ${./rip-common.sh} "$out/bin/rip-common.sh"
      '';
      # mount-nfs = shared mount-on-demand helper for the NFS shares; called by
      # sync-music (NFS_MUSIC) and rip-video/transcode-video (NFS_VIDEOS).
      mount-nfs = pkgs.runCommandLocal "mount-nfs" { } ''
        install -Dm755 ${./mount-nfs.sh} "$out/bin/mount-nfs"
      '';
      # sync-music = move finished, beets-tagged music to the NAS library.
      sync-music = pkgs.runCommandLocal "sync-music" { } ''
        install -Dm755 ${./sync-music.sh} "$out/bin/sync-music"
        install -Dm644 ${./rip-common.sh} "$out/bin/rip-common.sh"
      '';
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          rip
          rip-video
          transcode-video
          movie-name
          titles
          mount-nfs
          sync-music
          pkgs.rsync
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
          # ffprobe: read subtitle languages off ripped MKVs (Task 4 logging)
          pkgs.ffmpeg
          # movie-name: TMDB lookup for DVD label codes (B1) — curl + jq
          pkgs.curl
          pkgs.jq
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
          echo "     sync-music                  →  mount /mnt/music + move the library to the NAS (needs NFS_MUSIC)"
          echo "DVD/Blu-ray — titles [disc:N]            →  title list, longest-first (S = scrambled/decoy)"
          echo "             rip-video [title]            →  makemkvcon → lossless MKV in /mnt/videos/rip/ (DVDs prompt for a TMDB name)"
          echo "             transcode-video              →  HandBrakeCLI @ 1080p → /mnt/videos/library/ (rip then transcode)"
          echo "             movie-name [disc:N|file.mkv]  →  resolve Title (Year) [Lang]; Blu-ray from metadata, DVD via TMDB"
          echo
          echo "rip, rip-video and transcode-video log each phase + total to ./rip.log"
        '';
      };
    };
}
