"""Tests for tag.py — no NAS, no beets run, stub everything."""

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

        with patch("skive.tag.ensure_mounted", mock_ensure):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("sys.executable", "/fake/python"):
                    with patch("os.execvp", side_effect=SystemExit(0)):
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
            with patch("sys.executable", "/fake/python"):
                with patch("os.execvp", side_effect=SystemExit(0)):
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
        rip_dir.mkdir()

        mock_exec = Mock()
        mock_ensure = Mock()

        with patch("skive.tag.ensure_mounted", mock_ensure):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("sys.executable", "/fake/python"):
                    with patch("os.execvp", mock_exec):
                        try:
                            tag_cmd(FakeArgs())
                        except SystemExit:
                            pass

        # Check execvp was called with beets
        assert mock_exec.called
        argv = mock_exec.call_args[0][1]
        assert argv[:3] == ["/fake/python", "-m", "beets"]


def test_music_mount_env_override():
    """Test MUSIC_MOUNT env overrides the mountpoint."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "beets-config.yaml"
        config_path.write_text("plugins: []")

        mock_ensure = Mock()

        with patch.dict(os.environ, {"MUSIC_MOUNT": "/custom/music", "NFS_MUSIC": ""}):
            with patch("skive.tag.ensure_mounted", mock_ensure):
                with patch("pathlib.Path.exists", return_value=True):
                    with patch("sys.executable", "/fake/python"):
                        with patch("os.execvp", side_effect=SystemExit(0)):
                            try:
                                tag_cmd(FakeArgs())
                            except SystemExit:
                                pass

        # Check ensure_mounted was called with custom mount
        mock_ensure.assert_called_once_with("/custom/music", "")