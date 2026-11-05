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


# -- run view (slice 02): fallback rendering is the tagged lines, no panels ----

def test_fallback_view_renders_tagged_lines_no_panels(capsys):
    """A RunView on non-TTY stderr (pytest) holds no rich objects and its
    event/tool output is the plain tagged lines on stderr."""
    import sys

    view = ui.RunView()
    assert not view.tty and not hasattr(view, "_live")
    ui.set_view(view)
    try:
        ui.info("nothing to import")
        view.tool("handbrake", "Encoding: title 42 %")
        out = capsys.readouterr()
        assert out.err == "info  nothing to import\nhandbrake  Encoding: title 42 %\n"
    finally:
        ui.clear_view()


def test_render_carries_status_progress_and_log_tail():
    """render() (the test seam) returns the panel content as one string:
    status lines, the progress line (None fields omitted), and the tool-
    prefixed log tail, newest last."""
    view = ui.RunView()
    ui.set_view(view)
    try:
        view.status("item 2 of 3", "encode: Foo (2001).mkv")
        view.progress(42.5, "00:03:11")
        view.tool("handbrake", "Encoding: title 42.50 %")
        view.tool("copy", "40%  (4/10 MB)")
        text = view.render()
        assert "item 2 of 3" in text
        assert "encode: Foo (2001).mkv" in text
        assert "42.5%  ETA 00:03:11" in text
        assert "handbrake  Encoding: title 42.50 %" in text
        assert "copy  40%  (4/10 MB)" in text
    finally:
        ui.clear_view()


def test_progress_omits_none_fields():
    view = ui.RunView()
    view.progress(42.5, None)
    assert "42.5%" in view.render() and "ETA" not in view.render()
    view.progress(None, "00:01:00")
    text = view.render()
    assert "42.5%" not in text and "ETA 00:01:00" in text


def test_close_is_idempotent():
    view = ui.RunView()
    view.close()
    view.close()  # safe from finally, no state left to stop twice
