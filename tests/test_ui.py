import threading

from skive import ui


def test_info_renders_tagged_line_on_stderr(capsys):
    ui.info("nothing to import")
    assert capsys.readouterr().err == "info  nothing to import\n"


def test_warn_renders_tagged_line_on_stderr(capsys):
    ui.warn("cannot mount music share; skipping")
    assert capsys.readouterr().err == "warn  cannot mount music share; skipping\n"


def test_fail_renders_tagged_line_on_stderr(capsys):
    ui.fail("interrupted")
    assert capsys.readouterr().err == "fail  interrupted\n"


def test_print_lock_exists_for_whole_line_writes():
    assert isinstance(ui._print_lock, type(threading.Lock()))
