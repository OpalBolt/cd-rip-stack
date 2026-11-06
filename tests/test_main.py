"""Tests for skive/main.py — the -v/-vv verbosity flag on the top-level parser,
and the report-only `validate` subcommand."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import skive.main as skive_main
from skive.main import build_parser
from skive.validation import Finding, movie_document, write_validation, VALIDATION_FILENAME


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


# -- validate: report-only library checker, exit 0 always ----------------------


def test_validate_subcommand_wired():
    """`skive validate` parses with no arguments and dispatches to the checker
    runner."""
    args = build_parser().parse_args(["validate"])
    assert args.func is skive_main.cmd_validate


def test_validate_dispatches_to_the_report_only_runner(monkeypatch):
    runner = Mock()
    monkeypatch.setattr("skive.validation.run_validate", runner)
    args = build_parser().parse_args(["validate"])

    args.func(args)

    runner.assert_called_once_with(json_=False, issues=False, fix=False)


def test_validate_json_issues_and_fix_flags_pass_through(monkeypatch):
    """--json, --issues and --fix compose and reach the runner as kwargs."""
    runner = Mock()
    monkeypatch.setattr("skive.validation.run_validate", runner)
    args = build_parser().parse_args(["validate", "--json", "--issues", "--fix"])

    args.func(args)

    runner.assert_called_once_with(json_=True, issues=True, fix=True)


def test_validate_clean_library_exits_zero(tmp_path, monkeypatch, capsys):
    """A clean (empty) library: the walk prints no findings and main returns —
    exit 0, never a verdict."""
    (tmp_path / "library" / "movies").mkdir(parents=True)
    (tmp_path / "library" / "tv").mkdir()
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    monkeypatch.setattr(skive_main, "install_interrupt_handler", lambda: None)
    monkeypatch.setattr(sys, "argv", ["skive", "validate"])

    skive_main.main()

    assert "no findings" in capsys.readouterr().err


def test_validate_library_full_of_findings_still_exits_zero(tmp_path, monkeypatch, capsys):
    """A library full of findings: the table prints, main returns — findings
    are leads, exit 0 always."""
    (tmp_path / "library" / "movies").mkdir(parents=True)
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    monkeypatch.setattr(
        "skive.validation.validate_library",
        Mock(return_value=[Finding("movies/X (2000)", "ratio",
                                   "X (2000).mkv  0.50", "yellow")]))
    monkeypatch.setattr(skive_main, "install_interrupt_handler", lambda: None)
    monkeypatch.setattr(sys, "argv", ["skive", "validate"])

    skive_main.main()

    out = capsys.readouterr().err
    assert "ratio" in out and "X (2000)" in out


def test_validate_missing_library_warns_and_exits_zero(tmp_path, monkeypatch, capsys):
    """A share with no library/ under it (unmounted, not yet built): one
    stderr line, exit 0 — the walk never touches the wrong tree."""
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path / "nope"))
    monkeypatch.setattr(skive_main, "install_interrupt_handler", lambda: None)
    monkeypatch.setattr(sys, "argv", ["skive", "validate"])

    skive_main.main()

    captured = capsys.readouterr()
    assert "no library/ under" in captured.err
    assert captured.out == ""


def test_validate_doc_folder_walk_needs_no_network(tmp_path, monkeypatch, capsys):
    """A doc-carrying library walks on the document alone: TMDB is never
    touched, and an unprovable encode degrades to an unreadable row — exit 0
    either way."""
    root = tmp_path / "library"
    folder = root / "movies" / "X (2000)"
    folder.mkdir(parents=True)
    (folder / "X (2000).mkv").touch()  # 0 bytes: no ffprobe can read it
    write_validation(folder / VALIDATION_FILENAME,
                     movie_document("5", "X (2000)", 6000, "V", "run12345",
                                    "t1", []))
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))  # the share root
    monkeypatch.setattr("skive.validation.search_first",
                        Mock(side_effect=AssertionError("no network in tests")))
    monkeypatch.setattr("skive.validation.fetch_by_id",
                        Mock(side_effect=AssertionError("no network in tests")))
    monkeypatch.setattr("skive.validation.fetch_season",
                        Mock(side_effect=AssertionError("no network in tests")))
    monkeypatch.setattr(skive_main, "install_interrupt_handler", lambda: None)
    monkeypatch.setattr(sys, "argv", ["skive", "validate"])

    skive_main.main()

    out = capsys.readouterr().err
    assert "X (2000)" in out
    assert "unreadable" in out
