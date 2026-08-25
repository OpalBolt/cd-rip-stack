"""Tests for MakeMKV functions."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, call, patch

from skive.makemkv import (
    BASELINE, build_selection, drive_media, inject_profile, is_recordable,
    iso2b, purge_junk, rename_staged, rip_titles, looks_real, subtitle_note,
)


def test_iso2b_mapping():
    """Test ISO 639-1 to 2/B mapping."""
    assert iso2b("en") == "eng"
    assert iso2b("da") == "dan"
    assert iso2b("fr") == "fra"
    assert iso2b("xx") == "xx"  # unknown passes through


def test_build_selection_empty():
    """Test build_selection with empty keep_lang."""
    sel = build_selection("")
    assert sel == BASELINE


def test_build_selection_origlang():
    """Test build_selection with original language."""
    sel = build_selection("jpn")
    assert sel.startswith("-sel:all,+sel:(jpn|dan|eng|nolang|single)")


def test_build_selection_dup_guard():
    """Test dup-guard when origlang is dan/eng."""
    sel = build_selection("dan")
    assert "dan|dan" not in sel  # not doubled
    # dan is present, eng is also present (from baseline)
    assert "dan" in sel and "eng" in sel


def test_inject_profile(tmp_path, monkeypatch):
    """Test profile injection replaces BASELINE outside the repo directory."""
    monkeypatch.chdir(tmp_path)
    new_sel = "-sel:all,+sel:(jpn|dan|eng|nolang|single),-sel:(havemulti|havecore),-sel:mvcvideo"
    tmp_profile = inject_profile(new_sel)

    content = tmp_profile.read_text()
    assert new_sel in content
    assert BASELINE not in content

    tmp_profile.unlink()


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

def test_scan_disc_parses_audio_subs_scrambled():
    """scan_disc parses audio counts, subtitle langs, and the S flag."""
    from skive.makemkv import scan_disc

    output = """
TINFO:0,9,0,"1:26:43"
TINFO:0,10,0,"5845346392"
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
TINFO:1,10,0,"169400000"
TINFO:1,26,0,"3,2,1"
"""

    _, result = scan_disc(120, run=Mock(return_value=Mock(stdout=output)))

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
    from skive.makemkv import _menu_state

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
    from skive.makemkv import resolve_titles

    assert resolve_titles(None, []) is None
    assert resolve_titles("0,5-7", []) == [0, 5, 6, 7]


def test_resolve_titles_bd_skips_scrambled():
    """resolve_titles: no -T on Blu-ray drops scrambled titles; DVD rips all."""
    from skive.makemkv import resolve_titles, TitleInfo

    titles = [TitleInfo(0, 100, 1, "", "", False), TitleInfo(1, 100, 1, "", "", True)]
    assert resolve_titles(None, titles, "BD-ROM") == [0]
    assert resolve_titles(None, titles, "DVD-ROM") is None
    # all clean on BD -> None (rip all, single makemkvcon run)
    clean = [TitleInfo(0, 100, 1, "", "", False)]
    assert resolve_titles(None, clean, "BD-ROM") is None


def test_resolve_titles_menu():
    """resolve_titles: MENU opens the menu over the already-scanned titles."""
    from skive.makemkv import resolve_titles, MENU, TitleInfo

    titles = [TitleInfo(0, 100, 1, "", "", False)]
    with patch("skive.makemkv.title_menu", return_value=[0]) as mock_menu:
        assert resolve_titles(MENU, titles) == [0]
        mock_menu.assert_called_once_with(titles)


def test_render_menu_lines():
    """_render returns a header, one line per title, and a hint."""
    from skive.makemkv import _render, TitleInfo

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
