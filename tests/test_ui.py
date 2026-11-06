import os
import pytest
import threading

from skive import ui


@pytest.fixture(autouse=True)
def _reset_frame_state():
    """Frame state is module state (run_summary resets it in a run); a test
    that opens a frame must not leak its gutter into the next test."""
    yield
    ui._frame_open = False
    ui._scope = ""


def test_info_renders_tagged_line_on_stderr(capsys):
    ui.info("nothing to import")
    assert capsys.readouterr().err == "info  nothing to import\n"


def test_warn_renders_tagged_line_on_stderr(capsys):
    ui.warn("cannot mount music share; skipping")
    assert capsys.readouterr().err == "warn  cannot mount music share; skipping\n"


def test_fail_renders_tagged_line_on_stderr(capsys):
    ui.fail("interrupted")
    assert capsys.readouterr().err == "fail  interrupted\n"


def test_ok_renders_tagged_line_on_stderr(capsys):
    ui.ok("encoded  41m12s")
    assert capsys.readouterr().err == "ok    encoded  41m12s\n"


def test_opener_and_item_heading_render_as_plain_info_lines(capsys):
    """Plain mode: the opener and the item heading are info lines; nothing
    indents beneath the heading and no escape byte reaches the stream."""
    ui.opener("skive transcode \u00b7 4 to encode")
    ui.item(1, 4, "Rogue One (2016)")
    ui.info("copy 5744 MB at 64 MB/s  1m30s")
    ui.ok("encoded  41m12s")
    ui.warn("subtitle track 3 barren, dropped")
    err = capsys.readouterr().err
    assert err == ("info  skive transcode \u00b7 4 to encode\n"
                   "info  1/4  Rogue One (2016)\n"
                   "info  copy 5744 MB at 64 MB/s  1m30s\n"
                   "ok    encoded  41m12s\n"
                   "warn  subtitle track 3 barren, dropped\n")
    assert "\x1b" not in err


def _terminal_console(monkeypatch, **kw):
    """The shared console swapped for one that believes it is a terminal,
    writing into a buffer; returns the buffer."""
    import io

    from rich.console import Console
    buf = io.StringIO()
    monkeypatch.setattr(ui, "console", Console(
        file=buf, force_terminal=True, width=80, theme=ui.THEME,
        highlight=False, emoji=False, **kw))
    return buf


def test_tty_lines_carry_symbols_frame_and_gutter(monkeypatch):
    """On a terminal: the opener is a closed box, `✓ · ! ✗` replace the
    words, the item opens a `╭─ n/m  Name ───` frame, and events and tool
    lines print behind its `│` gutter."""
    buf = _terminal_console(monkeypatch, color_system=None)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        ui.opener("skive transcode", "2 to encode")
        ui.item(2, 2, "Arrival (2016)")
        ui.ok("done")
        ui.info("copy 4m12s")
        ui.warn("subtitle track 3 barren, dropped")
        ui.fail("encode failed")
        view.tool("handbrake", "ERROR x")
    finally:
        ui.clear_view()
    lines = buf.getvalue().splitlines()
    assert lines[0] == "\u256d\u2500 skive transcode " + "\u2500" * 60 + "\u256e"
    assert lines[1] == "\u2502 2 to encode" + " " * 66 + "\u2502"
    assert lines[2] == "\u2570" + "\u2500" * 78 + "\u256f"
    assert lines[3:] == ["\u256d\u2500 2/2  Arrival (2016) " + "\u2500" * 57,
                         "\u2502 \u2713 done",
                         "\u2502 \u00b7 copy 4m12s",
                         "\u2502 ! subtitle track 3 barren, dropped",
                         "\u2502 \u2717 encode failed",
                         "\u2502 handbrake  ERROR x"]
    assert view.replay_lines() == ["warn  subtitle track 3 barren, dropped",
                                   "fail  encode failed"]


def test_tty_lines_use_theme_colours(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    buf = _terminal_console(monkeypatch, color_system="standard")
    ui.ok("a")
    ui.warn("b")
    ui.fail("c")
    out = buf.getvalue()
    assert "\x1b[32m\u2713" in out and "\x1b[33m!" in out and "\x1b[31m\u2717" in out


def test_no_color_on_a_terminal_is_the_plain_fallback(monkeypatch, capsys):
    """NO_COLOR (or TERM=dumb) even on a terminal: plain tagged lines, no
    escape codes and no box characters — opener, frame, phase lines and the
    summary alike."""
    for env, value in (("NO_COLOR", "1"), ("TERM", "dumb")):
        monkeypatch.setenv(env, value)
        buf = _terminal_console(monkeypatch, color_system="standard")
        view = ui.RunView()  # detects: not a styled view under NO_COLOR/dumb
        assert not view.tty
        ui.opener("skive transcode", "1 to encode")
        ui.item(1, 1, "X (2001)")
        ui.ok("3664 MB at 19 MB/s", phase="copy", elapsed="3m06s")
        ui.item_end("ok", "done", "3m06s")
        ui.run_summary([("X (2001)", ["copy 3m06s"], "ok")], 1, 0)
        err = capsys.readouterr().err
        assert not any(c in buf.getvalue() for c in "\u256d\u2502\u2570\x1b")  # the plain table
        assert err.startswith("info  skive transcode \u00b7 1 to encode\n"
                              "info  1/1  X (2001)\n"
                              "ok    copy 3664 MB at 19 MB/s 3m06s\n")
        assert not any(c in err for c in "\u256d\u2502\u2570\x1b")
        assert err.endswith("ok    1 ok\n")
        monkeypatch.delenv(env)


def test_print_lock_exists_for_whole_line_writes():
    assert isinstance(ui._print_lock, type(threading.Lock()))


# -- run view (slice 02): fallback rendering is the tagged lines, no panels ----

def test_fallback_view_renders_tagged_lines_no_panels(capsys):
    """A RunView on non-TTY stderr (pytest) holds no rich objects and its
    event/tool output is the plain tagged lines on stderr."""
    import sys

    view = ui.RunView()
    assert not view.tty and view._live is None
    ui.set_view(view)
    try:
        ui.info("nothing to import")
        view.tool("handbrake", "Encoding: title 42 %")
        out = capsys.readouterr()
        assert out.err == "info  nothing to import\nhandbrake  Encoding: title 42 %\n"
    finally:
        ui.clear_view()


def _lanes(view, width=80):
    """The live box's lane rows: the `now` border rows and the side borders
    stripped (each rendered row is exactly the box width)."""
    rows = view.render(width).rstrip("\n").split("\n")
    assert rows[0].startswith("\u256d\u2500 now \u2500") and rows[-1].startswith("\u2570")
    assert {len(r) for r in rows} == {min(width, 100)}
    return [r[2:-2].rstrip() for r in rows[1:-1]]


def test_render_is_the_footer_line_events_go_to_scrollback(capsys):
    """render() (the test seam) returns the live box: one lane with
    position, item, phase, a bar, the percent and ETA (ADR 0014). Tool lines
    print to scrollback (stderr), never into the box."""
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        view.footer("2/3", "Foo (2001)", "encode")
        view.progress(42.5, "00:03:11")
        view.tool("handbrake", "Encoding: title 42.50 %")
        lanes = _lanes(view)
        assert len(lanes) == 1
        assert lanes[0].startswith("2/3 Foo (2001) \u00b7 encode  \u2501")
        assert lanes[0].endswith("42%  ETA 00:03:11")
        assert "handbrake" not in view.render()
        assert "handbrake  Encoding: title 42.50 %" in capsys.readouterr().err
    finally:
        ui.clear_view()


def test_footer_resets_progress_on_a_new_phase():
    view = ui.RunView(tty=True, live=False)
    view.footer("1/2", "Foo (2001)", "copy")
    view.progress(100, None)
    view.footer("1/2", "Foo (2001)", "subs")
    assert _lanes(view) == ["1/2 Foo (2001) \u00b7 subs"]


def test_footer_truncates_the_name_so_percent_and_eta_fit():
    view = ui.RunView(tty=True, live=False)
    view.footer("2/4", "A Very Long Movie Title That Will Not Fit (2016)", "encode")
    view.progress(42, "31m")
    line = _lanes(view, width=60)[0]
    assert line.startswith("2/4 A Very")
    assert "\u2026" in line and line.endswith("42%  ETA 31m")


def test_progress_omits_none_fields():
    view = ui.RunView()
    view.progress(42.5, None)
    assert "42%" in view.render() and "ETA" not in view.render()
    view.progress(None, "00:01:00")
    text = view.render()
    assert "%" not in text and "ETA 00:01:00" in text


def test_close_is_idempotent():
    view = ui.RunView()
    view.close()
    view.close()  # safe from finally, no state left to stop twice


def test_replay_buffer_keeps_warn_and_fail_tty_only():
    """The view keeps every warn/fail line for the summary replay; a fallback
    view (the lines are already in the scroll) keeps none."""
    tty = ui.RunView(tty=True, live=False)
    tty.event("warn", "w1")
    tty.event("info", "i1")
    tty.event("fail", "f1")
    assert tty.replay_lines() == ["warn  w1", "fail  f1"]
    fallback = ui.RunView()
    fallback.event("warn", "w1")
    assert fallback.replay_lines() == []


def test_run_summary_table_cells_result_line_and_banner(capsys):
    """ui.run_summary: one stderr table — per-phase cells from the parts
    strings, '-' for phases never run, total over the recorded secs, result
    ok/failed — then the banner only when set, then the result line."""
    rows = [
        ("A (2001)", ["copy 1s", "subs 5s", "encode 1m10s"], "ok"),
        ("B (2002)", ["copy prefetched", "encode 30s"], "failed"),
    ]
    ui.run_summary(rows, 1, 1, None)
    err = capsys.readouterr().err
    for col in ("Item", "copy", "merge", "subs", "encode", "total", "result"):
        assert col in err
    arow = next(l for l in err.splitlines() if l.strip().startswith("A (2001)"))
    assert "1m16s" in arow and arow.rstrip().endswith("ok")  # 1 + 5 + 70 secs, merge/detect absent
    brow = next(l for l in err.splitlines() if l.strip().startswith("B (2002)"))
    assert "prefetched" in brow and "30s" in brow and brow.rstrip().endswith("failed")
    assert err.endswith("fail  1 failed · 1 ok\n")  # no log: no time, no pointer
    assert "--keep:" not in err  # keep_banner=None: the honesty rule

    ui.run_summary([], 0, 0, "--keep: lossless sources moved to /x -- verify, then clean by hand")
    err = capsys.readouterr().err
    assert err.endswith("--keep: lossless sources moved to /x -- verify, then clean by hand\n"
                        "ok    0 ok\n")


def _ended_log(tmp_path, secs):
    """A RipLog whose clock says `secs` passed since start."""
    from datetime import datetime, timedelta

    from skive.common import RipLog
    t0 = datetime(2026, 11, 12, 21, 4)
    clock = iter([t0, t0 + timedelta(seconds=secs)])
    log = RipLog("skive transcode", "handbrake", root=tmp_path, now=lambda: next(clock))
    log.start()
    return log


def test_run_summary_plain_success_ending(tmp_path, capsys):
    log = _ended_log(tmp_path, 2 * 3600 + 41 * 60 + 7)
    ui.run_summary([("A (2001)", ["encode 1m10s"], "ok")], 1, 0, log=log)
    err = capsys.readouterr().err
    assert err.endswith("ok    1 ok · 2h41m\n")
    assert "\x1b" not in err


def test_run_summary_plain_failure_ending_points_at_the_run_log(tmp_path, capsys):
    log = _ended_log(tmp_path, 95)
    rows = [("A (2001)", ["encode 1m10s"], "ok"), ("B (2002)", ["encode 25s"], "failed")]
    ui.run_summary(rows, 1, 1, log=log)
    err = capsys.readouterr().err
    assert err.endswith(f"fail  1 failed · 1 ok · 1m35s — see "
                        f"{tmp_path}/logs/rip.jsonl (run {log.run}), "
                        f"raw {tmp_path}/logs/raw/{log.run}.jsonl\n")


def test_run_summary_failure_ending_omits_a_raw_log_not_retained(tmp_path, capsys):
    log = _ended_log(tmp_path, 95)
    log._raw_log.unlink()
    ui.run_summary([("B (2002)", ["encode 25s"], "failed")], 0, 1, log=log)
    err = capsys.readouterr().err
    assert err.endswith(f"fail  1 failed · 0 ok · 1m35s — see "
                        f"{tmp_path}/logs/rip.jsonl (run {log.run})\n")


def test_run_summary_failed_run_without_rows_ends_failed(capsys):
    """A run that failed before any row existed (an empty failed rip) still
    ends on a failure line, not `0 ok`."""
    ui.run_summary([], 0, 0, columns=("Title", "duration", "size", "result"),
                   failed=True)
    assert capsys.readouterr().err.endswith("fail  failed · 0 ok\n")


def test_run_summary_plain_interrupt_ending(tmp_path, capsys):
    log = _ended_log(tmp_path, 3600 + 12 * 60)
    rows = [("A (2001)", ["encode 1m10s"], "ok"), ("B (2002)", ["copy 4s"], "interrupted")]
    ui.run_summary(rows, 1, 0, log=log, interrupt_note="partial deleted")
    err = capsys.readouterr().err
    brow = next(l for l in err.splitlines() if l.strip().startswith("B (2002)"))
    assert brow.rstrip().endswith("interrupted")
    assert err.endswith("fail  interrupted · 1 ok · 1h12m — partial deleted\n")


def test_run_summary_tty_symbols_in_result_column_and_line():
    """On a terminal the replayed warn/fail lines carry their symbols above a
    rounded `summary` box; the result column and the box's bottom title lead
    with ✓ / ✗, and columns `-` for every row are dropped."""
    import io

    from rich.console import Console
    buf = io.StringIO()
    tty = Console(file=buf, force_terminal=True, color_system=None, width=80,
                  theme=ui.THEME)
    real = ui.console
    ui.console = tty
    try:
        ui.run_summary([("A (2001)", ["encode 1m10s"], "ok"),
                        ("B (2002)", ["encode 5s"], "interrupted")], 1, 0,
                       replay=["warn  subtitle track 3 barren, dropped"],
                       interrupt_note="partial deleted")
    finally:
        ui.console = real
    lines = buf.getvalue().splitlines()
    assert lines[0] == "! subtitle track 3 barren, dropped"
    assert lines[1].startswith("\u256d\u2500 summary \u2500") and lines[1].endswith("\u256e")
    assert lines[2].split() == ["\u2502", "Item", "encode", "total", "result", "\u2502"]
    assert "\u2713 ok" in lines[4] and "\u2717 interrupted" in lines[5]
    assert lines[-1].startswith("\u2570") and lines[-1].endswith("\u256f")
    assert " \u2717 interrupted \u00b7 1 ok \u2014 partial deleted " in lines[-1]
    assert {len(l) for l in lines[1:]} == {80}


def _tty(width):
    """A terminal console at `width` (colour-free) swapped in; returns
    (console, buffer)."""
    import io

    from rich.console import Console
    buf = io.StringIO()
    return Console(file=buf, force_terminal=True, color_system=None, width=width,
                   theme=ui.THEME, highlight=False, emoji=False), buf


@pytest.mark.parametrize("width", [80, 40, 120])
def test_item_frame_rules_and_phase_line_reach_the_frame_width(monkeypatch, width):
    """Header rule, gutter phase line (elapsed right-aligned) and closing rule
    each end exactly at min(console width, 100)."""
    con, buf = _tty(width)
    monkeypatch.setattr(ui, "console", con)
    frame = min(width, 100)
    ui.item(1, 4, "Bad Boys (1995)")
    ui.ok("3664 MB at 19 MB/s", phase="copy", elapsed="3m06s")
    ui.info("none (no bar >= 16 px)", phase="crop")
    ui.tool("handbrake", "Encoding: task 1")  # no view: the plain tool form
    ui.item_end("ok", "done", "7m56s")
    lines = buf.getvalue().splitlines()
    assert lines[0].startswith("\u256d\u2500 1/4  Bad Boys (1995) \u2500")
    assert lines[1].startswith("\u2502 \u2713 copy      3664 MB at 19 MB/s ")
    assert lines[1].endswith(" 3m06s")
    assert lines[2] == "\u2502 \u00b7 crop      none (no bar >= 16 px)"
    assert lines[3].startswith("\u2570\u2500 \u2713 done 7m56s \u2500")
    assert [len(lines[i]) for i in (0, 1, 3)] == [frame] * 3
    assert not ui._frame_open


def test_frame_gutter_carries_tool_lines_at_verbose(monkeypatch):
    con, buf = _tty(80)
    monkeypatch.setattr(ui, "console", con)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        ui.item(1, 1, "X (2001)")
        ui.tool("ffmpeg", "frame= 10")
    finally:
        ui.clear_view()
    assert buf.getvalue().splitlines()[1] == "\u2502 ffmpeg  frame= 10"


def test_new_item_closes_the_open_frame_neutrally(monkeypatch):
    con, buf = _tty(40)
    monkeypatch.setattr(ui, "console", con)
    ui.item(1, 2, "A (2001)")
    ui.item(2, 2, "B (2002)")
    lines = buf.getvalue().splitlines()
    assert lines[1] == "\u2570" + "\u2500" * 39
    assert lines[2].startswith("\u256d\u2500 2/2  B (2002)")


def test_run_summary_resets_frame_state_so_no_gutter_leaks(monkeypatch):
    """A crashed run leaves a frame open; the summary closes it and the next
    command's lines carry no gutter."""
    con, buf = _tty(80)
    monkeypatch.setattr(ui, "console", con)
    ui.item(1, 1, "Crash (2001)")
    ui.run_summary([], 0, 0, failed=True)
    assert not ui._frame_open
    ui.info("next command")
    lines = buf.getvalue().splitlines()
    assert lines[1] == "\u2570" + "\u2500" * 79  # the open frame closed first
    assert lines[-1] == "\u00b7 next command"


def test_phase_line_plain_form(capsys):
    ui.ok("3664 MB at 19 MB/s", phase="copy", elapsed="3m06s")
    ui.info("none (no bar >= 16 px)", phase="crop")
    ui.item_end("ok", "done", "7m56s")  # plain has no frames: prints nothing
    assert capsys.readouterr().err == ("ok    copy 3664 MB at 19 MB/s 3m06s\n"
                                       "info  crop none (no bar >= 16 px)\n")


@pytest.mark.parametrize("width", [80, 40])
def test_summary_box_drops_empty_columns_with_result_as_bottom_title(monkeypatch, width):
    con, buf = _tty(width)
    monkeypatch.setattr(ui, "console", con)
    ui.run_summary([("Bad Boys (1995)", ["copy 3m06s", "subs 3m12s", "encode 1m37s"], "ok"),
                    ("Bambi (1942)", ["copy 4s"], "interrupted")], 1, 0,
                   keep_banner="--keep: lossless sources moved to /x -- verify, then clean by hand",
                   interrupt_note="partial deleted")
    lines = buf.getvalue().splitlines()
    box = lines[:lines.index(next(l for l in lines if l.startswith("\u2570"))) + 1]
    assert box[0].startswith("\u256d\u2500 summary")
    assert {len(l) for l in box} == {width}
    assert "merge" not in buf.getvalue()  # `-` for every row: dropped
    assert "\u2717 interrupted \u00b7 1 ok" in box[-1]
    assert lines[-1] == "--keep: lossless sources moved to /x -- verify, then clean by hand"
    if width == 80:
        assert box[1].split()[1:-1] == ["Item", "copy", "subs", "encode", "total", "result"]
    else:  # the full result line does not fit the border: it prints under the box
        assert lines[-2] == "\u2717 interrupted \u00b7 1 ok \u2014 partial deleted"


def test_transcode_progress_plain_line_vs_tty_footer(capsys):
    """Transcode→view wiring: off a TTY the surfaced encoder line prints as
    a plain tool line; on a TTY only the footer bar moves (nothing scrolls)."""
    from skive import transcode
    line = "Encoding: task 1 of 1, 42.50 % (30 fps, avg 31 fps, ETA 00h01m30s)"
    plain = ui.RunView()
    ui.set_view(plain)
    try:
        transcode._footer_progress(line)
        transcode._progress_line("handbrake", line)
    finally:
        ui.clear_view()
    assert capsys.readouterr().err == f"handbrake  {line}\n"

    tty = ui.RunView(tty=True, live=False)
    ui.set_view(tty)
    try:
        transcode._footer_progress(line)
        transcode._progress_line("handbrake", line)
    finally:
        ui.clear_view()
    assert capsys.readouterr().err == ""
    rendered = tty.render()
    assert "42%" in rendered and "ETA 00h01m30s" in rendered


def test_suspend_is_a_no_op_context_without_a_live():
    """suspend() is a valid `with` on both the seam and fallback views (no
    Live to stop); the view keeps working inside and after."""
    for v in (ui.RunView(tty=True, live=False), ui.RunView()):
        with v.suspend():
            v.status("inside")
        v.status("after")
        assert "after" in v.render()


# -- background panel (slice 04): concurrent activity, one keyed line each ----

def _tty_seam_view():
    """A RunView forced onto the TTY render path without a Live region:
    render() is deterministic but bg()/bg_clear() behave as in TTY mode."""
    return ui.RunView(tty=True, live=False)


def test_bg_line_present_updates_and_clears():
    view = _tty_seam_view()
    assert len(_lanes(view)) == 1  # never empty: the scope stands in
    view.bg("copy", "Two (2002)_t00.mkv  40% (4/10 MB)")
    text = view.render()
    assert "copy Two (2002)_t00.mkv  40% (4/10 MB)" in text
    # in-place update: set twice, one line, latest text only
    view.bg("copy", "Two (2002)_t00.mkv  80% (8/10 MB)")
    text = view.render()
    assert "80% (8/10 MB)" in text
    assert "40% (4/10 MB)" not in text
    assert text.count("Two (2002)_t00.mkv") == 1
    view.bg_clear("copy")
    assert "Two (2002)" not in view.render()


def test_bg_is_noop_in_fallback_mode(capsys):
    view = ui.RunView()
    assert not view.tty
    view.bg("copy", "x  40%")
    view.bg_clear("copy")
    assert capsys.readouterr().err == ""  # no output, no state
    assert view._bg == {} and "x  40%" not in view.render()


def test_status_and_background_both_render():
    view = _tty_seam_view()
    view.status("item 1 of 2", "encode: One (2001).mkv")
    view.bg("copy", "Two (2002)_t00.mkv  40% (4/10 MB)")
    text = view.render()
    assert "encode: One (2001).mkv" in text  # status section
    assert "Two (2002)_t00.mkv  40% (4/10 MB)" in text  # background section


def test_suspend_stops_the_live_and_starts_a_fresh_one(monkeypatch):
    """suspend() with a Live running: the original Live is stopped inside the
    block (prompt prints to scrollback), and a NEW Live is started on exit
    (the original was discarded, not reused)."""
    view = _tty_seam_view()
    stopped = []

    class _OldLive:
        def stop(self):
            stopped.append(True)

    started = []

    class _NewLive:  # rich.live.Live stub; suspend() imports it lazily
        def __init__(self, panels, console=None, **kw):
            pass

        def start(self):
            started.append(self)

    monkeypatch.setattr("rich.live.Live", _NewLive)
    view._live = _OldLive()
    view._console = None  # the seam view never built one; the stub ignores it
    with view.suspend():
        assert view._live is None  # stopped and detached for the prompt
    assert stopped == [True]
    assert len(started) == 1 and view._live is started[0]  # a fresh Live


def test_closed_view_still_routes_fail_to_stderr_and_keeps_replay(capsys):
    """Regression (P1): after close(), ui.fail falls back to stderr (the
    view's refresh would be a no-op) while replay lines recorded before the
    close survive — transcode's summary reads them after close."""
    view = _tty_seam_view()
    ui.set_view(view)
    try:
        ui.warn("earlier warning")
        view.close()
        ui.fail("interrupted — partial deleted")
    finally:
        ui.clear_view()
    err = capsys.readouterr().err
    assert "fail  interrupted — partial deleted" in err
    assert view.replay_lines() == ["warn  earlier warning"]


# -- perf line (slice 05) ------------------------------------------------------

def test_perf_text_full_line():
    text, hot = ui.perf_text(62.4, {"cpu": 78.2, "gpu": 61.0})
    assert text == "CPU 62% \u00b7 cpu 78\u00b0C \u00b7 gpu 61\u00b0C"
    assert hot == set()


def test_perf_text_cpu_temp_only_and_no_temps():
    assert ui.perf_text(50, {"cpu": 78})[0] == "CPU 50% \u00b7 cpu 78\u00b0C"
    assert ui.perf_text(50, {})[0] == "CPU 50%"


def test_perf_text_none_pct_and_empty():
    assert ui.perf_text(None, {"gpu": 61})[0] == "gpu 61\u00b0C"
    text, hot = ui.perf_text(None, {})
    assert text == "" and hot == set()


def test_perf_text_hot_at_95_not_at_94_9():
    assert "cpu 95\u00b0C" in ui.perf_text(None, {"cpu": 95.0})[1]
    assert "cpu 95\u00b0C" in ui.perf_text(None, {"cpu": 95.4})[1]  # rounds to 95 display
    assert ui.perf_text(None, {"cpu": 94.9})[1] == set()


def _hwmon_tree(base, sensors):
    for i, (name, temp) in enumerate(sensors, 1):
        d = base / f"hwmon{i}"
        d.mkdir(parents=True)
        (d / "name").write_text(name)
        (d / "temp1_input").write_text(str(temp))


def test_read_hwmon_prefers_names(tmp_path):
    _hwmon_tree(tmp_path, [("k10temp", 78000), ("amdgpu", 61000)])
    ui._hwmon_cache.clear()
    assert ui.read_hwmon(str(tmp_path)) == {"cpu": 78.0, "gpu": 61.0}


def test_read_hwmon_fallback_fills_missing_slot(tmp_path):
    _hwmon_tree(tmp_path, [("zenpower", 77000), ("nvme", 40000)])
    ui._hwmon_cache.clear()
    # sorted walk: zenpower fills cpu, nvme fills gpu
    assert ui.read_hwmon(str(tmp_path)) == {"cpu": 77.0, "gpu": 40.0}


def test_read_hwmon_named_wins_over_earlier_generic(tmp_path):
    _hwmon_tree(tmp_path, [("nvme", 40000), ("k10temp", 78000)])
    ui._hwmon_cache.clear()
    assert ui.read_hwmon(str(tmp_path)) == {"cpu": 78.0, "gpu": 40.0}


def test_read_hwmon_missing_root_is_empty():
    ui._hwmon_cache.clear()
    assert ui.read_hwmon("/nonexistent-hwmon") == {}


def test_read_proc_stat_and_delta(tmp_path):
    a = tmp_path / "a"
    a.write_text("cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 2\n")
    b = tmp_path / "b"
    b.write_text("cpu  200 0 100 900 0 0 0 0 0 0\n")
    first = ui.read_proc_stat(str(a))
    second = ui.read_proc_stat(str(b))
    assert first == (800, 1000) and second == (900, 1200)
    pct = 100.0 * (1 - (second[0] - first[0]) / (second[1] - first[1]))
    assert pct == 50.0  # idle +100 of total +200
    assert ui.read_proc_stat(str(tmp_path / "gone")) is None
    truncated = tmp_path / "trunc"
    truncated.write_text("cpu  100 0 100\n")
    assert ui.read_proc_stat(str(truncated)) is None


def test_seam_view_perf_line_renders_and_drops():
    view = ui.RunView(tty=True, live=False)
    view.status("encoding")
    view.perf("CPU 62% \u00b7 cpu 78\u00b0C", {"cpu 78\u00b0C"})
    text = view.render()
    assert "CPU 62%" in text and "cpu 78\u00b0C" in text
    view.perf("")
    assert "CPU 62%" not in view.render()
    assert view._live is None and not hasattr(view, "_sampler")


def test_encode_live_box_lanes_at_80_columns():
    """During an encode the live box has three lanes: the item (position,
    item, phase, bar, percent, ETA), the prefetch copy, the perf line. It
    shrinks to the item lane once both are empty."""
    view = ui.RunView(tty=True, live=False)
    view.footer("2/4", "Arrival (2016)", "encode")
    view.progress(42, "31m")
    view.bg("prefetch", "Blade Runner (1982).mkv  40%")
    view.perf("CPU 62% \u00b7 cpu 78\u00b0C \u00b7 gpu 61\u00b0C")
    lines = _lanes(view, 80)
    assert len(lines) == 3
    assert lines[0].startswith("2/4 Arrival (2016) \u00b7 encode")
    assert "42%" in lines[0] and "ETA 31m" in lines[0]
    assert lines[1] == "prefetch Blade Runner (1982).mkv  40%"
    assert lines[2] == "CPU 62% \u00b7 cpu 78\u00b0C \u00b7 gpu 61\u00b0C"
    view.bg_clear("prefetch")
    view.perf("")
    lines = _lanes(view, 80)
    assert len(lines) == 1 and "ETA 31m" in lines[0]


def test_live_box_at_40_columns_drops_lanes_but_keeps_position_pct_eta():
    """Narrow: the perf lane drops, then the background lanes, then the bar
    shrinks; the position, percent and ETA always show."""
    view = ui.RunView(tty=True, live=False)
    view.footer("2/4", "A Very Long Movie Title That Will Not Fit (2016)", "encode")
    view.step("OCR eng track 3...")
    view.progress(42, "00h15m00s")
    view.bg("prefetch", "Brave (2012)_t00.mkv  21%")
    view.perf("CPU 62% \u00b7 cpu 78\u00b0C")
    lines = _lanes(view, 40)
    assert len(lines) == 1
    assert lines[0].startswith("2/4") and lines[0].endswith("42%  ETA 00h15m00s")
    assert "\u2501" in lines[0]  # a shrunk bar still shows


def test_live_box_is_never_empty_and_caps_at_100_columns():
    """No lane: the opener's scope line stands in. A 120-column console still
    renders the box 100 wide."""
    ui.opener("skive transcode", "4 of 63 this session (cap 4, ~60\u201380 min) \u00b7 59 deferred")
    view = ui.RunView(tty=True, live=False)
    assert _lanes(view, 80) == ["4 of 63 this session (cap 4, ~60\u201380 min) \u00b7 59 deferred"]
    assert len(_lanes(view, 120)) == 1  # _lanes asserts the 100-wide rows


def test_footer_line2_hot_temp_uses_hot_style():
    """A perf segment at >= 95 °C renders in the theme's `hot` style; the
    rest of line 2 stays dim."""
    view = ui.RunView(tty=True, live=False)
    text, hot = ui.perf_text(62, {"cpu": 96.0, "gpu": 61.0})
    view.perf(text, hot)
    line2 = view._footer(80).renderables[-1]
    styles = {line2.plain[s.start:s.end]: s.style for s in line2.spans}
    assert styles["cpu 96\u00b0C"] == "hot"
    assert styles["CPU 62%"] == "dim" and styles["gpu 61\u00b0C"] == "dim"


def test_fallback_view_has_no_perf(capsys):
    view = ui.RunView()
    view.perf("CPU 62% \u00b7 cpu 78\u00b0C")
    assert "CPU 62%" not in view.render()
    assert capsys.readouterr().err == ""


def test_sampler_tick_drives_view_and_clears(tmp_path):
    stat = tmp_path / "stat"
    stat.write_text("cpu  100 0 100 800 0 0 0 0 0 0\n")
    view = ui.RunView(tty=True, live=False)
    view.status("encoding")
    sampler = ui.PerfSampler(view, stat_path=str(stat),
                             hwmon_root=str(tmp_path / "hw"))
    sampler.tick()  # first tick: no delta yet → line dropped
    assert view._perf is None
    stat.write_text("cpu  200 0 100 900 0 0 0 0 0 0\n")
    ui._hwmon_cache.clear()
    _hwmon_tree(tmp_path / "hw", [("k10temp", 78000)])
    sampler.tick()
    assert view._perf[0] == "CPU 50% \u00b7 cpu 78\u00b0C"
    assert "CPU 50%" in view.render()


def test_module_suspend_delegates_to_registered_view():
    """ui.suspend() delegates to the registered view's suspend and is a
    plain no-op context when no view is registered (rip_tv/chapter_split
    prompts print normally either way)."""
    import contextlib

    v = ui.RunView(tty=True, live=False)
    calls = []
    v.suspend = lambda: calls.append(1) or contextlib.nullcontext()
    ui.set_view(v)
    try:
        with ui.suspend():
            pass
    finally:
        ui.clear_view()
    assert calls == [1]
    with ui.suspend():  # no view: still a valid context
        pass


def test_ask_prints_question_default_and_dim_choices_then_reads_on_marker(monkeypatch):
    monkeypatch.setattr(ui.sys.stdin, "isatty", lambda: True, raising=False)
    buf = _terminal_console(monkeypatch, color_system=None)
    prompts = []
    monkeypatch.setattr(ui.console, "input", lambda p="": prompts.append(p) or "  3  ")
    assert ui.ask("Pick 1-5", choices="s new search \u00b7 q quit", default="1") == "3"
    assert buf.getvalue() == "Pick 1-5 [1] s new search \u00b7 q quit\n"
    assert prompts == ["\u203a "]


def test_ask_returns_the_default_on_an_empty_answer(monkeypatch):
    _terminal_console(monkeypatch, color_system=None)
    monkeypatch.setattr(ui.console, "input", lambda p="": "   ")
    assert ui.ask("Pick 1-5", default="1") == "1"
    assert ui.ask("TMDB search") == ""


def test_ask_dims_the_choices(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    buf = _terminal_console(monkeypatch, color_system="standard")
    monkeypatch.setattr(ui.console, "input", lambda p="": "")
    ui.ask("Q", choices="q quit")
    assert "\x1b[2m q quit" in buf.getvalue()


def test_confirm_defaults_to_no_on_an_empty_answer(monkeypatch):
    buf = _terminal_console(monkeypatch, color_system=None)
    answers = iter(["", "y", "YES", "n", "x"])
    monkeypatch.setattr(ui.console, "input", lambda p="": next(answers))
    assert ui.confirm("delete 4 staged files?") is False
    assert ui.confirm("delete 4 staged files?") is True
    assert ui.confirm("delete 4 staged files?") is True
    assert ui.confirm("delete 4 staged files?") is False
    assert ui.confirm("delete 4 staged files?") is False
    assert buf.getvalue().splitlines()[0] == "delete 4 staged files? [y/N]"


def test_confirm_default_yes_is_explicit(monkeypatch):
    monkeypatch.setattr(ui.sys.stdin, "isatty", lambda: True, raising=False)
    buf = _terminal_console(monkeypatch, color_system=None)
    monkeypatch.setattr(ui.console, "input", lambda p="": "")
    assert ui.confirm("go?", default=True) is True
    assert buf.getvalue() == "go? [Y/n]\n"


def test_prompt_suspends_the_registered_view(monkeypatch):
    import contextlib

    _terminal_console(monkeypatch, color_system=None)
    monkeypatch.setattr(ui.console, "input", lambda p="": "")
    v = ui.RunView(tty=True, live=False)
    calls = []
    v.suspend = lambda: calls.append(1) or contextlib.nullcontext()
    ui.set_view(v)
    try:
        ui.ask("Q")
        ui.confirm("Q?")
    finally:
        ui.clear_view()
    assert calls == [1, 1]


def test_table_is_simple_head_with_dim_header_and_no_column_styles():
    from rich.box import SIMPLE_HEAD

    t = ui.table("#", "Title", title="T")
    assert t.box is SIMPLE_HEAD and t.header_style == "dim" and t.title == "T"
    assert [c.header for c in t.columns] == ["#", "Title"]
    assert all(not c.style for c in t.columns)


def test_ask_ends_the_marker_line_on_piped_input(monkeypatch, capsys):
    monkeypatch.setattr(ui.sys, "stdin", __import__("io").StringIO("3\n"))
    monkeypatch.setattr(ui.console, "input", lambda p: (ui.console.print(p, end=""), "3")[1])
    assert ui.ask("Pick") == "3"
    ui.info("next")
    assert "› \n" in capsys.readouterr().err


def test_out_exits_quietly_on_a_closed_pipe(monkeypatch):
    class Closed:
        def write(self, _): raise BrokenPipeError
        def flush(self): raise BrokenPipeError
        def fileno(self): return os.open(os.devnull, os.O_WRONLY)
    monkeypatch.setattr(ui.sys, "stdout", Closed())
    monkeypatch.setattr(ui.os, "dup2", lambda *a: None)
    with pytest.raises(SystemExit) as e:
        ui.out("hit")
    assert e.value.code == 0


def test_result_line_plain_joins_parts_elapsed_and_note(capsys):
    ui.result_line("fail", "rsync failed (exit 23)", "2.0 MB copied", elapsed=130,
                   note="re-run to resume")
    assert capsys.readouterr().err == (
        "fail  rsync failed (exit 23) \u00b7 2.0 MB copied \u00b7 2m10s \u2014 re-run to resume\n")


def test_result_line_on_a_terminal_leads_with_the_symbol(monkeypatch):
    buf = _terminal_console(monkeypatch, color_system=None)
    ui.result_line("ok", "synced 4.1 GB", elapsed=130)
    assert buf.getvalue() == "\u2713 synced 4.1 GB \u00b7 2m10s\n"


def test_tool_line_without_a_view_is_name_prefixed_on_stderr(capsys):
    ui.tool("cyanrip", "Ripping track 1")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "cyanrip  Ripping track 1\n"


def test_no_bare_print_in_skive_outside_ui():
    """Every line goes through ui: a bare `print(` (not `console.print(`) is
    only allowed inside the console module itself."""
    import re
    from pathlib import Path
    bare = re.compile(r"(?<![\w.])print\(")
    hits = [f"{path.name}:{n}: {line.strip()}"
            for path in sorted(Path(ui.__file__).parent.rglob("*.py"))
            if path.name != "ui.py"
            for n, line in enumerate(path.read_text().splitlines(), 1)
            if bare.search(line)]
    assert hits == []
