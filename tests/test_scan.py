"""Tests for disc scanning: media sniff, full scan, fast scans, title menu."""

import os
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from skive.scan import drive_media, is_recordable, iso2b, scan_disc


def test_iso2b_mapping():
    """Test ISO 639-1 to 2/B mapping."""
    assert iso2b("en") == "eng"
    assert iso2b("da") == "dan"
    assert iso2b("fr") == "fra"
    assert iso2b("xx") == "xx"  # unknown passes through


def test_is_recordable_known_recordable():
    """Test is_recordable returns True for known recordable types."""
    assert is_recordable("DVD+R")
    assert is_recordable("DVD+R-DL")
    assert is_recordable("DVD+RW")
    assert is_recordable("DVD-RW")
    assert is_recordable("DVD-RAM")
    assert is_recordable("BD-RE")
    assert is_recordable("CD-RW")
    assert is_recordable("DVD-R")
    assert is_recordable("DVD-R ")
    assert is_recordable("DVD-R-")
    assert is_recordable("CD-R")
    assert is_recordable("BD-R")
    assert is_recordable("BD-R ")


def test_is_recordable_pressed():
    """Test is_recordable returns False for pressed discs."""
    assert not is_recordable("DVD-ROM")
    assert not is_recordable("BD-ROM")
    assert not is_recordable("CD-ROM")
    assert not is_recordable("HD-DVD")
    assert not is_recordable("DVD")
    assert not is_recordable("BD")
    assert not is_recordable("CD")
    assert not is_recordable("")
    # DVD+RDL matches DVD+R* in bash, so IS recordable (not unknown)


def test_is_recordable_unknown():
    """Test is_recordable fails open on unknown tokens."""
    assert not is_recordable("SOMETHING-ELSE")


def test_is_recordable_dvd_rdl():
    """DVD+RDL matches DVD+R* in bash, so IS recordable."""
    assert is_recordable("DVD+RDL")


def test_drive_media_real(tmp_path):
    """Test drive_media parses DRV:0 line."""
    mock_output = 'DRV:0,2,0,0,"DVD-ROM hp CDDVDW TS-L633N"'
    mock_run = Mock(return_value=Mock(stdout=mock_output))

    media = drive_media(run=mock_run)
    assert media == "DVD-ROM"


def test_drive_media_env_override():
    """Test drive_media honors MAKEMKV_INFO."""
    mock_output = 'DRV:0,2,0,0,"BD-ROM Pioneer BDR-209"'
    with patch.dict("os.environ", {"MAKEMKV_INFO": f"echo '{mock_output}'"}):
        media = drive_media()
        assert media == "BD-ROM"


def test_drive_media_no_disc():
    """Test drive_media returns empty when no disc."""
    mock_output = ""
    mock_run = Mock(return_value=Mock(stdout=mock_output))

    media = drive_media(run=mock_run)
    assert media == ""


def test_scan_disc_parses_audio_subs_scrambled():
    """scan_disc parses audio counts, subtitle langs, and the S flag."""
    from skive.scan import scan_disc

    output = """
TINFO:0,9,0,"1:26:43"
TINFO:0,11,0,"5845346392"
TINFO:0,26,0,"1,2,3"
SINFO:0,0,1,0,"Video"
SINFO:0,0,3,0,"dan"
SINFO:0,1,1,0,"Audio"
SINFO:0,1,3,0,"dan"
SINFO:0,2,1,0,"Audio"
SINFO:0,2,3,0,"dan"
SINFO:0,3,1,0,"Audio"
SINFO:0,3,3,0,"eng"
SINFO:0,4,1,0,"Subtitles"
SINFO:0,4,3,0,"dan"
SINFO:0,5,1,0,"Subtitles"
SINFO:0,5,3,0,"eng"
TINFO:1,9,0,"0:03:33"
TINFO:1,11,0,"169400000"
TINFO:1,26,0,"3,2,1"
"""

    _, _, result = scan_disc(120, run=Mock(return_value=Mock(stdout=output)))

    assert len(result) == 2
    t0 = result[0]
    assert t0.number == 0
    assert t0.seconds == 1 * 3600 + 26 * 60 + 43
    assert t0.audio == "dan(2),eng"
    assert t0.subtitles == "dan,eng"
    assert t0.scrambled is False

    t1 = result[1]
    assert t1.number == 1
    assert t1.scrambled is True
    assert t1.audio == ""
    assert t1.subtitles == ""


def test_menu_state_toggle_move_confirm_abort():
    """Pure menu state machine: space toggles, arrows/jk move and wrap, Enter/q."""
    from skive.scan import _menu_state

    selected = [True, True]

    # j moves down from the header onto title 0
    sel, cur, done, abort = _menu_state(2, selected, 0, "j")
    assert cur == 1 and not done and not abort

    # space toggles title 0 (cursor 1 -> index 0) off
    sel, cur, done, abort = _menu_state(2, sel, 1, " ")
    assert sel == [False, True]

    # k moves back up to the header
    sel, cur, done, abort = _menu_state(2, sel, 1, "k")
    assert cur == 0

    # header space selects all (none were fully on)
    sel, cur, done, abort = _menu_state(2, sel, 0, " ")
    assert sel == [True, True]

    # header space again deselects all
    sel, cur, done, abort = _menu_state(2, sel, 0, " ")
    assert sel == [False, False]

    # down wraps from last row to header
    sel, cur, done, abort = _menu_state(2, sel, 1, "j")
    assert cur == 2
    sel, cur, done, abort = _menu_state(2, sel, 2, "j")
    assert cur == 0

    # Enter confirms
    sel, cur, done, abort = _menu_state(2, sel, 0, "\r")
    assert done and not abort

    # q aborts
    sel, cur, done, abort = _menu_state(2, sel, 0, "q")
    assert abort and not done


def test_resolve_titles_none_and_spec():
    """resolve_titles: None -> None (rip all), spec -> parsed list."""
    from skive.scan import resolve_titles

    assert resolve_titles(None, []) is None
    assert resolve_titles("0,5-7", []) == [0, 5, 6, 7]


def test_resolve_titles_bd_skips_scrambled():
    """resolve_titles: no -T on Blu-ray drops scrambled titles; DVD rips all."""
    from skive.scan import resolve_titles, TitleInfo

    titles = [TitleInfo(0, 100, 1, "", "", False), TitleInfo(1, 100, 1, "", "", True)]
    assert resolve_titles(None, titles, "BD-ROM") == [0]
    assert resolve_titles(None, titles, "DVD-ROM") is None
    # all clean on BD -> None (rip all, single makemkvcon run)
    clean = [TitleInfo(0, 100, 1, "", "", False)]
    assert resolve_titles(None, clean, "BD-ROM") is None


def test_resolve_titles_bd_dedupes_playlist_obfuscation():
    """resolve_titles: BD titles with identical duration are duplicates;
    keep the one with the most subtitle languages."""
    from skive.scan import resolve_titles, TitleInfo

    titles = [
        TitleInfo(1, 6835, 22_150_429_318, "eng", "eng,dan", False),
        TitleInfo(2, 6835, 21_592_789_440, "eng", "eng", False),
        TitleInfo(3, 500, 349_207_267, "eng", "eng", False),
    ]
    assert resolve_titles(None, titles, "BD-ROM") == [1, 3]
    assert resolve_titles(None, titles, "DVD-ROM") is None


def test_resolve_titles_menu():
    """resolve_titles: MENU opens the menu over the already-scanned titles."""
    from skive.scan import resolve_titles, MENU, TitleInfo

    titles = [TitleInfo(0, 100, 1, "", "", False)]
    with patch("skive.scan.title_menu", return_value=[0]) as mock_menu:
        assert resolve_titles(MENU, titles) == [0]
        mock_menu.assert_called_once_with(titles)


def test_render_menu_lines():
    """_render returns a header, one line per title, and a hint."""
    from skive.scan import _render, TitleInfo

    titles = [
        TitleInfo(2, 5203, 5_845_346_392, "dan", "dan,eng", False),
        TitleInfo(1, 213, 169_400_000, "", "", True),
    ]
    lines = _render(titles, [True, True], 0)
    assert lines[0].strip() == "> Rip all titles"
    assert any("title 2" in line for line in lines)
    assert any("title 1" in line for line in lines)
    assert any("S" in line for line in lines)
    assert any("space=toggle" in line for line in lines)


def test_render_menu_tty_uses_shared_rounded_table():
    """On a TTY the title overview is the shared rounded table with a header
    row; the cursor, checkboxes, title numbers and key hint stay. Plain keeps
    the bare headerless grid."""
    import re
    from skive import ui
    from skive.scan import _render, TitleInfo

    titles = [TitleInfo(2, 5203, 5_845_346_392, "dan", "dan,eng", False)]
    plain = _render(titles, [False], 1)
    assert not any("\u256d" in line or "length" in line for line in plain)
    ui.set_view(ui.RunView(tty=True, live=False))
    try:
        lines = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in _render(titles, [False], 1)]
    finally:
        ui.clear_view()
    assert lines[0].strip() == "Rip all titles"
    assert lines[1].startswith("\u256d")
    assert "title" in lines[2] and "length" in lines[2] and "subs" in lines[2]
    row = next(line for line in lines if "title 2" in line)
    assert row.startswith("\u2502 >") and "[ ]" in row
    assert any("space=toggle" in line for line in lines)


def test_scan_disc_bails_when_no_titles(tmp_path, monkeypatch, capsys):
    """A disc with media but zero parseable titles aborts instead of ripping blind."""
    import pytest
    from skive.scan import scan_disc

    output = 'DRV:0,2,999,12,"BD-RE ASUS","ROGUE_ONE","/dev/sr0"\nMSG:3307,0,2,"File 00800.mpls was added as title #1","",""\n'
    monkeypatch.setattr("skive.scan.tempfile.gettempdir", lambda: str(tmp_path))
    with pytest.raises(SystemExit):
        scan_disc(120, run=Mock(return_value=Mock(stdout=output)))
    assert "no titles" in capsys.readouterr().err
    assert (tmp_path / "skive-scan-failed.txt").exists()


def test_disc_n_threads_through_scan_and_rip(tmp_path):
    """disc=1 uses disc:1 in commands and parses the DRV:1 line."""
    from skive.makemkv import rip_titles
    from skive.scan import scan_disc

    output = '''DRV:0,256,999,0,"","",""
DRV:1,2,999,12,"BD-ROM DRIVE","MOVIE","/dev/sr1"
TINFO:0,9,0,"1:26:43"
TINFO:0,11,0,"5845346392"
'''
    mock_run = Mock(return_value=Mock(stdout=output))
    media, label, titles = scan_disc(120, run=mock_run, disc=1)
    assert media == "BD-ROM"
    assert label == "MOVIE"
    assert len(titles) == 1
    assert "disc:1" in mock_run.call_args[0][0]

    rip_run = Mock(return_value=Mock(returncode=0))
    rip_titles(None, Path("/tmp/p.xml"), 120, tmp_path, run=rip_run, disc=1)
    assert "disc:1" in rip_run.call_args[0][0]


def test_scan_disc_label_cleaning():
    """The DRV volume label becomes a cleaned search prefill."""
    from skive.scan import scan_disc

    output = 'DRV:0,2,999,12,"BD-RE ASUS","ROGUE__ONE_  A ","x"\nTINFO:0,9,0,"1:26:43"\nTINFO:0,11,0,"5845346392"\n'
    _, label, titles = scan_disc(120, run=Mock(return_value=Mock(stdout=output)))
    assert label == "ROGUE ONE A"
    assert len(titles) == 1


def test_scan_disc_label_absent():
    """No second DRV field means no prefill."""
    from skive.scan import scan_disc

    output = 'DRV:0,2,0,0,"DVD-ROM hp CDDVDW TS-L633N"\nTINFO:0,9,"0:21:00"\nTINFO:0,11,"1234567890"\n'
    _, label, _titles = scan_disc(120, run=Mock(return_value=Mock(stdout=output)))
    assert label == ""


def test_scan_disc_label_strips_set_marker():
    """Trailing DISC/SIDE markers are packaging, not title."""
    from skive.scan import scan_disc

    for raw, want in [
        ("GENERATION_KILL_DISC1", "GENERATION KILL"),
        ("SHOW DISC 2 OF 3", "SHOW"),
        ("MOVIE_SIDE_A", "MOVIE"),
        ("DISC1", ""),
    ]:
        output = f'DRV:0,2,999,12,"BD-RE ASUS","{raw}","x"\nTINFO:0,9,0,"1:26:43"\nTINFO:0,11,0,"5845346392"\n'
        _, label, _titles = scan_disc(120, run=Mock(return_value=Mock(stdout=output)))
        assert label == want, raw


def test_scan_disc():
    """One info run returns the media token + parsed titles."""
    output = """
DRV:0,2,0,0,"DVD-ROM hp CDDVDW TS-L633N"
TINFO:0,9,"0:21:00"
TINFO:0,11,"1234567890"
TINFO:1,9,"0:22:30"
TINFO:1,11,"2345678901"
TINFO:2,9,"0:15:00"
TINFO:2,11,"3456789012"
"""

    mock_run = Mock(return_value=Mock(stdout=output))

    media, _label, titles = scan_disc(120, run=mock_run)

    assert media == "DVD-ROM"
    # titles sorted by number, with seconds and size
    assert len(titles) == 3
    assert titles[0].number == 0
    assert titles[0].seconds == 21 * 60  # 21:00
    assert titles[0].size == 1234567890
    assert titles[1].number == 1
    assert titles[1].seconds == 22 * 60 + 30  # 22:30
    assert titles[2].number == 2
    assert titles[2].seconds == 15 * 60


def test_scan_disc_makemkv_info_override():
    """Honor MAKEMKV_INFO env var."""
    output = """
DRV:0,2,0,0,"DVD-ROM"
TINFO:0,9,"0:21:00"
TINFO:0,11,"1234567890"
"""
    mock_run = Mock(return_value=Mock(stdout=output))

    with patch.dict(os.environ, {"MAKEMKV_INFO": "echo test"}):
        media, _label, titles = scan_disc(120, run=mock_run)

    assert media == "DVD-ROM"
    assert len(titles) == 1


def test_scan_disc_persists_scan_dump(tmp_path):
    """With a log, scan_disc writes its verbatim output to
    <root>/logs/scan/<run>_<label>.txt — written on the success path too,
    before anyone knows a run will go wrong."""
    from skive.common import RipLog

    output = 'DRV:0,2,0,0,"DVD-ROM drive","G1_70736D1","/dev/sr0"\nTINFO:0,9,"0:21:00"\nTINFO:0,11,"1234567890"\n'
    log = RipLog("skive rip tv", "makemkvcon", root=tmp_path)

    media, _label, titles = scan_disc(120, run=Mock(return_value=Mock(stdout=output)), log=log)

    assert (media, len(titles)) == ("DVD-ROM", 1)
    # _parse_label cleans G1_70736D1 -> "G1 70736D1" (underscores to spaces)
    dump = tmp_path / "logs" / "scan" / f"{log.run}_G1 70736D1.txt"
    assert dump.read_text(encoding="utf-8") == output


def test_scan_disc_dump_before_zero_titles_bail(tmp_path, capsys):
    """The scan dump lands before the zero-titles refusal — a scan that
    parses nothing still leaves its evidence in logs/scan/, not just the
    /tmp dump."""
    from skive.common import RipLog

    output = 'DRV:0,2,0,0,"DVD-ROM drive","BAD_DISC","/dev/sr0"\nTCOUNT:0\n'
    log = RipLog("skive rip tv", "makemkvcon", root=tmp_path)

    with pytest.raises(SystemExit):
        scan_disc(120, run=Mock(return_value=Mock(stdout=output)), log=log)

    assert (tmp_path / "logs" / "scan" / f"{log.run}_BAD DISC.txt").exists()


def test_scan_disc_without_log_writes_nothing(tmp_path):
    """No log -> no dump, no crash: library/test callers stay side-effect free."""
    output = 'DRV:0,2,0,0,"DVD-ROM drive","G1_70736D1","/dev/sr0"\nTINFO:0,9,"0:21:00"\nTINFO:0,11,"1234567890"\n'

    media, _label, titles = scan_disc(120, run=Mock(return_value=Mock(stdout=output)))

    assert (media, len(titles)) == ("DVD-ROM", 1)
    assert not (tmp_path / "logs").exists()


# --- DVD fast scan (ADR 0002, DVD half) ---
LSDVD_GENERATION_KILL = """Disc Title: GENERATION_KILL
Title: 01, Length: 01:05:32.000 Chapters: 6, Cells: 10, Audio streams: 02, Subpictures: 04
Title: 02, Length: 01:04:57.000 Chapters: 6, Cells: 10, Audio streams: 02, Subpictures: 04
Title: 03, Length: 01:00:53.000 Chapters: 6, Cells: 10, Audio streams: 02, Subpictures: 04
Title: 04, Length: 03:11:22.000 Chapters: 18, Cells: 34, Audio streams: 02, Subpictures: 04
"""


def test_parse_lsdvd_generation_kill():
    """lsdvd's default output parses to durations in seconds, title order."""
    from skive.scan import parse_lsdvd

    assert parse_lsdvd(LSDVD_GENERATION_KILL) == [3932, 3897, 3653, 11482]


def test_parse_lsdvd_garbage():
    """No Title/Length lines -> no durations."""
    from skive.scan import parse_lsdvd

    assert parse_lsdvd("not lsdvd output\nat all\n") == []


def test_parse_lsdvd_missing_disc_title_and_fractional_seconds():
    """The Disc Title header is optional and fractional seconds round to int."""
    from skive.scan import parse_lsdvd

    out = ("Title: 1, Length: 0:21:00.750 Chapters: 1\n"
           "Title: 2, Length: 00:30:15.000\n")
    assert parse_lsdvd(out) == [1261, 1815]


def test_parse_device_third_quoted_field():
    """_parse_device returns the DRV line's third quoted field."""
    from skive.scan import _parse_device

    drv = 'DRV:0,2,999,12,"DVD-ROM PIONEER BD-RW BDR-209","LABEL","/dev/sr0"'
    assert _parse_device(drv) == "/dev/sr0"
    assert _parse_device(drv, disc=1) == ""  # another disc's line does not match


def test_parse_device_absent_or_empty_is_blank():
    """A missing device field, an empty-drive line, or no DRV line -> ""."""
    from skive.scan import _parse_device

    assert _parse_device('DRV:0,2,999,12,"DVD-ROM","LABEL",""') == ""
    assert _parse_device('DRV:0,256,999,0,"","",""') == ""
    assert _parse_device("") == ""


def _popen_recorder(lines):
    """Fake Popen: yields lines from stdout, then refuses further reads (a live
    process would block) — proving sniff stops at the DRV line and kills."""
    created = []

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
            self.kwargs = kwargs
            self._it = iter(lines)
            self.stdout = self
            self.killed = False
            self.waited = False

        def __iter__(self):
            return self

        def __next__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise AssertionError("sniff kept reading past the DRV line — kill missing?")

        def kill(self):
            self.killed = True

        def wait(self):
            self.waited = True
            return 0

    def fake_popen(cmd, **kwargs):
        proc = FakeProc(cmd, **kwargs)
        created.append(proc)
        return proc

    return fake_popen, created


def test_sniff_disc_kills_after_drv_line():
    """sniff stops at this disc's DRV line: media+label+device parsed, killed."""
    from skive.scan import sniff_disc

    lines = [
        'DRV:0,256,999,0,"","",""\n',
        'DRV:1,2,999,12,"DVD-ROM hp CDDVDW","GENERATION_KILL_DISC1","/dev/sr1"\n',
    ]
    fake_popen, created = _popen_recorder(lines)

    media, label, device = sniff_disc(run=fake_popen, disc=1)

    assert (media, label, device) == ("DVD-ROM", "GENERATION KILL", "/dev/sr1")
    assert created[0].cmd == ["makemkvcon", "-r", "info", "disc:1"]
    assert created[0].killed and created[0].waited


def test_sniff_disc_no_disc():
    """An empty-drive DRV line parses to (\"\", \"\", \"\")."""
    from skive.scan import sniff_disc

    fake_popen, created = _popen_recorder(['DRV:0,256,999,0,"","",""\n'])

    assert sniff_disc(run=fake_popen, disc=0) == ("", "", "")
    assert created[0].killed


def test_sniff_disc_env_override_replays_without_popen():
    """MAKEMKV_INFO replays via shell; the spawner (Popen) is never used."""
    from skive.scan import sniff_disc

    drv = 'DRV:0,2,999,12,"BD-ROM Pioneer","MOVIE","/dev/sr0"'

    def fail(*args, **kwargs):
        raise AssertionError("Popen must not be spawned when MAKEMKV_INFO is set")

    with patch.dict("os.environ", {"MAKEMKV_INFO": f"printf '%s\\n' '{drv}'"}):
        assert sniff_disc(run=fail) == ("BD-ROM", "MOVIE", "/dev/sr0")


def test_fast_scan_dvd_command_and_result():
    """No mount, no sudo: one lsdvd call straight on the device."""
    from skive.scan import fast_scan_dvd

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Mock(returncode=0, stdout=LSDVD_GENERATION_KILL)

    assert fast_scan_dvd(0, "/dev/sr0", run=fake_run) == [3932, 3897, 3653, 11482]
    assert calls[0][0] == ["lsdvd", "/dev/sr0"]
    assert calls[0][1] == {"capture_output": True, "text": True}


def test_fast_scan_dvd_scans_given_device_not_dev_sr_disc():
    """lsdvd addresses the GIVEN device — the DRV line's path, or the
    disc:N -> /dev/srN fallback the caller derives when the DRV line lacks
    one — never a mountpoint keyed by disc number."""
    from skive.scan import fast_scan_dvd

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Mock(returncode=0, stdout=LSDVD_GENERATION_KILL)

    assert fast_scan_dvd(7, "/dev/sr1", run=fake_run) == [3932, 3897, 3653, 11482]
    assert calls == [["lsdvd", "/dev/sr1"]]


def test_fast_scan_dvd_nonzero_rc_raises():
    """A failed lsdvd raises for the caller's full-scan fallback."""
    from skive.scan import fast_scan_dvd

    fake_run = Mock(return_value=Mock(returncode=1, stdout="", stderr="ioctl failed"))
    with pytest.raises(RuntimeError, match="lsdvd failed"):
        fast_scan_dvd(0, "/dev/sr0", run=fake_run)


def test_fast_scan_dvd_missing_lsdvd_raises():
    """FileNotFoundError (no lsdvd installed) becomes the caller's RuntimeError."""
    from skive.scan import fast_scan_dvd

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    with pytest.raises(RuntimeError, match="lsdvd unavailable"):
        fast_scan_dvd(0, "/dev/sr0", run=fake_run)


def test_fast_scan_dvd_unparsable_output_raises():
    """rc 0 with no parseable titles raises — nothing to gate the rip on."""
    from skive.scan import fast_scan_dvd

    fake_run = Mock(return_value=Mock(returncode=0, stdout="Disc Title: EMPTY\n"))
    with pytest.raises(RuntimeError, match="no titles"):
        fast_scan_dvd(0, "/dev/sr0", run=fake_run)


# --- Blu-ray fast scan (ADR 0002, BD half) ---
FFPROBE_BLURAY = """[bluray @ 0x55d1c2a4f6c0] 4 playable titles found
[bluray @ 0x55d1c2a4f6c0] title 0 duration: 01:05:32.00
[bluray @ 0x55d1c2a4f6c0] title 1 duration: 01:04:57.00
[bluray @ 0x55d1c2a4f6c0] title 2 duration: 01:00:53.00
[bluray @ 0x55d1c2a4f6c0] title 3 duration: 03:11:22.00
"""


def test_parse_ffprobe_bluray_captured_shape():
    """The captured ffprobe bluray: listing parses to durations in seconds."""
    from skive.scan import parse_ffprobe_bluray

    assert parse_ffprobe_bluray(FFPROBE_BLURAY) == [3932, 3897, 3653, 11482]


def test_parse_ffprobe_bluray_bare_durations_keep_listed_order():
    """Builds without the `title N` prefix parse positionally."""
    from skive.scan import parse_ffprobe_bluray

    out = """[bluray @ 0x55d1c2a4f6c0] 4 playable titles found
[bluray @ 0x55d1c2a4f6c0] duration: 01:05:32.00
[bluray @ 0x55d1c2a4f6c0] duration: 01:04:57.00
[bluray @ 0x55d1c2a4f6c0] duration: 01:00:53.00
[bluray @ 0x55d1c2a4f6c0] duration: 03:11:22.00
"""
    assert parse_ffprobe_bluray(out) == [3932, 3897, 3653, 11482]


def test_parse_ffprobe_bluray_sorts_by_title_number():
    """When `title N` is present, output order follows N, not print order."""
    from skive.scan import parse_ffprobe_bluray

    out = ("[bluray @ 0x55d1c2a4f6c0] title 2 duration: 01:00:53.00\n"
           "[bluray @ 0x55d1c2a4f6c0] title 0 duration: 01:05:32.00\n"
           "[bluray @ 0x55d1c2a4f6c0] title 3 duration: 03:11:22.00\n"
           "[bluray @ 0x55d1c2a4f6c0] title 1 duration: 01:04:57.00\n")
    assert parse_ffprobe_bluray(out) == [3932, 3897, 3653, 11482]


def test_parse_ffprobe_bluray_garbage_ignored_including_duration_header():
    """Non-duration lines and ffprobe's capital-`Duration:` input probe are ignored."""
    from skive.scan import parse_ffprobe_bluray

    out = ("not bluray output at all\n"
           "[bluray @ 0x55d1c2a4f6c0] 4 playable titles found\n"
           "Duration: 00:05:00.04, start: 0.000000, bitrate: 9900 kb/s\n")
    assert parse_ffprobe_bluray(out) == []


def test_parse_ffprobe_bluray_fractional_seconds_round_to_int():
    """Optional fractional seconds round to int, prefix or not."""
    from skive.scan import parse_ffprobe_bluray

    titled = "[bluray @ 0x55d1c2a4f6c0] title 0 duration: 00:21:00.750\n"
    bare = "[bluray @ 0x55d1c2a4f6c0] duration: 00:21:00.750\n"
    assert parse_ffprobe_bluray(titled) == [1261]
    assert parse_ffprobe_bluray(bare) == [1261]


def test_fast_scan_bluray_command_and_result():
    """No mount, no sudo: one timeout-wrapped ffprobe call on the device."""
    from skive.scan import fast_scan_bluray

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        # Real ffprobe prints the listing via av_log, i.e. stderr.
        return Mock(returncode=0, stdout="", stderr=FFPROBE_BLURAY)

    assert fast_scan_bluray(0, "/dev/sr0", run=fake_run) == [3932, 3897, 3653, 11482]
    assert calls[0][0] == ["ffprobe", "-hide_banner", "bluray:/dev/sr0"]
    assert calls[0][1] == {"capture_output": True, "text": True, "timeout": 60}

    fast_scan_bluray(2, "/dev/sr2", run=fake_run, timeout=30)
    assert calls[1][0] == ["ffprobe", "-hide_banner", "bluray:/dev/sr2"]
    assert calls[1][1]["timeout"] == 30


def test_fast_scan_bluray_probes_given_device_not_dev_sr_disc():
    """ffprobe addresses the GIVEN device (the DRV line's path), never the
    disc:N -> /dev/srN guess — parallel drives probe their own disc."""
    from skive.scan import fast_scan_bluray

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Mock(returncode=0, stdout="", stderr=FFPROBE_BLURAY)

    assert fast_scan_bluray(7, "/dev/sr1", run=fake_run) == [3932, 3897, 3653, 11482]
    assert calls[0] == ["ffprobe", "-hide_banner", "bluray:/dev/sr1"]


def test_fast_scan_bluray_timeout_raises_runtime_error():
    """A hung listing (opening the longest playlist) raises, not blocks."""
    from skive.scan import fast_scan_bluray

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 60)

    with pytest.raises(RuntimeError, match="timed out"):
        fast_scan_bluray(0, "/dev/sr0", run=fake_run)


def test_fast_scan_bluray_nonzero_rc_raises():
    """A failed bluray probe raises for the caller's full-scan fallback."""
    from skive.scan import fast_scan_bluray

    fake_run = Mock(return_value=Mock(returncode=1, stdout="", stderr="AACS error"))
    with pytest.raises(RuntimeError, match="failed"):
        fast_scan_bluray(0, "/dev/sr0", run=fake_run)


def test_fast_scan_bluray_missing_ffprobe_raises():
    """FileNotFoundError (no ffprobe installed) becomes the caller's RuntimeError."""
    from skive.scan import fast_scan_bluray

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    with pytest.raises(RuntimeError, match="ffprobe unavailable"):
        fast_scan_bluray(0, "/dev/sr0", run=fake_run)


def test_fast_scan_bluray_empty_listing_raises():
    """rc 0 with no parseable titles raises — nothing to gate the rip on."""
    from skive.scan import fast_scan_bluray

    fake_run = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
    with pytest.raises(RuntimeError, match="no titles"):
        fast_scan_bluray(0, "/dev/sr0", run=fake_run)


def test_ambiguous_escalation_trigger():
    """ambiguous (ADR 0002): True when any two durations are within ±1s —
    duplicate/decoy/angle playlists the fast path must not rip by join."""
    from skive.scan import ambiguous

    assert ambiguous([100, 400, 100]) is True      # exact duplicate
    assert ambiguous([100, 101]) is True           # ±1s
    assert ambiguous([100, 102]) is False          # 2s apart is fine
    assert ambiguous([100]) is False               # single title
    assert ambiguous([]) is False                  # no titles
    assert ambiguous([100, 101, 500]) is True      # near-dup among many
