"""Tests for skive/main.py — the -v/-vv verbosity flag on the top-level parser."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import skive.main as skive_main
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


# -- Interrupt fallback (CONTEXT.md: Interrupt): one line, exit 130 ------------

def test_main_interrupt_fallback_one_line_exit_130(monkeypatch, capsys):
    """A KeyboardInterrupt escaping a command (prompt paths without staging of
    their own) gets the one-line interrupt exit — no traceback."""
    monkeypatch.setattr(skive_main, "install_interrupt_handler", lambda: None)
    cmd = Mock(side_effect=KeyboardInterrupt)
    parser = Mock(parse_args=Mock(return_value=SimpleNamespace(verbose=0, func=cmd)))
    monkeypatch.setattr(skive_main, "build_parser", lambda: parser)

    with pytest.raises(SystemExit) as e:
        skive_main.main()

    assert e.value.code == 130
    assert capsys.readouterr().err == "skive: interrupted\n"
    cmd.assert_called_once()
