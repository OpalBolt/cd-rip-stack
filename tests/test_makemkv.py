"""Tests for MakeMKV ripping: selection, profile injection, rip, renames, notes."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from skive import common
from skive.makemkv import (
    BASELINE,
    SUBTITLE_SYNTAX,
    _run_with_progress,
    build_selection,
    inject_profile,
    looks_real,
    purge_junk,
    rename_staged,
    rip_titles,
    silent_staged,
    strip_audio_counts,
    subtitle_note,
    subtitle_shortfall,
    widened_audio,
    xml_escape,
)


def test_build_selection_empty():
    """Test build_selection with empty keep_lang."""
    sel = build_selection("")
    assert sel == BASELINE


def test_build_selection_per_type_shape():
    """Test the per-type form: audio language-scoped, subtitles unfiltered,
    video explicitly kept."""
    sel = build_selection("")
    assert sel == (
        "-sel:all,+sel:video,+sel:audio&(eng|dan|nolang|single),"
        "+sel:subtitle,-sel:(havemulti|havecore),-sel:mvcvideo"
    )
    assert "+sel:subtitle" in sel
    assert sel.endswith("-sel:mvcvideo")


def test_build_selection_origlang():
    """Test build_selection with original language."""
    sel = build_selection("jpn")
    assert sel.startswith("-sel:all,+sel:video,+sel:audio&(jpn|dan|eng|nolang|single)")


def test_build_selection_dup_guard():
    """Test dup-guard when origlang is dan/eng."""
    sel = build_selection("dan")
    assert "dan|dan" not in sel  # not doubled
    # dan is present, eng is also present (from baseline)
    assert "dan" in sel and "eng" in sel


def test_build_selection_union_fallback():
    """Test the union fallback: every scanned subtitle language joins the
    type-agnostic language union."""
    import skive.makemkv

    old = skive.makemkv.SUBTITLE_SYNTAX
    skive.makemkv.SUBTITLE_SYNTAX = "union"
    try:
        sel = build_selection("", {"jpn", "swe"})
        langs = {"eng", "dan", "nolang", "single"} | {"jpn", "swe"}
        assert sel == (
            f"-sel:all,+sel:({'|'.join(sorted(langs))}),-sel:(havemulti|havecore),-sel:mvcvideo"
        )
        # Baseline callers with no sub langs keep today's exact union
        sel2 = build_selection("", None)
        assert sel2.startswith("-sel:all,+sel:(dan|eng|nolang|single),")
        assert sel2.endswith(",-sel:(havemulti|havecore),-sel:mvcvideo")
    finally:
        skive.makemkv.SUBTITLE_SYNTAX = old
    assert SUBTITLE_SYNTAX == "per-type"


def test_build_selection_no_widening_intersecting_audio():
    """Baseline holds when the scanned audio intersects the keep set: keep=jpn
    with jpn/eng audio gives today's exact per-type and union strings."""
    import skive.makemkv

    sel = build_selection("jpn", audio_langs={"jpn", "eng"})
    assert sel.startswith("-sel:all,+sel:video,+sel:audio&(jpn|dan|eng|nolang|single)")
    assert "+sel:subtitle" in sel and sel.endswith("-sel:mvcvideo")
    old = skive.makemkv.SUBTITLE_SYNTAX
    skive.makemkv.SUBTITLE_SYNTAX = "union"
    try:
        sel2 = build_selection("jpn", {"swe"}, {"jpn", "eng"})
        langs = sorted({"jpn", "dan", "eng", "nolang", "single", "swe"})
        assert sel2 == (
            f"-sel:all,+sel:({'|'.join(langs)}),-sel:(havemulti|havecore),-sel:mvcvideo"
        )
    finally:
        skive.makemkv.SUBTITLE_SYNTAX = old


def test_build_selection_widens_on_nonintersecting_audio():
    """ADR 0022: scanned audio missing the keep set widens to every scanned
    audio language, in both syntax modes."""
    import skive.makemkv

    sel = build_selection("jpn", audio_langs={"zho", "cmn"})
    assert "+sel:audio&(jpn|dan|eng|cmn|zho|nolang|single)" in sel
    assert "+sel:video" in sel and "+sel:subtitle" in sel
    assert sel.endswith("-sel:mvcvideo")
    old = skive.makemkv.SUBTITLE_SYNTAX
    skive.makemkv.SUBTITLE_SYNTAX = "union"
    try:
        sel2 = build_selection("", None, {"zho", "cmn"})
        assert (
            sel2
            == "-sel:all,+sel:(cmn|dan|eng|nolang|single|zho),-sel:(havemulti|havecore),-sel:mvcvideo"
        )
    finally:
        skive.makemkv.SUBTITLE_SYNTAX = old


def test_build_selection_no_scanned_audio_no_widening():
    """Empty/None scanned audio never widens or crashes."""
    assert build_selection("") == BASELINE
    assert build_selection("jpn", audio_langs=set()) == build_selection("jpn")


def test_build_selection_keep_all_short_circuit():
    """keep_all stages every scanned audio language regardless of overlap."""
    sel = build_selection("", audio_langs={"eng"}, keep_all=True)
    assert "+sel:audio&(dan|eng|nolang|single)" in sel  # eng already in base
    sel2 = build_selection("jpn", audio_langs={"jpn", "zho"}, keep_all=True)
    assert "+sel:audio&(jpn|dan|eng|zho|nolang|single)" in sel2


def test_widened_audio_helper():
    """widened_audio: only fires on non-intersection or keep-all."""
    assert widened_audio("jpn", {"jpn", "eng"}, False) == set()
    assert widened_audio("jpn", {"zho"}, False) == {"zho"}
    assert widened_audio("jpn", {"eng"}, True) == {"eng"}
    assert widened_audio("", None, False) == set()


def test_strip_audio_counts():
    """Count suffixes from _audio_str strings are stripped at the caller seam."""
    assert strip_audio_counts(["jpn(2)", "eng"]) == {"jpn", "eng"}
    assert strip_audio_counts([]) == set()


def test_baseline_xml_escape_contract():
    """Test that the file's escaped BASELINE literal is byte-identical to the
    escaped Python constant (the injection target)."""
    from skive.makemkv import CONFIG_DIR

    content = (CONFIG_DIR / "custom.mmcp.xml").read_text(encoding="utf-8")
    escaped = xml_escape(BASELINE)
    assert content.count(escaped) == 4  # app_DefaultSelectionString + 3 trackSettings


def test_inject_profile(tmp_path, monkeypatch):
    """Test profile injection replaces the escaped BASELINE outside the repo."""
    monkeypatch.chdir(tmp_path)
    new_sel = "-sel:all,+sel:video,+sel:audio&(jpn|dan|eng|nolang|single),+sel:subtitle,-sel:(havemulti|havecore),-sel:mvcvideo"
    tmp_profile = inject_profile(new_sel)

    content = tmp_profile.read_text()
    assert xml_escape(new_sel) in content
    assert xml_escape(BASELINE) not in content

    tmp_profile.unlink()


def test_rip_titles_all():
    """Test rip_titles with None (all)."""
    mock_run = Mock(return_value=Mock(returncode=0))
    profile = Path("/tmp/test.xml")

    rc = rip_titles(None, profile, 120, Path("/tmp/stage"), run=mock_run)

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "disc:0" in cmd
    assert "all" in cmd
    assert rc == 0


def test_rip_titles_list():
    """Test rip_titles with a list."""
    mock_run = Mock(return_value=Mock(returncode=0))
    profile = Path("/tmp/test.xml")

    rc = rip_titles([0, 2], profile, 120, Path("/tmp/stage"), run=mock_run)

    assert mock_run.call_count == 2
    assert rc == 0


def test_rip_titles_worst_rc():
    """Test rip_titles collects worst return code."""

    def side_effect(cmd):
        if "0" in cmd:
            return Mock(returncode=0)
        else:
            return Mock(returncode=2)

    mock_run = Mock(side_effect=side_effect)
    profile = Path("/tmp/test.xml")

    rc = rip_titles([0, 1], profile, 120, Path("/tmp/stage"), run=mock_run)
    assert rc == 2


def test_purge_junk():
    """Test purge_junk removes junk-labeled files."""
    stage = Path("/tmp/test_purge")
    stage.mkdir(exist_ok=True)

    (stage / "B1_t0.mkv").touch()
    (stage / "Movie (2020)_t01.mkv").touch()
    (stage / "Real Title_t00.mkv").touch()  # not junk pattern

    purge_junk([stage])

    assert not (stage / "B1_t0.mkv").exists()
    assert (stage / "Movie (2020)_t01.mkv").exists()
    assert (stage / "Real Title_t00.mkv").exists()


def test_looks_real():
    """Test looks_real heuristic."""
    assert looks_real("A Real Title")
    assert looks_real("A Real Title (2020)")
    assert not looks_real("B1")  # junk label
    assert not looks_real("C2")
    assert not looks_real("SHORT")
    assert not looks_real("ABC")  # all caps, short


def test_rename_staged():
    """Test rename_staged renames junk files."""
    stage = Path("/tmp/test_rename")
    stage.mkdir(exist_ok=True)

    # Create a junk-labeled file
    (stage / "B1_t0.mkv").touch()

    rename_staged("Movie (2020)", stage, suffix="t")

    assert not (stage / "B1_t0.mkv").exists()
    assert (stage / "Movie (2020)_t01.mkv").exists()


def test_rename_staged_preexisting():
    """Files present before the rip are never renamed, even by extras."""
    import shutil

    stage = Path("/tmp/test_rename_pre")
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()

    old = stage / "Other Movie (1999)_t00.mkv"
    old.touch()
    new = stage / "Bonus Disc_t01.mkv"
    new.touch()

    rename_staged("Movie (2020)", stage, suffix="e", preexisting={old})

    assert old.exists()
    assert (stage / "Movie (2020) - Extra 01.mkv").exists()


def test_rename_staged_part():
    """Test rename_staged with part suffix."""
    stage = Path("/tmp/test_rename_part")
    stage.mkdir(exist_ok=True)

    (stage / "B1_t0.mkv").touch()

    rename_staged("Movie (2020)", stage, suffix="t", part=1)

    assert (stage / "Movie (2020)_t01.p1.mkv").exists()


def test_rename_staged_roll():
    """Test rename_staged rolls past existing."""
    stage = Path("/tmp/test_roll")
    stage.mkdir(exist_ok=True)

    (stage / "Movie (2020)_t01.mkv").touch()
    (stage / "B1_t0.mkv").touch()

    rename_staged("Movie (2020)", stage, suffix="t")

    assert (stage / "Movie (2020)_t02.mkv").exists()
    assert not (stage / "B1_t0.mkv").exists()


def test_rename_staged_skip_real():
    """Test rename_staged skips already-named and real-looking files."""
    stage = Path("/tmp/test_skip")
    stage.mkdir(exist_ok=True)

    (stage / "Movie (2020)_t01.mkv").touch()  # already this display
    (stage / "Another Movie (2021)_t02.mkv").touch()  # looks real
    (stage / "B1_t0.mkv").touch()  # junk

    rename_staged("Movie (2020)", stage, suffix="t")

    assert (stage / "Movie (2020)_t01.mkv").exists()
    assert (stage / "Another Movie (2021)_t02.mkv").exists()
    assert not (stage / "B1_t0.mkv").exists()
    assert (stage / "Movie (2020)_t02.mkv").exists()


def test_rename_staged_extras():
    """Test rename_staged names extras as '<display> - Extra NN'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stage = Path(tmpdir)
        (stage / "B1_t1.mkv").touch()

        rename_staged("Movie (2020)", stage, suffix="e")

        assert (stage / "Movie (2020) - Extra 01.mkv").exists()


def test_subtitle_note():
    """Test subtitle_note parses ffprobe output."""
    stage = Path("/tmp/test_sub")
    stage.mkdir(exist_ok=True)

    # Create a dummy mkv file so glob finds it
    (stage / "test.mkv").touch()

    mock_output = "dan\neng\n"
    mock_run = Mock(return_value=Mock(stdout=mock_output))

    note = subtitle_note(stage, run=mock_run)

    assert note.startswith("subs=dan langs=")


def test_subtitle_note_no_dan():
    """Test subtitle_note with no dan subtitles."""
    stage = Path("/tmp/test_sub_nodan")
    stage.mkdir(exist_ok=True)

    (stage / "test.mkv").touch()

    mock_output = "eng\nfra\n"
    mock_run = Mock(return_value=Mock(stdout=mock_output))

    note = subtitle_note(stage, run=mock_run)

    assert "subs=eng" in note
    assert "(no dan)" in note


def test_subtitle_note_no_dan_eng():
    """Test subtitle_note with neither dan nor eng."""
    stage = Path("/tmp/test_sub_none")
    stage.mkdir(exist_ok=True)

    (stage / "test.mkv").touch()

    mock_output = "fra\nspa\n"
    mock_run = Mock(return_value=Mock(stdout=mock_output))

    note = subtitle_note(stage, run=mock_run)

    assert "(no dan/eng)" in note


def test_subtitle_note_empty():
    """Test subtitle_note with no subtitles."""
    stage = Path("/tmp/test_sub_empty")
    stage.mkdir(exist_ok=True)

    mock_run = Mock(return_value=Mock(stdout=""))

    note = subtitle_note(stage, run=mock_run)

    assert note == "subs=none"


def test_promote_staged(tmp_path, capsys):
    """promote_staged moves mkvs up one dir and never clobbers."""
    from skive.makemkv import promote_staged

    sub = tmp_path / "d1"
    sub.mkdir()
    (sub / "Movie_t00.mkv").write_text("a")
    (sub / "Clash.mkv").write_text("b")
    (tmp_path / "Clash.mkv").write_text("existing")

    promote_staged(sub)

    assert (tmp_path / "Movie_t00.mkv").exists()
    assert (sub / "Clash.mkv").exists()  # not clobbered
    assert (tmp_path / "Clash.mkv").read_text() == "existing"
    err = capsys.readouterr().err
    assert "Clash.mkv already exists — left in d1/" in err


def test_rename_staged_extras_real_looking():
    """Extras rename even real-looking bonus-disc names (Bonus Disc_tNN → Extra NN)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        stage = Path(tmpdir)
        (stage / "Rogue One- A Star Wars Story Bonus Disc_t00.mkv").touch()
        (stage / "Rogue One- A Star Wars Story Bonus Disc_t01.mkv").touch()

        rename_staged("Rogue One (2016)", stage, suffix="e")

        assert (stage / "Rogue One (2016) - Extra 00.mkv").exists()
        assert (stage / "Rogue One (2016) - Extra 01.mkv").exists()


def test_rip_header():
    from skive.makemkv import rip_header
    from skive.scan import TitleInfo

    infos = [
        TitleInfo(0, 5000, 30 * 1024**3, "eng,dan(2)", "eng,dan", False),
        TitleInfo(1, 600, 1024**3, "eng", "", False),
    ]
    h = rip_header("Movie (2020)", infos)
    assert "Movie (2020)" in h and "titles: 2" in h
    assert "31.0 GiB" in h
    assert "audio: dan,eng" in h and "subs:  dan,eng" in h


# -- verbosity tiers on the _run_with_progress console prints ------------------


def _mkvcon(lines, rc=0):
    """A fake makemkvcon Popen: stdout iterates lines, wait returns rc."""
    proc = Mock(stdout=iter(lines))
    proc.wait.return_value = rc
    return proc


@pytest.fixture
def verbosity(monkeypatch):
    """Set the module console verbosity for one test, restore afterwards."""

    def set_level(level):
        monkeypatch.setattr(common, "VERBOSITY", level)

    return set_level


def test_run_with_progress_quiet_at_default(verbosity, capsys):
    """Level 0: MSG and raw lines stay hidden; the skive header still shows."""
    verbosity(0)
    with patch(
        "skive.makemkv.subprocess.Popen",
        return_value=_mkvcon(["PRGV:100,200,65536", 'MSG:1007,"Working"', "drive chatter"]),
    ):
        rc = _run_with_progress(["makemkvcon", "mkv"], "test", header="Movie (2020)")
    assert rc == 0
    err = capsys.readouterr().err
    assert "Movie (2020)" in err  # skive's own lines print at every level
    assert "Working" not in err
    assert "drive chatter" not in err


def test_run_with_progress_msg_shown_at_v1(verbosity, capsys):
    """Level 1: MSG lines stream (parsed value); raw/unknown lines stay hidden."""
    verbosity(1)
    with patch(
        "skive.makemkv.subprocess.Popen",
        return_value=_mkvcon(['MSG:1007,"Working"', "drive chatter"]),
    ):
        rc = _run_with_progress(["makemkvcon", "mkv"], "test")
    assert rc == 0
    err = capsys.readouterr().err
    assert "Working" in err
    assert "drive chatter" not in err


def test_run_with_progress_raw_passthrough_at_v2(verbosity, capsys):
    """Level 2: raw/unknown lines pass through; PRGV still only drives the bar."""
    verbosity(2)
    with patch(
        "skive.makemkv.subprocess.Popen",
        return_value=_mkvcon(["PRGV:100,200,65536", 'MSG:1007,"Working"', "drive chatter"]),
    ):
        rc = _run_with_progress(["makemkvcon", "mkv"], "test")
    assert rc == 0
    err = capsys.readouterr().err
    assert "Working" in err
    assert "drive chatter" in err
    assert "PRGV:100,200,65536" not in err  # bar driver, never a console line


def test_run_with_progress_failure_replays_hidden_lines(verbosity, capsys):
    """Failure at level 0: hidden output replays verbatim (story 7)."""
    verbosity(0)
    with patch(
        "skive.makemkv.subprocess.Popen",
        return_value=_mkvcon(['MSG:1007,"Working"', "drive chatter"], rc=2),
    ):
        rc = _run_with_progress(["makemkvcon", "mkv"], "test")
    assert rc == 2
    err = capsys.readouterr().err
    assert 'MSG:1007,"Working"' in err
    assert "drive chatter" in err


def test_run_with_progress_success_replays_nothing(verbosity, capsys):
    """Success at level 0 stays silent: the raw log alone records the output."""
    verbosity(0)
    with patch("skive.makemkv.subprocess.Popen", return_value=_mkvcon(["drive chatter"])):
        rc = _run_with_progress(["makemkvcon", "mkv"], "test")
    assert rc == 0
    assert "drive chatter" not in capsys.readouterr().err


# -- three-line status block ---------------------------------------------------


def test_status_block_renders_both_lines():
    """Lines 1/2: what + drive, then phase · title x/y · sizes · disc · ETA."""
    from skive.makemkv import status_block

    line1, line2 = status_block(
        what="Movie (2020)",
        drive="disc:0",
        phase="Saving to MKV file 1/3",
        title_x=2,
        title_y=5,
        cur="1.2 GiB",
        total="4.0 GiB",
        disc="6.4 GiB/31.0 GiB",
        eta="3:07",
    )
    assert line1 == "Movie (2020) — disc:0"
    assert line2 == (
        "Saving to MKV file 1/3 · title 2/5 · 1.2 GiB / 4.0 GiB · disc 6.4 GiB/31.0 GiB · ETA 3:07"
    )


def test_status_block_omits_unknown_segments():
    """Without size/ETA inputs the segments disappear (fast scans, sizeless rips)."""
    from skive.makemkv import status_block

    line1, line2 = status_block(what="Show (2021)", drive="disc:1", phase="", title_x=0, title_y=1)
    assert line1 == "Show (2021) — disc:1"
    assert line2 == "starting · title 0/1"


def test_fmt_eta():
    """_fmt_eta: m:ss under an hour, h:mm:ss above, never negative."""
    from skive.makemkv import _fmt_eta

    assert _fmt_eta(0) == "0:00"
    assert _fmt_eta(187) == "3:07"
    assert _fmt_eta(3723) == "1:02:03"
    assert _fmt_eta(-5) == "0:00"


def test_run_with_progress_leaves_final_summary_line(verbosity, capsys):
    """On exit the Live region clears; one static line records the outcome."""
    verbosity(0)
    with patch(
        "skive.makemkv.subprocess.Popen", return_value=_mkvcon(["PRGV:32768,65536,65536"], rc=0)
    ):
        rc = _run_with_progress(
            ["makemkvcon", "mkv", "disc:1"],
            "all titles",
            total_titles=2,
            sizes=[30 * 1024**3, 1024**3],
        )
    assert rc == 0
    err = capsys.readouterr().err
    assert "all titles: done · title 0/2 · disc 31.0 GiB/31.0 GiB" in err
    assert "PRGV" not in err  # the block itself never leaks into the capture


def test_run_with_progress_what_and_title_offset(verbosity, capsys):
    """what/title_offset: the fallback transition event and the summary line
    name the rip and show true x/y."""
    verbosity(0)
    with patch(
        "skive.makemkv.subprocess.Popen",
        return_value=_mkvcon(['PRGC:50,"Saving to MKV file 1 of 1"', "PRGV:32768,65536,65536"]),
    ):
        rc = _run_with_progress(
            ["makemkvcon", "mkv", "disc:0"],
            "title 12 (2/5)",
            total_titles=5,
            what="Movie (2020)",
            title_offset=1,
        )
    assert rc == 0
    err = capsys.readouterr().err
    assert "info  title 12 (2/5): Saving to MKV file 1 of 1 · title 2/5" in err
    assert "title 12 (2/5): done · title 2/5" in err  # summary shows true x/y too


def test_rip_titles_per_title_threads_header_and_position(verbosity, capsys):
    """Per-title rips: every fallback transition names the display title and
    true x/y, while the static header still prints exactly once."""
    verbosity(0)
    header = "Movie (2020)\n  titles: 2  est total: 31.0 GiB\n  audio: eng\n  subs:  none"
    with patch(
        "skive.makemkv.subprocess.Popen",
        side_effect=lambda cmd, **kw: _mkvcon(
            ['PRGC:50,"Saving to MKV file 1 of 1"', "PRGV:32768,65536,65536"]
        ),
    ):
        rc = rip_titles(
            [12, 14], Path("/tmp/p.xml"), 120, Path("/tmp/stage"), disc=0, header=header
        )
    assert rc == 0
    err = capsys.readouterr().err
    assert err.count("titles: 2") == 1  # header printed once
    # True position across the selection: title 1 then title 2, of 2 total.
    assert "info  title 12 (1/2): Saving to MKV file 1 of 1 · title 1/2" in err
    assert "info  title 14 (2/2): Saving to MKV file 1 of 1 · title 2/2" in err
    assert "title 12 (1/2): done · title 1/2" in err
    assert "title 14 (2/2): done · title 2/2" in err


def test_run_with_progress_seam_view_carries_status_block(verbosity, capsys):
    """With a seam view set the footer carries status_block's lines and the
    bar's pct; the summary lands in scrollback as an event (ADR 0013)."""
    from skive import ui

    verbosity(0)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch(
            "skive.makemkv.subprocess.Popen",
            return_value=_mkvcon(
                [
                    'PRGC:50,"Saving to MKV file 1 of 1"',
                    "PRGV:32768,65536,65536",
                    "PRGV:65535,65536,65536",
                ]
            ),
        ):
            rc = _run_with_progress(
                ["makemkvcon", "mkv", "disc:0"],
                "title 12 (2/5)",
                total_titles=5,
                what="Movie (2020)",
                title_offset=1,
            )  # no sizes: line 2 fits the 80-col render
    finally:
        ui.clear_view()
    assert rc == 0
    out = view.render(width=160)  # both status_block parts on footer line 1
    assert "Movie (2020) — disc:0" in out  # status_block line 1
    assert "Saving to MKV file 1 of 1 · title 2/5" in out  # line 2
    assert "100%" in out  # the last PRGV drove the percent (clamped near-total)
    # the summary event prints to scrollback, not the footer
    assert "title 12 (2/5): done · title 2/5" in capsys.readouterr().err
    assert "done" not in out


def test_run_with_progress_fallback_progress_line_cadence(verbosity, capsys):
    """Fallback: a plain makemkv progress line every PRGV at -vv, none quiet
    inside the 60 s throttle window."""
    verbosity(0)
    with patch(
        "skive.makemkv.subprocess.Popen",
        return_value=_mkvcon(["PRGV:32768,65536,65536", "PRGV:65535,65536,65536"]),
    ):
        rc = _run_with_progress(["makemkvcon", "mkv"], "test")
    assert rc == 0
    assert "%" not in capsys.readouterr().err

    verbosity(2)
    with patch("skive.makemkv.subprocess.Popen", return_value=_mkvcon(["PRGV:32768,65536,65536"])):
        rc = _run_with_progress(["makemkvcon", "mkv"], "test")
    assert rc == 0
    err = capsys.readouterr().err
    assert "makemkv  100%" in err


# -- Interrupt (Ctrl+C) mid-rip (CONTEXT.md: Interrupt) ------------------------


def _stdout_then_interrupt(lines):
    """A fake makemkvcon stdout that streams lines, then the interrupt lands."""

    def gen():
        yield from lines
        raise common.Interrupted()

    return gen()


def test_run_with_progress_interrupt_kills_child_and_reraises(verbosity, capsys, monkeypatch):
    """Ctrl+C mid-rip: the child goes through the forward/kill helper, the Live
    region closes with no summary line, and Interrupted propagates to the
    command's exit path (partial purge + status=interrupted + 130)."""
    verbosity(0)
    proc = Mock(stdout=_stdout_then_interrupt(["PRGV:100,200,65536"]))
    kill = Mock()
    monkeypatch.setattr(common, "kill_child_after_grace", kill)
    with patch("skive.makemkv.subprocess.Popen", return_value=proc):
        with pytest.raises(common.Interrupted):
            _run_with_progress(["makemkvcon", "mkv"], "test", header="Movie (2020)")
    kill.assert_called_once_with(proc)
    # No stale bar, no outcome summary: the interrupt line comes from the
    # command's interrupted_exit, not from here.
    assert "done" not in capsys.readouterr().err


def test_run_with_progress_wait_interrupt_also_kills_child(verbosity, monkeypatch):
    """The interrupt can land while waiting on the child after the output
    stream ends — the kill still runs."""
    verbosity(0)
    proc = Mock(stdout=iter([]))
    proc.wait.side_effect = common.Interrupted()
    kill = Mock()
    monkeypatch.setattr(common, "kill_child_after_grace", kill)
    with patch("skive.makemkv.subprocess.Popen", return_value=proc):
        with pytest.raises(common.Interrupted):
            _run_with_progress(["makemkvcon", "mkv"], "test")
    kill.assert_called_once_with(proc)


def test_run_with_progress_footer_names_the_title_being_ripped(verbosity):
    """names: when the title at a rip position starts saving, the footer
    names it (rip tv: the episode) and keeps the drive."""
    from skive import ui

    verbosity(0)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch(
            "skive.makemkv.subprocess.Popen",
            return_value=_mkvcon(['PRGC:50,"Saving to MKV file 1 of 1"', "PRGV:100,65536,65536"]),
        ):
            _run_with_progress(
                ["makemkvcon", "mkv", "disc:0"],
                "title 5 (3/6)",
                total_titles=6,
                what="Andor (2022)",
                title_offset=2,
                device="/dev/sr0",
                names=["a", "b", "Andor (2022) S01E04"],
            )
    finally:
        ui.clear_view()
    # the full lane text is read from the view (the render may still
    # ellipsize at the box width)
    out = view._label.plain
    assert "Andor (2022) S01E04 — /dev/sr0" in out
    assert "title 3/6" in out


@pytest.mark.parametrize(
    "rc, last, phase",
    [
        (
            0,
            "\u2570\u2500 \u2713 done 0s ",
            "\u2502 \u2713 rip       all titles: done \u00b7 title 2/2",
        ),
        (
            2,
            "\u2570\u2500 \u2717 failed: rip (rc=2) 0s ",
            "\u2502 \u2717 rip       all titles: failed (rc=2) \u00b7 title 2/2",
        ),
    ],
)
def test_run_with_progress_closes_each_title_frame_with_result_and_time(
    verbosity, capsys, rc, last, phase
):
    """Each title the caller framed (on_title_start opens ui.item) closes
    neutrally when the next title starts (its outcome is never reported, so a
    title that failed partway must not read done) after a timed neutral rip
    phase line, and at the end with the call's outcome; the last frame
    carries the timed ✓/✗ rip phase line."""
    from skive import ui

    verbosity(0)
    ui.set_view(ui.RunView(tty=True, live=False))
    try:
        with patch(
            "skive.makemkv.subprocess.Popen",
            return_value=_mkvcon(
                ['PRGC:50,"Saving to MKV file 1 of 2"', 'PRGC:50,"Saving to MKV file 2 of 2"'],
                rc=rc,
            ),
        ):
            _run_with_progress(
                ["makemkvcon", "mkv", "disc:0"],
                "all titles",
                total_titles=2,
                on_title_start=lambda n: ui.item(n, 2, f"title {n}"),
            )
    finally:
        ui.clear_view()
    lines = capsys.readouterr().err.splitlines()
    frames = [line for line in lines if line[:1] in "\u256d\u2570"]
    assert frames[0].startswith("\u256d\u2500 1/2  title 1")
    assert frames[1] == "\u2570" + "\u2500" * (len(frames[1]) - 2) + "\u256f"  # neutral: no result
    assert frames[2].startswith("\u256d\u2500 2/2  title 2")
    assert frames[3].startswith(last) and len(frames) == 4
    phase_lines = [line for line in lines if line.startswith("\u2502 ") and " rip " in line]
    assert len(phase_lines) == 2 and phase_lines[1].startswith(phase)
    assert phase_lines[0].startswith("\u2502 \u00b7 rip       title 1/2 ")  # no outcome claimed
    assert "saved" not in phase_lines[0]
    assert all(line.endswith(" 0s \u2502") for line in phase_lines)
    assert lines.index(phase_lines[0]) == lines.index(frames[1]) - 1  # inside the first frame
    assert lines.index(phase_lines[1]) == lines.index(frames[3]) - 1  # inside the last frame


def test_run_with_progress_plain_keeps_summary_without_phase_line(verbosity, capsys):
    """Plain mode: the per-call summary keeps today's wording; no phase line,
    no frame rules."""
    from skive import ui

    verbosity(0)
    ui.set_view(ui.RunView(tty=False, live=False))
    try:
        with patch(
            "skive.makemkv.subprocess.Popen",
            return_value=_mkvcon(['PRGC:50,"Saving to MKV file 1 of 1"']),
        ):
            _run_with_progress(
                ["makemkvcon", "mkv", "disc:0"],
                "title 3 (1/1)",
                on_title_start=lambda n: ui.item(n, 1, f"title {n}"),
            )
    finally:
        ui.clear_view()
    err = capsys.readouterr().err
    assert "info  title 3 (1/1): done \u00b7 title 1/1" in err
    assert " rip " not in err and "\u2570" not in err


def test_rip_titles_records_each_selected_titles_rc(tmp_path):
    """outcomes: a selected-title rip records every title's own rc beside
    the worst rc it returns."""
    rcs = iter([0, 2, 0])
    run = Mock(side_effect=lambda cmd: Mock(returncode=next(rcs)))
    outcomes = {}
    worst = rip_titles([3, 5, 7], tmp_path / "p.xml", 600, tmp_path, run=run, outcomes=outcomes)
    assert worst == 2
    assert outcomes == {3: 0, 5: 2, 7: 0}


# --- silent_staged: the post-rip audio gate (ADR 0019) ----------------------

_MKV_FIXTURES = Path(__file__).parent / "fixtures" / "mkv"


def test_silent_staged_trips_on_silent_fixture():
    """A video-only fixture MKV trips the gate (real ffprobe)."""
    silent = _MKV_FIXTURES / "silent.mkv"
    assert silent_staged([silent]) == [silent]


def test_silent_staged_passes_fixture_with_audio():
    """A fixture with an audio track passes silently (real ffprobe)."""
    withaudio = _MKV_FIXTURES / "withaudio.mkv"
    assert silent_staged([withaudio]) == []


def test_silent_staged_mocked_run():
    """Empty ffprobe stdout = zero audio streams; healthy stdout passes."""
    files = [Path("/s/M_t01.mkv"), Path("/s/M_t02.mkv")]
    outs = iter(["", "audio\n"])
    run = Mock(side_effect=lambda cmd, **k: Mock(stdout=next(outs), returncode=0))
    assert silent_staged(files, run=run) == [files[0]]


def test_silent_staged_skips_probe_failures():
    """An unreadable file is a probe failure, not a silent rip — never trips."""
    run = Mock(return_value=Mock(stdout="", returncode=2))
    assert silent_staged([Path("/s/M_t01.mkv")], run=run) == []


# --- subtitle_shortfall: the rip-time subtitle shortfall warning (ADR 0022) ---


def test_subtitle_shortfall_fixture_promised_and_staged():
    """Promised dan, fixture has a dan sub track (real ffprobe) — no warning."""
    f = _MKV_FIXTURES / "dansub.mkv"
    assert subtitle_shortfall({f: {"dan"}}) == []


def test_subtitle_shortfall_fixture_promised_missing():
    """Promised dan+eng, fixture stages dan only — the warning names eng."""
    f = _MKV_FIXTURES / "dansub.mkv"
    warnings = subtitle_shortfall({f: {"dan", "eng"}})
    assert warnings == [f"{f.name}: scan promised eng, staged none of it"]


def test_subtitle_shortfall_fixture_subless_disc():
    """Empty promise (genuinely subless disc) — nothing logged, no probe."""
    f = _MKV_FIXTURES / "silent.mkv"
    run = Mock(side_effect=AssertionError("must not probe"))
    assert subtitle_shortfall({f: set()}, run=run) == []


def test_subtitle_shortfall_mocked_run():
    """Per-file ffprobe, one warning per shortfall file naming the missing langs."""
    files = [Path("/s/M_t01.mkv"), Path("/s/M_t02.mkv")]
    outs = iter(["eng\n", "dan\neng\n"])
    run = Mock(side_effect=lambda cmd, **k: Mock(stdout=next(outs), returncode=0))
    warnings = subtitle_shortfall({files[0]: {"dan", "eng"}, files[1]: {"dan"}}, run=run)
    assert warnings == ["M_t01.mkv: scan promised dan, staged none of it"]


def test_subtitle_shortfall_empty_when_all_delivered():
    """Every promised keep-set language staged — zero output."""
    run = Mock(return_value=Mock(stdout="dan\neng\n", returncode=0))
    assert subtitle_shortfall({Path("/s/M_t01.mkv"): {"dan", "eng"}}, run=run) == []
