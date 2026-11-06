"""Tests for rip_music.py — no network, no drive, no cyanrip, stub everything."""

import os
import subprocess
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from skive import common
from skive.rip_music import (
    _cyanrip_run,
    _newest_flac_album,
    _metaflac_tag,
    _write_cd_record,
    require_online,
    rip_music,
)


@pytest.fixture(autouse=True)
def _stage_root(tmp_path, monkeypatch):
    """Keep staging off the real /mnt/music."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))


class FakeArgs:
    """Minimal argparse.Namespace for rip_music."""
    def __init__(self, offset=None, cyanrip_flags=None):
        self.offset = offset
        self.cyanrip_flags = cyanrip_flags or []


def test_cyanrip_run_cmdline():
    """Test cyanrip command line includes offset and flags."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rip_dir = Path(tmpdir)
        process = Mock(stdout=iter(()))
        process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=process) as mock_popen:
            rc, captured = _cyanrip_run("100", ["-N"], rip_dir)

            cmd = mock_popen.call_args[0][0]
            assert cmd == ["cyanrip", "-s", "100", "-N"]
            assert rip_dir == mock_popen.call_args[1]["cwd"]
            assert mock_popen.call_args[1]["stderr"] == subprocess.STDOUT
            assert (rc, captured) == (0, "")


def test_cyanrip_run_quiet_at_default(capsys, monkeypatch):
    """Level 0: cyanrip output is captured but not streamed on success."""
    monkeypatch.setattr(common, "VERBOSITY", 0)
    with tempfile.TemporaryDirectory() as tmpdir:
        process = Mock(stdout=iter(["Ripping track 1\n", "Rip complete\n"]))
        process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=process):
            rc, captured = _cyanrip_run("100", [], Path(tmpdir))

        assert rc == 0
        assert captured == "Ripping track 1\nRip complete\n"
        assert capsys.readouterr().out == ""


def test_cyanrip_run_progress_streams_at_v1(capsys, monkeypatch):
    """Level 1: percent progress lines stream; other chatter stays hidden."""
    monkeypatch.setattr(common, "VERBOSITY", 1)
    lines = ["Track 1 of 12\n", "Ripping... 45%\n", "Rip complete\n"]
    with tempfile.TemporaryDirectory() as tmpdir:
        process = Mock(stdout=iter(lines))
        process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=process):
            rc, captured = _cyanrip_run("100", [], Path(tmpdir))

        assert rc == 0
        assert capsys.readouterr().out == "Ripping... 45%\n"
        assert captured == "".join(lines)  # capture is complete either way


def test_cyanrip_run_raw_passthrough_at_v2(capsys, monkeypatch):
    """Level 2: everything streams exactly as captured."""
    monkeypatch.setattr(common, "VERBOSITY", 2)
    with tempfile.TemporaryDirectory() as tmpdir:
        process = Mock(stdout=iter(["Ripping track 1\n", "Rip complete\n"]))
        process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=process):
            rc, captured = _cyanrip_run("100", [], Path(tmpdir))

        assert rc == 0
        assert capsys.readouterr().out == captured


def test_cyanrip_run_failure_replays_captured(capsys, monkeypatch):
    """Level 0 failure: the hidden lines replay verbatim to the console."""
    monkeypatch.setattr(common, "VERBOSITY", 0)
    with tempfile.TemporaryDirectory() as tmpdir:
        process = Mock(stdout=iter(["Ripping track 1\n", "Read error\n"]))
        process.wait.return_value = 1

        with patch("subprocess.Popen", return_value=process):
            rc, captured = _cyanrip_run("100", [], Path(tmpdir))

        assert rc == 1
        assert capsys.readouterr().out == captured


def test_multiple_releases_prompts(tmp_path):
    """Test 'Multiple releases found' prompts and re-runs with -R."""
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.RipLog") as MockLog, \
            patch("skive.rip_music._newest_flac_album", return_value=("-", "-")), \
            patch("skive.rip_music.ui.ask", return_value="2") as ask, \
            patch("skive.rip_music._cyanrip_run", side_effect=[
                (1, "Multiple releases found\n"),
                (0, "Rip complete\n"),
            ]) as mock_run:
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    ask.assert_called_once_with("Multiple MusicBrainz releases — pick one",
                                choices="Enter abort", default="")
    rip_dir = tmp_path / "rip" / "cd"
    log = MockLog.return_value  # the raw log rides along to _cyanrip_run
    assert mock_run.call_args_list == [
        (("100", ["-N"], rip_dir, log, {}),),
        (("100", ["-R", "2", "-N"], rip_dir, log,
          {"_opener_emitted": False}),),
    ]


def test_require_online_uses_stdlib_head_probe():
    """Test MusicBrainz connectivity uses urllib with a five-second timeout."""
    response = MagicMock()

    with patch("urllib.request.urlopen", return_value=response) as mock_urlopen:
        require_online()

    request = mock_urlopen.call_args.args[0]
    assert request.full_url == "https://musicbrainz.org/"
    assert request.get_method() == "HEAD"
    assert mock_urlopen.call_args.kwargs["timeout"] == 5


def test_require_online_accepts_http_error_response():
    """Test any MusicBrainz HTTP response counts as online."""
    error = urllib.error.HTTPError(
        "https://musicbrainz.org/", 503, "unavailable", {}, None
    )

    with patch("urllib.request.urlopen", side_effect=error):
        require_online()


def test_require_online_exits_on_network_failure(capsys):
    """Test MusicBrainz network failure exits as offline."""
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        with pytest.raises(SystemExit) as exc:
            require_online()

    assert exc.value.code == 1
    assert capsys.readouterr().err == "fail  MusicBrainz is unreachable\n"


def test_online_gate_skipped_with_minus_N():
    """Test online gate is skipped when -N in flags."""
    with tempfile.TemporaryDirectory() as tmpdir:
        args = FakeArgs(offset="100", cyanrip_flags=["-N"])

        with patch("skive.rip_music.require_online") as mock_require:
            with patch("skive.rip_music._cyanrip_run", return_value=(0, "")):
                with patch("skive.rip_music._newest_flac_album", return_value=("-", "-")):
                    try:
                        rip_music(args)
                    except SystemExit:
                        pass
                    mock_require.assert_not_called()


def test_online_gate_without_minus_N():
    """Test online gate probes MusicBrainz without -N."""
    with tempfile.TemporaryDirectory() as tmpdir:
        args = FakeArgs(offset="100")

        with patch("skive.rip_music.ensure_mounted"):
            with patch("skive.rip_music.require_online") as mock_require:
                with patch("skive.rip_music._cyanrip_run", return_value=(0, "")):
                    with patch("skive.rip_music._newest_flac_album", return_value=("-", "-")):
                        with patch("skive.rip_music.RipLog"):
                            try:
                                rip_music(args)
                            except SystemExit:
                                pass
                        mock_require.assert_called_once()


def test_cd_record_merges_newest_log_for_each_disc(tmp_path):
    folder = tmp_path / "Album [FLAC]"
    folder.mkdir()
    log = Mock()
    log.run = "rip-1"
    first_log = folder / "Album CD1.log"
    first_log.write_text("Disc number: 1\nOffset: +10 samples\n")
    (folder / "Album CD1.cue").write_text("cue")
    os.utime(first_log, (100, 100))
    _write_cd_record(tmp_path, folder.name, "10", log)

    log.run = "rip-2"
    second_log = folder / "Album CD2.log"
    second_log.write_text("Disc number: 2\nOffset: +20 samples\n")
    (folder / "Album CD2.cue").write_text("cue")
    os.utime(second_log, (200, 200))
    _write_cd_record(tmp_path, folder.name, "20", log)

    import json
    doc = json.loads((folder / "validation.json").read_text())
    assert set(doc["discs"]) == {"1", "2"}
    assert doc["discs"]["1"]["offset"] == 10
    assert doc["discs"]["2"]["offset"] == 20


def test_rip_name_from_tags():
    """Test rip name from newest FLAC's ALBUM/ARTIST tags."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rip_dir = Path(tmpdir)

        # Create FLAC files with different timestamps
        old_flac = rip_dir / "old.flac"
        new_flac = rip_dir / "new.flac"

        old_flac.touch()
        new_flac.touch()

        # Make newest actually newer
        import time
        time.sleep(0.1)
        new_flac.touch()

        # Mock metaflac to return tags
        def mock_metaflac(flac, tag):
            if flac == new_flac and tag == "ALBUM":
                return "TestAlbum"
            if flac == new_flac and tag == "ARTIST":
                return "TestArtist"
            return ""

        with patch("skive.rip_music._metaflac_tag", side_effect=mock_metaflac):
            name, rel_folder = _newest_flac_album(rip_dir)
            assert name == "TestArtist - TestAlbum"
            assert rel_folder == "."


def test_rip_name_no_flac():
    """Test rip name is '-' when no FLAC files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        name, rel_folder = _newest_flac_album(Path(tmpdir))
        assert name == "-"
        assert rel_folder == "-"


def test_rip_name_no_tags():
    """Test rip name is '-' when tags missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rip_dir = Path(tmpdir)
        (rip_dir / "test.flac").touch()

        with patch("skive.rip_music._metaflac_tag", return_value=""):
            name, rel_folder = _newest_flac_album(rip_dir)
            assert name == "-"
            assert rel_folder == "."


def test_rip_name_album_only():
    """Test rip name is just album when no ARTIST tag."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rip_dir = Path(tmpdir)
        (rip_dir / "test.flac").touch()

        def mock_metaflac(flac, tag):
            if tag == "ALBUM":
                return "TestAlbum"
            return ""

        with patch("skive.rip_music._metaflac_tag", side_effect=mock_metaflac):
            name, rel_folder = _newest_flac_album(rip_dir)
            assert name == "TestAlbum"
            assert rel_folder == "."


def test_rip_name_in_album_subfolder():
    """Test rip name and folder when FLAC is inside an album subfolder."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rip_dir = Path(tmpdir)
        album_dir = rip_dir / "Cool Album"
        album_dir.mkdir()
        flac = album_dir / "01.flac"
        flac.touch()

        def mock_metaflac(flac_path, tag):
            if tag == "ALBUM":
                return "Cool Album"
            if tag == "ARTIST":
                return "Cool Artist"
            return ""

        with patch("skive.rip_music._metaflac_tag", side_effect=mock_metaflac):
            name, rel_folder = _newest_flac_album(rip_dir)
            assert name == "Cool Artist - Cool Album"
            assert rel_folder == "Cool Album"


def test_rip_music_logs_under_music_mount_and_notes_folder(tmp_path):
    """Test RipLog writes under MUSIC_MOUNT and notes the relative folder."""
    import json

    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    rip_cd = tmp_path / "rip" / "cd"
    album_dir = rip_cd / "My Album"
    album_dir.mkdir(parents=True)
    flac = album_dir / "track.flac"
    flac.touch()

    def mock_metaflac(flac_path, tag):
        if tag == "ALBUM":
            return "My Album"
        if tag == "ARTIST":
            return "Artist"
        return ""

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
            patch("skive.rip_music._metaflac_tag", side_effect=mock_metaflac):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    log_file = tmp_path / "logs" / "rip.jsonl"
    assert log_file.exists()
    lines = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert lines[0]["status"] == "started"
    # RipLog note for folder
    note_lines = [l for l in lines if l.get("status") == "folder: My Album"]
    assert len(note_lines) == 1
    assert note_lines[0]["phase"] == "rip"
    # Phase rip ok
    phase_lines = [l for l in lines if l.get("phase") == "rip" and l.get("status") == "ok"]
    assert len(phase_lines) == 1


def test_metaflac_tag_parser():
    """Test metaflac tag parser."""
    with tempfile.TemporaryDirectory() as tmpdir:
        flac = Path(tmpdir) / "test.flac"
        flac.touch()

        def mock_run(cmd, **kwargs):
            result = Mock()
            if "--show-tag=ALBUM" in cmd:
                result.stdout = "ALBUM=MyAlbum\n"
            elif "--show-tag=ARTIST" in cmd:
                result.stdout = "ARTIST=MyArtist\n"
            else:
                result.stdout = ""
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=mock_run):
            album = _metaflac_tag(flac, "ALBUM")
            artist = _metaflac_tag(flac, "ARTIST")
            assert album == "MyAlbum"
            assert artist == "MyArtist"


# -- Run-log name lifecycle (album header pair, decision notes) ----------------

# Genuine column-0 header shapes, from the top of
# tests/fixtures/cyanrip/summertime-blues.log.
SUMMERTIME_HEADER = [
    "cyanrip 0.9.3 (523e132)\n",
    "Album:          Summertime Blues\n",
    "Album artist:   Various Artists\n",
    "Track 1 ripped and encoded successfully!\n",
]


def _rip_jsonl(tmp_path):
    import json

    return [json.loads(line)
            for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]


def test_header_pair_names_run_on_success(tmp_path):
    """The streamed Album/Album artist pair names the run mid-rip: every
    rip.jsonl line — started, the rip ok phase line included — carries
    'Various Artists - Summertime Blues', with no FLAC scan needed."""
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    process = Mock(stdout=iter(SUMMERTIME_HEADER))
    process.wait.return_value = 0

    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    lines = _rip_jsonl(tmp_path)
    assert lines[0]["status"] == "started"
    # 'started' printed before cyanrip ran; everything after the pair
    # streamed mid-rip carries the name, the rip ok phase line included.
    assert lines[0]["name"] == "-"
    assert all(line["name"] == "Various Artists - Summertime Blues"
               for line in lines[1:])
    ok = [line for line in lines if line["phase"] == "rip" and line["status"] == "ok"]
    assert ok and ok[0]["name"] == "Various Artists - Summertime Blues"


def test_header_pair_names_failed_run_on_total_line(tmp_path):
    """A failed run keeps the header pair's name: the FAIL total line carries
    it, and staging is never scanned for a failed run."""
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    process = Mock(stdout=iter(SUMMERTIME_HEADER))
    process.wait.return_value = 1

    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 1
    lines = _rip_jsonl(tmp_path)
    total = [line for line in lines if line["phase"] == "total"]
    assert total and total[0]["status"].startswith("FAIL")
    assert total[0]["name"] == "Various Artists - Summertime Blues"


def test_header_pair_names_interrupted_run(tmp_path, monkeypatch):
    """Ctrl+C after the pair streamed: the name set mid-rip persists through
    the interrupted total line."""
    import json

    monkeypatch.setattr("skive.rip_music.kill_child_after_grace", lambda proc: None)

    def gen():
        yield from SUMMERTIME_HEADER
        raise KeyboardInterrupt

    process = Mock(stdout=gen())
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 130
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    interrupted = [line for line in lines if line["status"] == "interrupted"]
    assert interrupted and interrupted[0]["name"] == "Various Artists - Summertime Blues"


def test_album_header_without_artist_success_uses_flac_tags(tmp_path):
    """Album header with no Album artist line: on success the FLAC ARTIST tag
    names the run, exactly as when no header streamed."""
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    album_dir = tmp_path / "rip" / "cd" / "My Album"
    album_dir.mkdir(parents=True)
    (album_dir / "track.flac").touch()
    process = Mock(stdout=iter(["Album:          My Album\n",
                                "Track 1 ripped and encoded successfully!\n"]))
    process.wait.return_value = 0

    def mock_metaflac(flac_path, tag):
        return {"ALBUM": "My Album", "ARTIST": "Artist"}.get(tag, "")

    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process), \
            patch("skive.rip_music._metaflac_tag", side_effect=mock_metaflac):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    ok = [line for line in _rip_jsonl(tmp_path)
          if line["phase"] == "rip" and line["status"] == "ok"]
    assert ok and ok[0]["name"] == "Artist - My Album"


def test_album_header_without_artist_failure_keeps_album_name(tmp_path):
    """The same album-only header on a failing run keeps that album's name —
    not the previous album's name from a staging scan."""
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    stale = tmp_path / "rip" / "cd" / "Old Album"
    stale.mkdir(parents=True)
    (stale / "old.flac").touch()
    process = Mock(stdout=iter(["Album:          My Album\n", "Read error\n"]))
    process.wait.return_value = 1

    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process), \
            patch("skive.rip_music._metaflac_tag", return_value="Old Artist"):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 1
    lines = _rip_jsonl(tmp_path)
    # The album-only header never sets the name mid-rip (success would fall
    # back to the FLAC tags); post-run the failed run keeps the header album.
    assert lines[0] == {**lines[0], "status": "started", "name": "-"}
    assert all(line["name"] == "My Album" for line in lines[1:])
    assert all("Old Album" not in line["status"] for line in lines)


def test_failed_run_without_header_inherits_nothing_from_staging(tmp_path):
    """Regression: a failed run with no header keeps name '-' and folder '-'
    even when another album's tagged FLACs sit in staging — the old
    always-scan bug let the previous album lend its name."""
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    stale = tmp_path / "rip" / "cd" / "Old Album"
    stale.mkdir(parents=True)
    (stale / "old.flac").touch()
    process = Mock(stdout=iter(["Ripping track 1\n", "Read error\n"]))
    process.wait.return_value = 1

    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process), \
            patch("skive.rip_music._metaflac_tag", return_value="Old Artist"):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 1
    lines = _rip_jsonl(tmp_path)
    assert all(line["name"] == "-" for line in lines)
    folder_notes = [line["status"] for line in lines
                    if line["status"].startswith("folder:")]
    assert folder_notes == ["folder: -"]


def test_release_pick_note_logged_before_rerun(tmp_path):
    """Answering the Multiple-releases prompt with 3 logs 'release pick: 3'
    before the -R re-run starts, and exactly once."""
    import json

    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    rip_jsonl = tmp_path / "logs" / "rip.jsonl"
    runs = []

    def fake_run(offset, flags, rip_dir, log, header):
        runs.append((list(flags),
                     rip_jsonl.read_text() if rip_jsonl.exists() else ""))
        if len(runs) == 1:
            return 1, "Multiple releases found\n"
        return 0, "Rip complete\n"

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music.ui.ask", return_value="3"), \
            patch("skive.rip_music._cyanrip_run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    assert [flags for flags, _ in runs] == [["-N"], ["-R", "3", "-N"]]
    assert "release pick: 3" not in runs[0][1]
    assert "release pick: 3" in runs[1][1]
    lines = [json.loads(line) for line in rip_jsonl.read_text().splitlines()]
    assert [line["status"] for line in lines].count("release pick: 3") == 1


def test_no_release_failure_notes_submit_url(tmp_path):
    """A no-release failure logs one note carrying the attach URL from that
    run's own captured output (fixture lines, nothing hard-coded)."""
    fixture = Path(__file__).parent / "fixtures" / "cyanrip" / "no-release.txt"
    out_lines = [line + "\n" for line in fixture.read_text().splitlines()]
    url = next(line for line in out_lines
               if line.startswith("https://musicbrainz.org/cdtoc/attach?")).strip()

    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    process = Mock(stdout=iter(out_lines))
    process.wait.return_value = 1

    with patch("skive.rip_music.ensure_mounted"), \
            patch("subprocess.Popen", return_value=process):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 1
    lines = _rip_jsonl(tmp_path)
    notes = [line["status"] for line in lines
             if "no MusicBrainz release" in line["status"]]
    assert notes == [f"no MusicBrainz release: submit this disc — {url}"]
    assert all(line["name"] == "-" for line in lines)


def test_repo_sweep_no_local_paths():
    """Test no ~/media/audio paths in skive/, config/, windows/ (not lib, not README)."""
    excluded = {".git", ".venv", "uv.lock", "tests/__pycache__", "skive/__pycache__"}
    repo_root = Path(__file__).parent.parent

    for root_dir in [repo_root / "skive", repo_root / "config", repo_root / "windows"]:
        if not root_dir.exists():
            continue
        for py_file in root_dir.rglob("*.py"):
            # Skip cache
            if any(excluded_part in str(py_file) for excluded_part in excluded):
                continue
            content = py_file.read_text()
            assert "~/media" not in content, f"~/media found in {py_file}"
            assert "media/audio" not in content, f"media/audio found in {py_file}"

# -- Interrupt (Ctrl+C) (CONTEXT.md: Interrupt) --------------------------------

def _stdout_then_interrupt(lines):
    """A fake cyanrip stdout that streams lines, then the interrupt lands."""
    def gen():
        yield from lines
        raise KeyboardInterrupt
    return gen()


def test_cyanrip_run_interrupt_kills_child_and_reraises(monkeypatch):
    """Ctrl+C during the cyanrip stream: the child goes through the
    forward/kill helper and the interrupt propagates to rip_music's handler."""
    killed = []
    monkeypatch.setattr("skive.rip_music.kill_child_after_grace",
                        lambda proc: killed.append(proc))
    process = Mock(stdout=_stdout_then_interrupt(["Ripping track 1\n"]))

    with patch("subprocess.Popen", return_value=process):
        with pytest.raises(KeyboardInterrupt):
            _cyanrip_run("100", [], Path("/tmp/rip"))

    assert killed == [process]


def test_rip_music_interrupt_records_interrupted_and_exits_130(tmp_path, monkeypatch, capsys):
    """Ctrl+C mid-rip: the run log's closing line says status=interrupted (the
    finally's finish stays silent), the raw log is kept, one line, exit 130.
    Music staging has no junk labels, so there is no partial purge here."""
    import json

    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path))
    args = FakeArgs(offset="100", cyanrip_flags=["-N"])

    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", side_effect=KeyboardInterrupt):
        with pytest.raises(SystemExit) as e:
            rip_music(args)

    assert e.value.code == 130
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert [line["status"] for line in lines] == ["started", "interrupted"]
    err = capsys.readouterr().err
    assert "fail  interrupted · 0 ok" in err


def test_clean_rip_writes_cd_record_from_cyanrip_log(tmp_path):
    """A clean fake rip leaves the CD proof beside its staged FLACs."""
    import json

    album = tmp_path / "rip" / "cd" / "Album"
    album.mkdir(parents=True)
    (album / "Album.log").write_text(
        "Offset:         +123 samples\n"
        "  Track 1\n"
        "    Accurip v1:  1234 (accurately ripped, confidence 7)\n",
        encoding="utf-8",
    )
    (album / "Album.cue").write_text("FILE Album.flac\n", encoding="utf-8")
    args = FakeArgs(offset="123", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
            patch("skive.rip_music._newest_flac_album", return_value=("Album", "Album")):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)
    assert exc.value.code == 0
    doc = json.loads((album / "validation.json").read_text(encoding="utf-8"))
    assert doc["source"] == "cd"
    assert doc["discs"]["1"]["offset"] == 123
    assert doc["discs"]["1"]["accuraterip"][0]["status"] == "accurate"


def test_unparsed_cyanrip_log_keeps_record_and_raw_note(tmp_path, capsys):
    import json

    album = tmp_path / "rip" / "cd" / "Album"
    album.mkdir(parents=True)
    (album / "Album.log").write_text("unexpected cyanrip output\nfirst detail\n",
                                     encoding="utf-8")
    (album / "Album.cue").write_text("FILE Album.flac\n", encoding="utf-8")
    args = FakeArgs(offset="123", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
            patch("skive.rip_music._newest_flac_album", return_value=("Album", "Album")):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)
    assert exc.value.code == 0
    doc = json.loads((album / "validation.json").read_text(encoding="utf-8"))
    assert "accuraterip" not in doc["discs"]["1"]
    lines = (tmp_path / "logs" / "rip.jsonl").read_text(encoding="utf-8").splitlines()
    assert any("accuraterip: unparsed" in json.loads(line)["status"] for line in lines)
    run = json.loads(lines[0])["run"]
    raw = tmp_path / "logs" / "raw" / f"{run}.jsonl"
    assert raw.exists()
    assert "unexpected cyanrip output" in raw.read_text(encoding="utf-8")


# -- Born-complete staged record and validation note ----------------------------


def _fixture_report() -> str:
    """The real cyanrip report (spec: genuine output, not guesses)."""
    return (Path(__file__).parent / "fixtures" / "cyanrip"
            / "summertime-blues.log").read_text(encoding="utf-8")


def _rip_statuses(tmp_path):
    import json

    return [json.loads(line)["status"]
            for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]


def test_clean_rip_writes_born_complete_record_from_real_report(tmp_path):
    """A clean rip with the real report shape leaves a born-complete staged
    record: the parsed release, 19 tracks (file/dur_s/format) built from the
    staged FLACs, crc32 on every Accurip entry, and the validation note in
    the run log."""
    import json

    folder_name = "Summertime Blues [FLAC]"
    album = tmp_path / "rip" / "cd" / folder_name
    album.mkdir(parents=True)
    report = _fixture_report()
    (album / "Summertime Blues.log").write_text(report, encoding="utf-8")
    (album / "Summertime Blues.cue").write_text("FILE image.flac\n", encoding="utf-8")
    flac_names = [Path(line.strip()).name for line in report.splitlines()
                  if line.strip().endswith(".flac")]
    for name in flac_names:
        (album / name).touch()

    args = FakeArgs(offset="6", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
            patch("skive.rip_music._newest_flac_album",
                  return_value=("Various Artists - Summertime Blues", folder_name)):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    doc = json.loads((album / "validation.json").read_text(encoding="utf-8"))
    assert doc["release"] == "0ba5b04b-db78-46cd-95e9-9fcf1945fa23"
    assert len(doc["tracks"]) == 19
    assert all(set(track) == {"file", "dur_s", "format"} for track in doc["tracks"])
    assert doc["tracks"][0] == {
        "file": "01 - Damn Right, I've Got the Blues.flac",
        "dur_s": 269,
        "format": "flac",
    }
    accuraterip = doc["discs"]["1"]["accuraterip"]
    assert all(entry["crc32"] for entry in accuraterip)
    assert accuraterip[0]["crc32"] == "6C421A41"
    assert "validation: 19 tracks, 19 accurate (conf 3)" in _rip_statuses(tmp_path)


def test_validation_note_extends_not_found_and_partial(tmp_path):
    """A report with unmatched tracks extends the note: the not_found and
    partial counts appear only when nonzero, after the accurate count."""
    album = tmp_path / "rip" / "cd" / "Album"
    album.mkdir(parents=True)
    (album / "Album.log").write_text(
        "Offset:         +123 samples\n"
        "  Track 1\n"
        "    Accurip v1:  1234 (accurately ripped, confidence 7)\n"
        "  Track 2\n"
        "    Accurip v1:  5678 (not found)\n"
        "  Track 3\n"
        "    Accurip v1:  9abc (partially accurately ripped, confidence 2)\n",
        encoding="utf-8",
    )
    (album / "Album.cue").write_text("FILE Album.flac\n", encoding="utf-8")
    for name in ("01 - One.flac", "02 - Two.flac", "03 - Three.flac"):
        (album / name).touch()

    args = FakeArgs(offset="123", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
            patch("skive.rip_music._newest_flac_album", return_value=("Album", "Album")):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    assert ("validation: 3 tracks, 1 accurate (conf 7), 1 not_found, 1 partial"
            in _rip_statuses(tmp_path))


def test_offline_minus_N_record_lists_files_without_report_facts(tmp_path):
    """Offline -N: the report parses no release and no AccurateRip, yet the
    record is still born from what's on disk: release null, the folder's
    FLACs as tracks with null durations, and no validation note (the
    accuraterip: unparsed note covers that case)."""
    import json

    album = tmp_path / "rip" / "cd" / "Album"
    album.mkdir(parents=True)
    (album / "Album.log").write_text(
        "Offset:         +6 samples\n"
        "Album:          Unknown Album\n",
        encoding="utf-8",
    )
    (album / "Album.cue").write_text("FILE Album.flac\n", encoding="utf-8")
    (album / "01 - One.flac").touch()
    (album / "02 - Two.flac").touch()

    args = FakeArgs(offset="6", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
            patch("skive.rip_music._newest_flac_album", return_value=("Album", "Album")):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    doc = json.loads((album / "validation.json").read_text(encoding="utf-8"))
    assert doc["release"] is None
    assert doc["tracks"] == [
        {"file": "01 - One.flac", "dur_s": None, "format": "flac"},
        {"file": "02 - Two.flac", "dur_s": None, "format": "flac"},
    ]
    assert "accuraterip" not in doc["discs"]["1"]
    statuses = _rip_statuses(tmp_path)
    assert not any(status.startswith("validation:") for status in statuses)
    assert "accuraterip: unparsed" in statuses


def test_interrupted_run_writes_no_record(tmp_path):
    """Interrupt contract: the CD record is written only on rc == 0 — an
    interrupted rip leaves no validation.json beside the staged FLACs."""
    album = tmp_path / "rip" / "cd" / "Album"
    album.mkdir(parents=True)
    (album / "Album.log").write_text("Offset:         +123 samples\n", encoding="utf-8")
    (album / "Album.cue").write_text("FILE Album.flac\n", encoding="utf-8")
    (album / "01 - One.flac").touch()

    args = FakeArgs(offset="123", cyanrip_flags=["-N"])
    with patch("skive.rip_music.ensure_mounted"), \
            patch("skive.rip_music._cyanrip_run", side_effect=KeyboardInterrupt):
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 130
    assert not list((tmp_path / "rip" / "cd").rglob("validation.json"))


def test_tracks_pair_positionally_without_number_prefix(tmp_path):
    """A staged file with no leading 'NN - ' number pairs to its parsed
    track by position in the sorted file list."""
    import json

    album = tmp_path / "Album"
    album.mkdir()
    (album / "Album.log").write_text(
        "Offset: +10 samples\n"
        "  Track 1\n"
        "    Duration:    00:01:10\n"
        "    Accurip v1:  AAAA (accurately ripped, confidence 1)\n"
        "  Track 2\n"
        "    Duration:    00:02:20\n"
        "    Accurip v1:  BBBB (accurately ripped, confidence 1)\n",
        encoding="utf-8",
    )
    (album / "side a.flac").touch()
    (album / "side b.flac").touch()
    log = Mock()
    log.run = "rip-1"

    _write_cd_record(tmp_path, "Album", "10", log)

    doc = json.loads((album / "validation.json").read_text(encoding="utf-8"))
    assert [track["dur_s"] for track in doc["tracks"]] == [70, 140]


def test_rerip_record_rebuilds_from_newest_log(tmp_path):
    """-R re-rip: cyanrip appends per-rip logs to the album folder; each run
    rebuilds the disc entry from the newest log by mtime."""
    import json

    folder = tmp_path / "Album"
    folder.mkdir()
    old_log = folder / "Album.log"
    old_log.write_text(
        "Disc number: 1\nOffset: +10 samples\n"
        "  Track 1\n"
        "    EAC CRC32:   AAAAAAAA\n"
        "    Accurip v1:  CCCCCCCC (accurately ripped, confidence 2)\n",
        encoding="utf-8",
    )
    (folder / "Album.cue").write_text("cue\n", encoding="utf-8")
    os.utime(old_log, (100, 100))
    log = Mock()
    log.run = "rip-1"
    _write_cd_record(tmp_path, "Album", "10", log)

    new_log = folder / "Album CD1.log"
    new_log.write_text(
        "Disc number: 1\nOffset: +10 samples\n"
        "  Track 1\n"
        "    EAC CRC32:   BBBBBBBB\n"
        "    Accurip v1:  DDDDDDDD (accurately ripped, confidence 3)\n",
        encoding="utf-8",
    )
    os.utime(new_log, (200, 200))
    log.run = "rip-2"
    _write_cd_record(tmp_path, "Album", "10", log)

    doc = json.loads((folder / "validation.json").read_text(encoding="utf-8"))
    assert doc["discs"]["1"]["accuraterip"][0]["crc32"] == "BBBBBBBB"


# --- Run view wiring (slice 08: opener, headings, footer, summary) ------------


def test_cyanrip_track_heading_and_footer_plain(monkeypatch, capsys):
    """The shared run view keeps music track headings readable when piped."""
    from skive import ui

    monkeypatch.setattr(common, "VERBOSITY", 0)
    view = ui.RunView(tty=False, live=False)
    ui.set_view(view)
    process = Mock(stdout=iter([
        "Disc tracks: 2\n",
        "Track 1 ripped and encoded successfully!\n",
        "title: First song\n",
        "Track 2 ripped and encoded successfully!\n",
        "title: Second song\n",
    ]))
    process.wait.return_value = 0
    try:
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            rc, _ = _cyanrip_run("+6", [], Path(tmpdir))
    finally:
        ui.clear_view()

    assert rc == 0
    err = capsys.readouterr().err
    assert "info  1/2  First song" in err
    assert "info  2/2  Second song" in err
    assert "\\x1b" not in err


def test_cyanrip_run_tool_lines_and_footer_at_v1(monkeypatch, capsys):
    """With a seam view set at -v on a TTY: a % line feeds the footer's
    progress but stays out of scrollback, and other lines keep their tier."""
    from skive import ui

    monkeypatch.setattr(common, "VERBOSITY", 1)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        process = Mock(stdout=iter(["Track 1 of 12\n", "Ripping... 45%\n",
                                    "Track 1 ripped and encoded successfully!\n"]))
        process.wait.return_value = 0
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            rc, captured = _cyanrip_run("100", [], Path(tmpdir))
    finally:
        ui.clear_view()

    assert rc == 0
    rendered = view.render()
    err = capsys.readouterr().err
    assert "Ripping... 45%" not in err  # TTY progress never enters scrollback
    assert "cyanrip  Track 1 ripped and encoded successfully!" not in err  # tier unchanged
    assert "100%" in rendered


def test_album_name_lands_in_status_via_header_watch(tmp_path):
    """The header pair renames the status panel mid-rip: 'ripping -' before,
    the real name once Album + Album artist have both streamed."""
    from skive import ui

    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    process = Mock(stdout=iter(SUMMERTIME_HEADER))
    process.wait.return_value = 0

    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch("skive.rip_music.ensure_mounted"), \
                patch("subprocess.Popen", return_value=process):
            with pytest.raises(SystemExit) as exc:
                rip_music(args)
    finally:
        ui.clear_view()

    assert exc.value.code == 0
    rendered = view.render()
    assert "track 1/1" in rendered


def test_album_opener_and_track_footer_use_shared_run_view(tmp_path, capsys):
    """A named album opens the run before track headings, and the footer
    carries the track position, title, offset, bar, and percent."""
    from skive import ui

    args = FakeArgs(offset="+6", cyanrip_flags=["-N"])
    process = Mock(stdout=iter([
        "Disc tracks: 2\n",
        "Album:          Test Album\n",
        "Album artist:   Test Artist\n",
        "Track 1 ripped and encoded successfully!\n",
        "title: First song\n",
        "Track 2 ripped and encoded successfully!\n",
        "title: Second song\n",
    ]))
    process.wait.return_value = 0
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch("skive.rip_music.ensure_mounted"), \
                patch("subprocess.Popen", return_value=process), \
                patch("skive.rip_music._newest_flac_album", return_value=("-", "-")):
            with pytest.raises(SystemExit) as exc:
                rip_music(args)
    finally:
        ui.clear_view()

    assert exc.value.code == 0
    err = capsys.readouterr().err
    opener = "skive rip music · Test Artist - Test Album · offset +6"
    assert opener in err
    assert err.index(opener) < err.index("▸ 1/2  First song")
    footer = view.render()
    assert "track 2/2" in footer
    assert "Second song" in footer
    assert "offset +6" in footer
    assert "100%" in footer


def test_hidden_replay_lands_tool_prefixed_under_a_view(monkeypatch, capsys):
    """Quiet failure under a view: the hidden replay set is unchanged and
    replays through the tool prefix."""
    from skive import ui

    monkeypatch.setattr(common, "VERBOSITY", 0)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        process = Mock(stdout=iter(["Ripping track 1\n", "Read error\n"]))
        process.wait.return_value = 1
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            rc, _ = _cyanrip_run("100", [], Path(tmpdir))
    finally:
        ui.clear_view()

    assert rc == 1
    err = capsys.readouterr().err  # tool lines print to scrollback
    assert "cyanrip  Ripping track 1" in err
    assert "cyanrip  Read error" in err


def test_multiple_releases_prompt_prints_under_a_seam_view(tmp_path, capsys):
    """The Multiple-releases prompt prints to stdout (scrollback) while a
    seam view is set — the prompt suspends the view — and the run completes."""
    from skive import ui

    args = FakeArgs(offset="100", cyanrip_flags=["-N"])

    def ask_then_print(prompt, choices="", default=""):
        print(prompt, end="")
        return "2"

    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch("skive.rip_music.ensure_mounted"), \
                patch("skive.rip_music.RipLog"), \
                patch("skive.rip_music._newest_flac_album", return_value=("-", "-")), \
                patch("skive.rip_music.ui.ask", side_effect=ask_then_print), \
                patch("skive.rip_music._cyanrip_run", side_effect=[
                    (1, "Multiple releases found\n"), (0, "Rip complete\n")]):
            with pytest.raises(SystemExit) as exc:
                rip_music(args)
    finally:
        ui.clear_view()

    assert exc.value.code == 0
    assert "Multiple MusicBrainz releases" in capsys.readouterr().out


def test_run_summary_rows_and_count_after_close(tmp_path, capsys):
    """The run summary: per-track rows (track, duration, result) plus the
    19-track count, printed after the view closed."""
    from skive import ui

    folder_name = "Summertime Blues [FLAC]"
    album = tmp_path / "rip" / "cd" / folder_name
    album.mkdir(parents=True)
    report = _fixture_report()
    (album / "Summertime Blues.log").write_text(report, encoding="utf-8")
    (album / "Summertime Blues.cue").write_text("FILE image.flac\n", encoding="utf-8")
    for name in [Path(line.strip()).name for line in report.splitlines()
                 if line.strip().endswith(".flac")]:
        (album / name).touch()

    args = FakeArgs(offset="6", cyanrip_flags=["-N"])
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    seen = []
    real = ui.run_summary
    monkey = pytest.MonkeyPatch()
    monkey.setattr(ui, "run_summary",
                   lambda *a, **k: seen.append(view._closed) or real(*a, **k))
    try:
        with patch("skive.rip_music.ensure_mounted"), \
                patch("skive.rip_music._cyanrip_run", return_value=(0, "")), \
                patch("skive.rip_music._newest_flac_album",
                      return_value=("Various Artists - Summertime Blues", folder_name)):
            with pytest.raises(SystemExit) as exc:
                rip_music(args)
    finally:
        monkey.undo()
        ui.clear_view()

    assert exc.value.code == 0
    assert seen == [True]  # closed before the summary printed
    err = capsys.readouterr().err
    for col in ("track", "duration", "result"):
        assert col in err
    assert any(l.strip().startswith("1") and "0:04:29" in l and "ok" in l
               for l in err.splitlines())
    assert "ok    19 ok · " in err


def test_interrupt_closes_view_and_fails_once(tmp_path, capsys):
    """Ctrl+C mid-rip under a seam view: the view closes before the fail
    line, one fail line, exit 130, no summary."""
    from skive import ui

    args = FakeArgs(offset="100", cyanrip_flags=["-N"])
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch("skive.rip_music.ensure_mounted"), \
                patch("skive.rip_music._cyanrip_run", side_effect=KeyboardInterrupt):
            with pytest.raises(SystemExit) as e:
                rip_music(args)
    finally:
        ui.clear_view()

    assert e.value.code == 130
    assert view._closed
    err = capsys.readouterr().err
    assert "fail  interrupted · 0 ok" in err
    assert "track" in err  # interrupted runs still leave a summary


# --- Fix round: real stream shapes, live headings, opener, interrupt rows ----


def _fixture_track_title_lines(count):
    """The first `count` indented title lines of the real cyanrip report."""
    path = Path(__file__).parent / "fixtures" / "cyanrip" / "summertime-blues.log"
    return [line for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
            if line.strip().startswith("title:")][:count]


def test_indented_real_titles_head_every_track(monkeypatch, capsys):
    """The real report indents each track's title; every completed track gets
    its own heading (a pending track is not overwritten by the next one)."""
    from skive import ui

    monkeypatch.setattr(common, "VERBOSITY", 0)
    titles = _fixture_track_title_lines(3)
    stream = ["Disc tracks: 19\n"]
    for number, title_line in enumerate(titles, start=1):
        stream += [f"Track {number} ripped and encoded successfully!\n", title_line]
    view = ui.RunView(tty=False, live=False)
    ui.set_view(view)
    process = Mock(stdout=iter(stream))
    process.wait.return_value = 0
    try:
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            _cyanrip_run("+6", [], Path(tmpdir))
    finally:
        ui.clear_view()

    err = capsys.readouterr().err
    assert "info  1/19  Damn Right, I've Got the Blues" in err
    assert "info  2/19  The Healer" in err
    assert "info  3/19  The Thrill Is Gone" in err
    assert "1/19  track 1" not in err


def test_progress_stream_drives_heading_and_footer_at_default_verbosity(monkeypatch, capsys):
    """The track being ripped is headed and footed from cyanrip's progress
    line at default verbosity, and the percent never enters scrollback."""
    from skive import ui

    monkeypatch.setattr(common, "VERBOSITY", 0)
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    process = Mock(stdout=iter([
        "Disc tracks: 4\n",
        "Ripping and encoding track 2, progress - 50.00%, ETA - 30s\n",
    ]))
    process.wait.return_value = 1
    try:
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=process):
            _cyanrip_run("+6", [], Path(tmpdir))
        footer = view.render()
        err = capsys.readouterr().err
    finally:
        ui.clear_view()

    assert "▸ 2/4  track 2" in err
    assert "progress - 50.00%" not in err  # not in scrollback, nor in the failure replay
    assert "track 2/4" in footer
    assert "offset +6" in footer
    assert "38%" in footer  # (1 + 0.5) / 4 of the disc


def test_album_without_artist_opens_before_track_work(monkeypatch, capsys):
    """An album-only header emits its opener at the "Tracks:" line, before the
    first heading — and survives an interrupt mid-track."""
    from skive import ui

    monkeypatch.setattr("skive.rip_music.kill_child_after_grace", lambda proc: None)

    def gen():
        yield "Disc tracks: 2\n"
        yield "Album:          Lonely Album\n"
        yield "Tracks:\n"
        yield "Ripping and encoding track 1, progress - 10.00%\n"
        raise KeyboardInterrupt

    view = ui.RunView(tty=False, live=False)
    ui.set_view(view)
    header = {}
    try:
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch("subprocess.Popen", return_value=Mock(stdout=gen())):
            with pytest.raises(KeyboardInterrupt):
                _cyanrip_run("+6", [], Path(tmpdir), None, header)
    finally:
        ui.clear_view()

    err = capsys.readouterr().err
    opener = "skive rip music · Lonely Album · offset +6"
    assert opener in err
    assert err.index(opener) < err.index("1/2  track 1")
    assert header["_opener_emitted"]


def test_interrupt_summary_keeps_track_outcomes_and_truthful_note(monkeypatch, capsys):
    """Ctrl+C in track 2: the summary lists track 1 ok and track 2
    interrupted, and says nothing was deleted."""
    from skive import ui

    monkeypatch.setattr("skive.rip_music.kill_child_after_grace", lambda proc: None)

    def gen():
        yield "Disc tracks: 3\n"
        yield "Ripping and encoding track 1, progress - 100.00%\n"
        yield "Track 1 ripped and encoded successfully!\n"
        yield "Ripping and encoding track 2, progress - 20.00%\n"
        raise KeyboardInterrupt

    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        with patch("skive.rip_music.ensure_mounted"), \
                patch("subprocess.Popen", return_value=Mock(stdout=gen())):
            with pytest.raises(SystemExit) as e:
                rip_music(FakeArgs(offset="100", cyanrip_flags=["-N"]))
    finally:
        ui.clear_view()

    assert e.value.code == 130
    err = capsys.readouterr().err
    rows = [line.split() for line in err.splitlines()]
    assert ["1", "?", "ok"] in rows
    assert ["2", "?", "interrupted"] in rows
    assert "fail  interrupted · 1 ok" in err
    assert "partial deleted" not in err
    assert "nothing deleted" in err
