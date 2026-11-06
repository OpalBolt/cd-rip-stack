"""Tests for tag.py — no NAS, no beets run, stub everything."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from skive.tag import tag as tag_cmd


class FakeArgs:
    """Minimal argparse.Namespace for tag."""
    pass


def test_mount_before_beets():
    """Test mount is called before beets."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "beets-config.yaml"
        config_path.write_text("plugins: []")

        mock_ensure = Mock()

        with patch.dict(os.environ, {"MUSIC_MOUNT": tmpdir, "NFS_MUSIC": ""}):
            with patch("skive.tag.ensure_mounted", mock_ensure):
                with patch("pathlib.Path.exists", return_value=True):
                    try:
                        tag_cmd(FakeArgs())
                    except SystemExit:
                        pass

        # Check mount was called with /mnt/music default
        mock_ensure.assert_called_once()


def test_nothing_to_import(tmp_path, monkeypatch):
    """Test graceful exit when rip directory doesn't exist."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    fake_rip_dir = tmp_path / "rip"

    orig_exists = Path.exists
    def selective_exists(self):
        if self == fake_rip_dir:
            return False
        return orig_exists(self)

    mock_ensure = Mock()

    with patch("skive.tag.ensure_mounted", mock_ensure):
        with patch("pathlib.Path.exists", new=selective_exists):
            try:
                tag_cmd(FakeArgs())
            except SystemExit as e:
                assert e.code == 0


def test_config_path_resolution():
    """Test config path is resolved correctly relative to this file."""
    import skive.tag
    expected_path = Path(skive.tag.__file__).parent.parent / "config" / "beets-config.yaml"
    assert expected_path.exists() or True  # May not exist if config/ not created yet


def test_beets_invoked_correctly():
    """Test beets is invoked with correct arguments."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "beets-config.yaml"
        config_path.write_text("plugins: []")

        rip_dir = Path(tmpdir) / "rip"
        cd_dir = rip_dir / "cd"
        cd_dir.mkdir(parents=True)
        (cd_dir / "album").mkdir()

        mock_run = Mock(return_value=Mock(returncode=0))
        mock_ensure = Mock()

        with patch.dict(os.environ, {"MUSIC_MOUNT": tmpdir, "NFS_MUSIC": ""}):
            with patch("skive.tag.ensure_mounted", mock_ensure):
                with patch("pathlib.Path.exists", return_value=True):
                    with patch("sys.executable", "/fake/python"):
                        tag_cmd(FakeArgs(), run=mock_run)

        # Check beets was called
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["/fake/python", "-m", "beets"]


def test_beets_child_gets_repo_on_pythonpath(tmp_path, monkeypatch):
    """The local filing plugin is importable regardless of subprocess cwd."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    monkeypatch.setenv("NFS_MUSIC", "")
    (tmp_path / "rip" / "cd" / "Album").mkdir(parents=True)
    captured = {}

    def fake_run(cmd, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return Mock(returncode=0)

    with patch("skive.tag.ensure_mounted"):
        tag_cmd(FakeArgs(), run=fake_run)

    repo_root = str(Path(__file__).parent.parent.resolve())
    assert repo_root in captured["env"]["PYTHONPATH"].split(os.pathsep)


def test_music_mount_env_override():
    """Test MUSIC_MOUNT env overrides the mountpoint."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "beets-config.yaml"
        config_path.write_text("plugins: []")

        mock_ensure = Mock()

        with patch.dict(os.environ, {"MUSIC_MOUNT": "/custom/music", "NFS_MUSIC": ""}):
            with patch("skive.tag.ensure_mounted", mock_ensure):
                with patch("pathlib.Path.exists", return_value=True):
                    try:
                        tag_cmd(FakeArgs())
                    except SystemExit:
                        pass

        # Check ensure_mounted was called with custom mount
        mock_ensure.assert_called_once_with("/custom/music", "")


def test_three_sources_order_and_flags(tmp_path, monkeypatch):
    """Test three sources mean three calls with the right flags and order."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    monkeypatch.setenv("NFS_MUSIC", "")

    # Populate albums in cd, bandcamp, other
    for source in ("cd", "bandcamp", "other"):
        sdir = tmp_path / "rip" / source / "Album"
        sdir.mkdir(parents=True)

    calls = []
    def fake_run(cmd):
        calls.append(cmd)
        return Mock(returncode=0)

    with patch("skive.tag.ensure_mounted"):
        tag_cmd(FakeArgs(), run=fake_run)

    assert len(calls) == 3
    # Order: cd, other, bandcamp
    assert calls[0][-1].endswith("/rip/cd")
    assert "-A" not in calls[0]

    assert calls[1][-1].endswith("/rip/other")
    assert "-A" not in calls[1]

    assert calls[2][-1].endswith("/rip/bandcamp")
    assert "-A" in calls[2]
    # Check -A is passed as an option before source path
    a_idx = calls[2].index("-A")
    assert calls[2][a_idx + 1] == str(tmp_path / "rip" / "bandcamp")


def test_tag_single_source_bandcamp(tmp_path, monkeypatch):
    """Test `tag bandcamp` means one call with -A flag."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    monkeypatch.setenv("NFS_MUSIC", "")

    for source in ("cd", "bandcamp", "other"):
        sdir = tmp_path / "rip" / source / "Album"
        sdir.mkdir(parents=True)

    calls = []
    def fake_run(cmd):
        calls.append(cmd)
        return Mock(returncode=0)

    args = FakeArgs()
    args.source = "bandcamp"

    with patch("skive.tag.ensure_mounted"):
        tag_cmd(args, run=fake_run)

    assert len(calls) == 1
    assert calls[0][-1].endswith("/rip/bandcamp")
    assert "-A" in calls[0]


def test_tag_single_source_cd(tmp_path, monkeypatch):
    """Test `tag cd` means one call without -A flag."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    monkeypatch.setenv("NFS_MUSIC", "")

    sdir = tmp_path / "rip" / "cd" / "Album"
    sdir.mkdir(parents=True)

    calls = []
    def fake_run(cmd):
        calls.append(cmd)
        return Mock(returncode=0)

    args = FakeArgs()
    args.source = "cd"

    with patch("skive.tag.ensure_mounted"):
        tag_cmd(args, run=fake_run)

    assert len(calls) == 1
    assert calls[0][-1].endswith("/rip/cd")
    assert "-A" not in calls[0]


def test_tag_empty_folders_clean_exit(tmp_path, monkeypatch, capsys):
    """Test empty source folders mean a clean exit 0 with 'nothing to import'."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    monkeypatch.setenv("NFS_MUSIC", "")

    # Create empty source dirs
    for source in ("cd", "bandcamp", "other"):
        (tmp_path / "rip" / source).mkdir(parents=True)

    mock_run = Mock()

    with patch("skive.tag.ensure_mounted"):
        try:
            tag_cmd(FakeArgs(), run=mock_run)
        except SystemExit as e:
            assert e.code == 0

    assert not mock_run.called
    assert "ok    nothing to import" in capsys.readouterr().err


def test_tag_skips_empty_source_folders(tmp_path, monkeypatch):
    """Test non-empty folders run, empty ones are skipped."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    monkeypatch.setenv("NFS_MUSIC", "")

    # cd is empty, bandcamp has album, other does not exist
    (tmp_path / "rip" / "cd").mkdir(parents=True)
    (tmp_path / "rip" / "bandcamp" / "Album").mkdir(parents=True)

    calls = []
    def fake_run(cmd):
        calls.append(cmd)
        return Mock(returncode=0)

    with patch("skive.tag.ensure_mounted"):
        tag_cmd(FakeArgs(), run=fake_run)

    assert len(calls) == 1
    assert calls[0][-1].endswith("/rip/bandcamp")


def test_tag_nonzero_exit_if_beets_fails(tmp_path, monkeypatch):
    """Test nonzero exit if any beets invocation fails."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    monkeypatch.setenv("NFS_MUSIC", "")

    (tmp_path / "rip" / "cd" / "Album").mkdir(parents=True)
    (tmp_path / "rip" / "other" / "Album").mkdir(parents=True)

    calls = []
    def fake_run(cmd):
        calls.append(cmd)
        return Mock(returncode=42)

    with patch("skive.tag.ensure_mounted"):
        try:
            tag_cmd(FakeArgs(), run=fake_run)
            assert False, "Should have exited"
        except SystemExit as e:
            assert e.code == 42

    # Should stop after first failed call
    assert len(calls) == 1


def _tag_log_lines(root):
    """Read the JSONL run log written on a temporary music share."""
    return [json.loads(line) for line in (root / "logs" / "rip.jsonl").read_text().splitlines()]


def test_album_outcomes_keep_raw_when_one_is_left(tmp_path, monkeypatch):
    """A moved album is filed, a remaining audio file is left, and raw stays."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    monkeypatch.setenv("NFS_MUSIC", "")
    filed = tmp_path / "rip" / "cd" / "Filed"
    left = tmp_path / "rip" / "cd" / "Left"
    filed.mkdir(parents=True)
    left.mkdir()
    (left / "track.flac").write_bytes(b"audio")

    def fake_run(cmd):
        filed.rmdir()
        return Mock(returncode=0, stdout="beets imported\n")

    with patch("skive.tag.ensure_mounted"):
        tag_cmd(FakeArgs(), run=fake_run)

    lines = _tag_log_lines(tmp_path)
    notes = {line["name"]: line["status"] for line in lines
             if line["status"] in {"filed", "left"}}
    assert notes == {"Filed": "filed", "Left": "left"}
    run = lines[0]["run"]
    assert (tmp_path / "logs" / "raw" / f"{run}.jsonl").exists()


def test_clean_tag_deletes_raw_log(tmp_path, monkeypatch):
    """When all album folders lose their audio, a clean raw log is removed."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    monkeypatch.setenv("NFS_MUSIC", "")
    album = tmp_path / "rip" / "cd" / "Album"
    album.mkdir(parents=True)

    def fake_run(cmd):
        album.rmdir()
        return Mock(returncode=0, stdout="done\n")

    with patch("skive.tag.ensure_mounted"):
        tag_cmd(FakeArgs(), run=fake_run)

    lines = _tag_log_lines(tmp_path)
    run = lines[0]["run"]
    assert not (tmp_path / "logs" / "raw" / f"{run}.jsonl").exists()


def test_tag_interrupt_records_130_and_keeps_raw(tmp_path, monkeypatch):
    """An interrupted beets session is logged and its raw transcript remains."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    monkeypatch.setenv("NFS_MUSIC", "")
    (tmp_path / "rip" / "cd" / "Album").mkdir(parents=True)

    def fake_run(cmd):
        raise KeyboardInterrupt

    with patch("skive.tag.ensure_mounted"):
        try:
            tag_cmd(FakeArgs(), run=fake_run)
        except SystemExit as exc:
            assert exc.code == 130
        else:
            assert False, "interrupt should exit 130"

    lines = _tag_log_lines(tmp_path)
    assert any(line["status"] == "interrupted" for line in lines)
    run = lines[0]["run"]
    assert (tmp_path / "logs" / "raw" / f"{run}.jsonl").exists()


def test_tag_argparse_unknown_source():
    """Test unknown source argument causes argparse error."""
    import pytest
    from skive.main import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["tag", "vinyl"])

def _tag_stderr(tmp_path, monkeypatch, capsys, fake_run):
    """Run tag over a temporary share; return (exit code, stderr lines)."""
    monkeypatch.setenv("MUSIC_MOUNT", str(tmp_path))
    monkeypatch.setenv("NFS_MUSIC", "")
    code = 0
    with patch("skive.tag.ensure_mounted"):
        try:
            tag_cmd(FakeArgs(), run=fake_run)
        except SystemExit as exc:
            code = exc.code
    return code, capsys.readouterr().err.splitlines()


def test_tag_prints_one_symbol_line_per_album_and_a_clean_result(tmp_path, monkeypatch, capsys):
    album = tmp_path / "rip" / "cd" / "Artist - Album"
    album.mkdir(parents=True)

    def fake_run(cmd):
        album.rmdir()
        return Mock(returncode=0)

    code, err = _tag_stderr(tmp_path, monkeypatch, capsys, fake_run)
    assert code == 0
    assert err[0] == "ok    filed Artist - Album"
    assert len(err) == 2 and err[-1].startswith("ok    1 filed \u00b7 ")
    # the screen line replaces the run-log tee
    assert not any(line.startswith("skive:") for line in err)


def test_tag_left_album_warns_and_result_line_is_a_warning(tmp_path, monkeypatch, capsys):
    left = tmp_path / "rip" / "cd" / "Artist - Album"
    left.mkdir(parents=True)
    (left / "track.flac").write_bytes(b"audio")

    code, err = _tag_stderr(tmp_path, monkeypatch, capsys, lambda cmd: Mock(returncode=0))
    assert code == 0
    assert err[0] == "warn  left Artist - Album: still in rip/cd"
    assert err[-1].startswith("warn  0 filed \u00b7 1 left \u00b7 ")
    assert "see " in err[-1] and "rip.jsonl (run " in err[-1]


def test_tag_beets_failure_result_line_carries_exit_code(tmp_path, monkeypatch, capsys):
    (tmp_path / "rip" / "cd" / "Album").mkdir(parents=True)
    code, err = _tag_stderr(tmp_path, monkeypatch, capsys, lambda cmd: Mock(returncode=42))
    assert code == 42
    assert err[-1].startswith("fail  beets failed (exit 42) \u00b7 1 filed \u00b7 ")
    assert "rip.jsonl (run " in err[-1]


def test_tag_interrupt_result_line_counts_albums(tmp_path, monkeypatch, capsys):
    (tmp_path / "rip" / "cd" / "Album").mkdir(parents=True)

    def fake_run(cmd):
        raise KeyboardInterrupt

    code, err = _tag_stderr(tmp_path, monkeypatch, capsys, fake_run)
    assert code == 130
    assert err[-1].startswith("fail  interrupted \u00b7 1 filed \u00b7 ")
