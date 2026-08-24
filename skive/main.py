"""skive main entry point — argparse dispatch for rip movie|tv|music, tag, sync-sd."""

import argparse
import sys


def not_implemented(subcommand: str) -> None:
    print(f"{subcommand}: not implemented yet", file=sys.stderr)
    sys.exit(2)


def cmd_rip_movie(args):
    from skive.rip_movie import rip_movie
    rip_movie(args)


def cmd_rip_tv(args):
    from skive.rip_tv import rip_tv
    rip_tv(args)


def cmd_rip_music(args):
    from skive.rip_music import rip_music
    rip_music(args)


def cmd_tag(args):
    from skive.tag import tag
    tag(args)


def cmd_sync_sd(args):
    not_implemented("sync-sd")


def main() -> None:
    parser = argparse.ArgumentParser(prog="skive", description="Rip CDs, DVDs, and Blu-rays to MKV and FLAC.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # rip subcommand
    rip_parser = subparsers.add_parser("rip", help="rip")
    rip_subparsers = rip_parser.add_subparsers(dest="rip_type", required=True)

    # rip movie
    rip_movie_parser = rip_subparsers.add_parser("movie", help="rip movie")
    rip_movie_parser.add_argument("titles", nargs="?", default="all",
                                  help="title numbers: all, N, N-M, or comma list (default: all)")
    rip_movie_parser.add_argument("--part", type=int, metavar="N",
                                  help="part number for multi-part movies")
    rip_movie_parser.add_argument("--extras", action="store_true",
                                  help="rip extras instead of main titles")
    rip_movie_parser.add_argument("--tmdbid", metavar="ID|URL",
                                  help="TMDB movie ID or URL (skip prompt)")
    rip_movie_parser.add_argument("--verified", action="store_true",
                                  help="allow recordable media (DVD±R/RW, BD-R)")
    rip_movie_parser.set_defaults(func=cmd_rip_movie)

    # rip tv
    rip_tv_parser = rip_subparsers.add_parser("tv", help="rip TV show")
    rip_tv_parser.add_argument("titles", nargs="?", default="all",
                               help="title numbers: all, N, N-M, or comma list (default: all)")
    rip_tv_parser.add_argument("--extras", action="store_true",
                               help="rip bonus disc extras (no season/matching)")
    rip_tv_parser.add_argument("--tmdbid", metavar="tv:N|URL",
                               help="TMDB TV ID or URL (skip prompt)")
    rip_tv_parser.add_argument("--verified", action="store_true",
                               help="allow recordable media (DVD±R/RW, BD-R)")
    rip_tv_parser.set_defaults(func=cmd_rip_tv)

    # rip music
    rip_music_parser = rip_subparsers.add_parser("music", help="rip CD")
    rip_music_parser.add_argument("offset", nargs="?", default=None,
                                  help="drive read offset")
    rip_music_parser.add_argument("cyanrip_flags", nargs=argparse.REMAINDER,
                                  help="pass-through flags to cyanrip (e.g., -N, -R x)")
    rip_music_parser.set_defaults(func=cmd_rip_music)

    # tag
    tag_parser = subparsers.add_parser("tag", help="tag and file music")
    tag_parser.set_defaults(func=cmd_tag)

    # sync-sd
    sync_sd_parser = subparsers.add_parser("sync-sd", help="sync music library to SD card")
    sync_sd_parser.add_argument("mountpoint", nargs="?",
                                 help="card mountpoint (skip auto-detection)")
    sync_sd_parser.set_defaults(func=cmd_sync_sd)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()