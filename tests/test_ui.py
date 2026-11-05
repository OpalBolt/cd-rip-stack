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
    assert not view.tty and view._live is None
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


def test_run_summary_table_cells_count_and_banner(capsys):
    """ui.run_summary: one stderr table — per-phase cells from the parts
    strings, '-' for phases never run, total over the recorded secs, result
    ok/FAILED — then the count line, then the banner only when set."""
    rows = [
        ("A (2001)", ["copy 1s", "subs 5s", "encode 1m10s"], True),
        ("B (2002)", ["copy prefetched", "encode 30s"], False),
    ]
    ui.run_summary(rows, 1, 1, None)
    err = capsys.readouterr().err
    for col in ("Item", "copy", "merge", "subs", "encode", "total", "result"):
        assert col in err
    arow = next(l for l in err.splitlines() if l.strip().startswith("A (2001)"))
    assert "1m16s" in arow and "ok" in arow  # 1 + 5 + 70 secs, merge/detect absent
    brow = next(l for l in err.splitlines() if l.strip().startswith("B (2002)"))
    assert "prefetched" in brow and "30s" in brow and "FAILED" in brow
    assert "1 ok, 1 failed" in err
    assert "--keep:" not in err  # keep_banner=None: the honesty rule

    ui.run_summary([], 0, 0, "--keep: lossless sources moved to /x -- verify, then clean by hand")
    err = capsys.readouterr().err
    assert "--keep: lossless sources moved to /x -- verify, then clean by hand" in err


def test_fallback_view_wired_to_transcode_progress(capsys):
    """Transcode→view wiring (reviewer P2, slice 02): a fallback view set for
    a run receives the surfaced throttled encoder line; render() carries the
    parsed pct/ETA."""
    from skive import transcode
    view = ui.RunView()
    ui.set_view(view)
    try:
        transcode._progress_line("ffmpeg", "frame= 100 fps=25 Encoding: 42.5% ETA 00:01:30")
    finally:
        ui.clear_view()
    rendered = view.render()
    assert "42.5%" in rendered
    assert "ETA 00:01:30" in rendered


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


def test_bg_panel_present_updates_and_clears():
    view = _tty_seam_view()
    assert "background" not in view.render()  # no panel when no keys
    view.bg("copy", "Two (2002)_t00.mkv  40% (4/10 MB)")
    text = view.render()
    assert "background" in text
    assert "copy  Two (2002)_t00.mkv  40% (4/10 MB)" in text
    # in-place update: set twice, one line, latest text only
    view.bg("copy", "Two (2002)_t00.mkv  80% (8/10 MB)")
    text = view.render()
    assert "80% (8/10 MB)" in text
    assert "40% (4/10 MB)" not in text
    assert text.count("Two (2002)_t00.mkv") == 1
    view.bg_clear("copy")
    assert "background" not in view.render()


def test_bg_is_noop_in_fallback_mode(capsys):
    view = ui.RunView()
    assert not view.tty
    view.bg("copy", "x  40%")
    view.bg_clear("copy")
    assert capsys.readouterr().err == ""  # no output, no state
    assert "background" not in view.render()


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
