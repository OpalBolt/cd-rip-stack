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
from rich.prompt import Prompt


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
            patch("skive.rip_music.Prompt.ask", return_value="2"), \
            patch("skive.rip_music._cyanrip_run", side_effect=[
                (1, "Multiple releases found\n"),
                (0, "Rip complete\n"),
            ]) as mock_run:
        with pytest.raises(SystemExit) as exc:
            rip_music(args)

    assert exc.value.code == 0
    rip_dir = tmp_path / "rip" / "cd"
    log = MockLog.return_value  # the raw log rides along to _cyanrip_run
    assert mock_run.call_args_list == [
        (("100", ["-N"], rip_dir, log, {}),),
        (("100", ["-R", "2", "-N"], rip_dir, log, {}),),
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
    assert capsys.readouterr().err == "skive: MusicBrainz is unreachable\n"


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
            patch("skive.rip_music.Prompt.ask", return_value="3"), \
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
    assert "skive: interrupted" in err
    assert err.count("skive: interrupted") == 1


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
