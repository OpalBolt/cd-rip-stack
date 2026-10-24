"""skive main entry point — argparse dispatch for rip movie|tv|music, tag, sync-sd, validate."""

import argparse
import sys

from skive.common import MENU, install_interrupt_handler, set_verbosity


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
    from skive.sync_sd import sync_sd
    sync_sd(args)


def cmd_validate(args):
    from skive.validation import run_validate
    run_validate()


def build_parser() -> argparse.ArgumentParser:
    """The skive argument parser: -v/-vv live on the TOP level so they apply
    to every subcommand (skive -v rip movie, not skive rip movie -v)."""
    parser = argparse.ArgumentParser(prog="skive", description="Rip CDs, DVDs, and Blu-rays to MKV and FLAC.")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="console verbosity: -v adds tool messages (MakeMKV MSG, cyanrip progress, HandBrake errors), -vv passes tool output through raw")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # rip subcommand
    rip_parser = subparsers.add_parser("rip", help="rip")
    rip_subparsers = rip_parser.add_subparsers(dest="rip_type", required=True)

    # rip movie
    rip_movie_parser = rip_subparsers.add_parser("movie", help="rip movie")
    rip_movie_parser.add_argument("-T", "--titles", nargs="?", const=MENU, default=None, metavar="SPEC",
                                  help="title spec: all, N, N-M, or comma list; bare -T opens a menu")
    rip_movie_parser.add_argument("--part", type=int, metavar="N",
                                  help="part number for multi-part movies")
    rip_movie_parser.add_argument("-E", "--extras", action="store_true",
                                  help="rip extras instead of main titles")
    rip_movie_parser.add_argument("--tmdbid", metavar="ID|URL",
                                  help="TMDB movie ID or URL (skip prompt)")
    rip_movie_parser.add_argument("-V", "--verified", action="store_true",
                                  help="allow recordable media (DVD±R/RW, BD-R)")
    rip_movie_parser.add_argument("-d", "--disc", type=int, default=0, metavar="N",
                                  help="drive number (default 0); use for concurrent rips from two drives")
    rip_movie_parser.set_defaults(func=cmd_rip_movie)

    # rip tv
    rip_tv_parser = rip_subparsers.add_parser("tv", help="rip TV show")
    rip_tv_parser.add_argument("-T", "--titles", nargs="?", const=MENU, default=None, metavar="SPEC",
                               help="title spec: all, N, N-M, or comma list; bare -T opens a menu")
    rip_tv_parser.add_argument("-E", "--extras", action="store_true",
                               help="rip bonus disc extras (no season/matching)")
    rip_tv_parser.add_argument("--tmdbid", metavar="tv:N|URL",
                               help="TMDB TV ID or URL (skip prompt)")
    rip_tv_parser.add_argument("-V", "--verified", action="store_true",
                               help="allow recordable media (DVD±R/RW, BD-R)")
    rip_tv_parser.add_argument("-d", "--disc", type=int, default=0, metavar="N",
                               help="drive number (default 0); use for concurrent rips from two drives")
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

    # validate — report-only library checker: findings are leads, exit 0 always
    validate_parser = subparsers.add_parser("validate",
                                            help="report library findings (read-only, never changes anything)")
    validate_parser.set_defaults(func=cmd_validate)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    set_verbosity(args.verbose)  # clamps -vvv and beyond to 2
    install_interrupt_handler()  # CONTEXT.md Interrupt: SIGINT -> clean exit path
    try:
        args.func(args)
    except KeyboardInterrupt:
        # Interrupt fallback for the paths the rip commands don't cover: the
        # interactive prompts (TMDB search, menus) and commands without staging
        # of their own. One line, no traceback, exit 130; runs that already
        # started logged their interrupted status where the RipLog was at hand.
        print("skive: interrupted", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()