"""skive doctor — the green table: every check's status and the exit code."""

from pathlib import Path

from skive import doctor

# one DRV: line per drive makemkvcon sees (empty-drive rows count too)
REPLAY = 'DRV:0,1,999,12,"DVD-ROM","LABEL","/dev/sr0"\nDRV:1,0,999,0,"","",""\n'
TESSERACT_OK = "List of available languages (2):\neng\ndan\n"
VAINFO_OK = "VAEntrypointVLD\nVAEntrypointEncSlice\n"


class Proc:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""


class Run:
    """The one run seam for the three tool checks; None means the tool is
    not installed (FileNotFoundError)."""

    def __init__(self, makemkv=REPLAY, tesseract=TESSERACT_OK, vainfo=VAINFO_OK):
        self.makemkv, self.tesseract, self.vainfo = makemkv, tesseract, vainfo

    def __call__(self, cmd, **kwargs):
        if kwargs.get("shell"):  # the MAKEMKV_INFO replay path (_info)
            return Proc(self.makemkv)
        text = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        out = {"makemkvcon": self.makemkv, "tesseract": self.tesseract, "vainfo": self.vainfo}[
            text.split()[0]
        ]
        if out is None:
            raise FileNotFoundError(text)
        return Proc(out)


def green_env(tmp_path, monkeypatch):
    """Everything a box needs: a device node, both shares mounted, the
    key in the environment, a MAKEMKV_INFO replay dump."""
    (tmp_path / "sr0").touch()
    for env, default in (("VIDEOS_MOUNT", "videos"), ("MUSIC_MOUNT", "music")):
        monkeypatch.setenv(env, str(tmp_path / default))
    monkeypatch.setattr(Path, "is_mount", lambda self: True)
    monkeypatch.setenv("MAKEMKV_KEY", "T_test")
    dump = tmp_path / "dump.txt"
    dump.write_text(REPLAY)
    monkeypatch.setenv("MAKEMKV_INFO", f"cat {dump}")
    # the tmdb key check stops at the doctor seam — no network in tests
    monkeypatch.setattr("skive.doctor.validate_key", lambda timeout=5: ("ok", "key valid"))


def test_green_box_exit_0(tmp_path, monkeypatch, capsys):
    green_env(tmp_path, monkeypatch)
    rc = doctor.run_doctor(dev=str(tmp_path), run=Run(), settings=tmp_path / "none.conf")
    assert rc == 0
    assert "box ready" in capsys.readouterr().err


def test_driveless_box_skips_rip_checks_exit_0(tmp_path, monkeypatch, capsys):
    """A drive-less box has no optical nodes: drive, enumeration, and key
    checks skip as warns — the box never rips, so they are not missing
    prerequisites."""
    green_env(tmp_path, monkeypatch)
    monkeypatch.delenv("MAKEMKV_KEY", raising=False)  # key unchecked too
    nodev = tmp_path / "nodev"
    nodev.mkdir()
    rc = doctor.run_doctor(dev=str(nodev), run=Run(), settings=tmp_path / "none.conf")
    err = capsys.readouterr().err
    assert rc == 0
    assert "box ready — with warnings" in err
    assert "skipped" in err  # the rip-only rows say so
    assert "sg module" in err  # ...but keep the drive remedies in the detail


def test_tesseract_missing_dan_exit_1(tmp_path, monkeypatch, capsys):
    green_env(tmp_path, monkeypatch)
    rc = doctor.run_doctor(
        dev=str(tmp_path), run=Run(tesseract="eng\n"), settings=tmp_path / "none.conf"
    )
    assert rc == 1
    assert "dan" in capsys.readouterr().err


def test_key_absent_everywhere_exit_1(tmp_path, monkeypatch, capsys):
    green_env(tmp_path, monkeypatch)
    monkeypatch.delenv("MAKEMKV_KEY", raising=False)
    rc = doctor.run_doctor(dev=str(tmp_path), run=Run(), settings=tmp_path / "none.conf")
    assert rc == 1
    assert "app_Key" in capsys.readouterr().err


def test_key_from_settings_file(tmp_path, monkeypatch, capsys):
    green_env(tmp_path, monkeypatch)
    monkeypatch.delenv("MAKEMKV_KEY", raising=False)
    settings = tmp_path / "settings.conf"
    settings.write_text('app_Key = "T_file"\n')
    rc = doctor.run_doctor(dev=str(tmp_path), run=Run(), settings=settings)
    assert rc == 0


def test_vainfo_absent_warns_but_exit_0(tmp_path, monkeypatch, capsys):
    green_env(tmp_path, monkeypatch)
    rc = doctor.run_doctor(dev=str(tmp_path), run=Run(vainfo=None), settings=tmp_path / "n.conf")
    assert rc == 0
    assert "box ready — with warnings" in capsys.readouterr().err


def test_tmdb_key_rejected_exit_1(tmp_path, monkeypatch, capsys):
    """A key TMDB rejects is a missing prerequisite — the .env quotes bug
    looked like a network problem before the 401 had its own message."""
    green_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "skive.doctor.validate_key",
        lambda timeout=5: ("fail", "TMDB answered 401 — invalid or revoked key"),
    )
    rc = doctor.run_doctor(dev=str(tmp_path), run=Run(), settings=tmp_path / "n.conf")
    assert rc == 1
    assert "401" in capsys.readouterr().err


def test_tmdb_unreachable_warns_but_exit_0(tmp_path, monkeypatch, capsys):
    """An unreachable API proves nothing about the key — warn, not fail."""
    green_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "skive.doctor.validate_key", lambda timeout=5: ("warn", "TMDB unreachable: timed out")
    )
    rc = doctor.run_doctor(dev=str(tmp_path), run=Run(), settings=tmp_path / "n.conf")
    assert rc == 0
    assert "box ready — with warnings" in capsys.readouterr().err


def test_piped_render_is_plain_tagged_lines(tmp_path, monkeypatch, capsys):
    """Piped (pytest's capture): one tagged event line per row, no box
    characters — the event-line rule."""
    green_env(tmp_path, monkeypatch)
    nodev = tmp_path / "nodev"
    nodev.mkdir()
    monkeypatch.delenv("MAKEMKV_KEY", raising=False)
    doctor.run_doctor(dev=str(nodev), run=Run(vainfo=None), settings=tmp_path / "n.conf")
    err = capsys.readouterr().err
    assert "│" not in err and "╭" not in err
    assert "warn  optical devices:" in err  # drive-less: warn rows, not fail
    assert "warn  vaapi encode:" in err
