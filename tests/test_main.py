"""Tests for skive/main.py — the -v/-vv verbosity flag on the top-level parser."""

import pytest

from skive.main import build_parser


@pytest.mark.parametrize("argv, expected", [
    (["rip", "music", "100"], 0),
    (["-v", "rip", "music", "100"], 1),
    (["-vv", "rip", "music", "100"], 2),
])
def test_verbose_levels_on_top_level_parser(argv, expected):
    """-v counts on the top-level parser: absent -> 0, -v -> 1, -vv -> 2."""
    args = build_parser().parse_args(argv)
    assert args.verbose == expected


def test_verbose_flag_is_top_level_only():
    """The flag lands on the top-level parser, so subcommands reject it."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rip", "movie", "-v"])
