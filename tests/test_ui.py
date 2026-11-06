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


def test_tty_lines_carry_symbols_heading_and_indent(monkeypatch):
    """On a terminal: `✓ · ! ✗` replace the words, the heading is `▸ n/m
    Name`, events and tool lines indent beneath it, the opener resets."""
    buf = _terminal_console(monkeypatch, color_system=None)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        ui.opener("skive transcode \u00b7 2 to encode")
        ui.item(2, 2, "Arrival (2016)")
        ui.ok("done")
        ui.info("copy 4m12s")
        ui.warn("subtitle track 3 barren, dropped")
        ui.fail("encode failed")
        view.tool("handbrake", "ERROR x")
    finally:
        ui.clear_view()
    assert buf.getvalue() == ("skive transcode \u00b7 2 to encode\n"
                              "\u25b8 2/2  Arrival (2016)\n"
                              "  \u2713 done\n"
                              "  \u00b7 copy 4m12s\n"
                              "  ! subtitle track 3 barren, dropped\n"
                              "  \u2717 encode failed\n"
                              "  handbrake  ERROR x\n")
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


def test_no_color_render_has_no_colour_codes(monkeypatch):
    """rich's own NO_COLOR detection: symbols stay, colour codes go."""
    import re

    monkeypatch.setenv("NO_COLOR", "1")
    buf = _terminal_console(monkeypatch, color_system="standard")
    ui.ok("a")
    ui.warn("b")
    ui.fail("c")
    ui.item(1, 1, "X")
    ui.clear_view()  # drop the heading's indent state
    out = buf.getvalue()
    assert "\u2713 a" in out and "! b" in out and "\u2717 c" in out
    assert not re.search(r"\x1b\[[0-9;]*(3[0-9]|4[0-9]|9[0-7])[;m]", out)


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


def test_render_is_the_footer_line_events_go_to_scrollback(capsys):
    """render() (the test seam) returns the footer: position, item, phase,
    a bar, the percent and ETA on one line (ADR 0013). Tool lines print to
    scrollback (stderr), never into the footer."""
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        view.footer("2/3", "Foo (2001)", "encode")
        view.progress(42.5, "00:03:11")
        view.tool("handbrake", "Encoding: title 42.50 %")
        text = view.render()
        assert text.count("\n") == 1  # one footer line, no panels
        line = text.rstrip("\n")
        assert line.startswith(" 2/3  Foo (2001) \u00b7 encode  \u2501")
        assert line.endswith("42%  ETA 00:03:11")
        assert "handbrake" not in text
        assert "handbrake  Encoding: title 42.50 %" in capsys.readouterr().err
    finally:
        ui.clear_view()


def test_footer_resets_progress_on_a_new_phase():
    view = ui.RunView(tty=True, live=False)
    view.footer("1/2", "Foo (2001)", "copy")
    view.progress(100, None)
    view.footer("1/2", "Foo (2001)", "subs")
    assert view.render() == " 1/2  Foo (2001) \u00b7 subs\n"


def test_footer_truncates_the_name_so_percent_and_eta_fit():
    view = ui.RunView(tty=True, live=False)
    view.footer("2/4", "A Very Long Movie Title That Will Not Fit (2016)", "encode")
    view.progress(42, "31m")
    line = view.render(width=60).rstrip("\n")
    assert line.startswith(" 2/4  A Very")
    assert "\u2026" in line and line.endswith("42%  ETA 31m")
    assert len(line) <= 60


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
                        f"{tmp_path}/logs/rip.jsonl (run {log.run})\n")


def test_run_summary_plain_interrupt_ending(tmp_path, capsys):
    log = _ended_log(tmp_path, 3600 + 12 * 60)
    rows = [("A (2001)", ["encode 1m10s"], "ok"), ("B (2002)", ["copy 4s"], "interrupted")]
    ui.run_summary(rows, 1, 0, log=log, interrupt_note="partial deleted")
    err = capsys.readouterr().err
    brow = next(l for l in err.splitlines() if l.strip().startswith("B (2002)"))
    assert brow.rstrip().endswith("interrupted")
    assert err.endswith("fail  interrupted · 1 ok · 1h12m — partial deleted\n")


def test_run_summary_tty_symbols_in_result_column_and_line():
    """On a terminal the result column and line lead with ✓ / ✗ and the
    replayed warn/fail lines carry their symbols; the table is borderless."""
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
    out = buf.getvalue()
    assert out.startswith("! subtitle track 3 barren, dropped\n")
    assert "✓ ok" in out and "✗ interrupted" in out
    assert "│" not in out and "|" not in out  # no column rules
    assert out.endswith("✗ interrupted · 1 ok — partial deleted\n")


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
    assert view.render() == ""  # no footer at all yet
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
    assert view.render() == ""


def test_bg_is_noop_in_fallback_mode(capsys):
    view = ui.RunView()
    assert not view.tty
    view.bg("copy", "x  40%")
    view.bg_clear("copy")
    assert capsys.readouterr().err == ""  # no output, no state
    assert view.render() == ""


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


def test_encode_footer_line2_carries_prefetch_and_perf_at_80_columns():
    """During an encode the footer is two lines: line 1 position, item,
    phase, bar, percent, ETA; line 2 the prefetch copy and the perf line.
    Line 2 disappears once both are empty."""
    view = ui.RunView(tty=True, live=False)
    view.footer("2/4", "Arrival (2016)", "encode")
    view.progress(42, "31m")
    view.bg("prefetch", "Blade Runner (1982).mkv  40%")
    view.perf("CPU 62% \u00b7 cpu 78\u00b0C \u00b7 gpu 61\u00b0C")
    lines = view.render(width=80).rstrip("\n").split("\n")
    assert len(lines) == 2
    assert lines[0].startswith(" 2/4  Arrival (2016) \u00b7 encode")
    assert "42%" in lines[0] and "ETA 31m" in lines[0]
    assert "prefetch Blade Runner (1982).mkv  40%" in lines[1]
    assert "CPU 62% \u00b7 cpu 78\u00b0C \u00b7 gpu 61\u00b0C" in lines[1]
    view.bg_clear("prefetch")
    view.perf("")
    lines = view.render(width=80).rstrip("\n").split("\n")
    assert len(lines) == 1 and "ETA 31m" in lines[0]


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
