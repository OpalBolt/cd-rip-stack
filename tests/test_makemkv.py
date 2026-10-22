"""Tests for MakeMKV ripping: selection, profile injection, rip, renames, notes."""

import tempfile
from pathlib import Path
from unittest.mock import Mock

from skive.makemkv import (
    BASELINE, build_selection, inject_profile, purge_junk, rename_staged,
    rip_titles, looks_real, subtitle_note,
)


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


def test_promote_staged(tmp_path):
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


