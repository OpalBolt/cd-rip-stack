"""Port of windows/test-transcode.sh's seven scenarios to pytest.

Stubs HandBrakeCLI/mkvmerge with the same shell semantics as the bash test:
HandBrakeCLI copies input->output and exits 3 on input containing FAILME;
mkvmerge concatenates args' contents in order and exits 2 on BADMERGE.
"""

import ast
import importlib.util
import os
import pathlib
import re
import subprocess
import sys

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "transcode", pathlib.Path(__file__).parent.parent / "windows" / "transcode.py"
)
transcode = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(transcode)


class Args:
    def __init__(self, prefixes=None, keep=False, encoder="x265", workdir=None):
        self.prefixes = prefixes or []
        self.keep = keep
        self.encoder = encoder
        self.workdir = workdir


def mk(vid, rel, content):
    p = pathlib.Path(vid) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Stub tools on PATH + VIDEOS/VIDEO_WORK_DIR pointing at tmp dirs."""
    vid = tmp_path / "vid"
    (vid / "rip").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    hb_log = tmp_path / "hb-calls.log"

    (bin_dir / "HandBrakeCLI.exe").write_text(
        f"""#!/bin/sh
{{ printf 'ARGS:'; printf ' %s' "$@"; echo; }} >> "{hb_log}"
in=""; out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -i) in="$2"; shift 2 ;;
    -o) out="$2"; shift 2 ;;
    *)  shift ;;
  esac
done
if grep -q FAILME "$in" 2>/dev/null; then exit 3; fi
cat "$in" > "$out"
"""
    )
    (bin_dir / "mkvmerge.exe").write_text(
        f"""#!/bin/sh
out="$2"; shift 2
: > "$out"
for a in "$@"; do
  [ "$a" = "+" ] && continue
  if grep -q BADMERGE "$a" 2>/dev/null; then rm -f "$out"; exit 2; fi
  cat "$a" >> "$out"
done
"""
    )
    (bin_dir / "ffprobe.exe").write_text(
        """#!/bin/sh
in=""
for a in "$@"; do in="$a"; done
if grep -q 2160 "$in" 2>/dev/null; then echo 2160
elif grep -q 576 "$in" 2>/dev/null; then echo 576
elif grep -q 480 "$in" 2>/dev/null; then echo 480
else echo 1080; fi
"""
    )
    for f in bin_dir.iterdir():
        f.chmod(0o755)

    monkeypatch.setenv("VIDEOS", str(vid))
    monkeypatch.setenv("VIDEO_WORK_DIR", str(work))
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.chdir(tmp_path)
    return tmp_path


# -- scenario 1: end-to-end movie batch ---------------------------------------
def test_scenario1_end_to_end_movie_batch(env, capsys):
    vid = env / "vid"
    mk(vid, "rip/Foo (2001)_t00.mkv", "FOO-MAIN-BYTES-BYTES-BYTES")  # outweighs t01
    mk(vid, "rip/Foo (2001)_t01.mkv", "FOO-EXTRA")
    mk(vid, "rip/Bar (2002)_t00.mkv", "BAR-DECOY")
    mk(vid, "rip/Bar (2002)_t01.p1.mkv", "BAR-P1-BYTES-BYTES-BYTES-BYTES")
    mk(vid, "rip/Bar (2002)_t01.p2.mkv", "BAR-P2-BYTES-BYTES-BYTES-BYTES-BYTES-BYTES")
    mk(vid, "rip/Bar (2002)_t02.mkv", "BAR-EXTRA")
    mk(vid, "rip/Baz Extras (2003)_e00.mkv", "BAZ-E0")
    mk(vid, "rip/Baz Extras (2003)_e02.mkv", "BAZ-E2")
    mk(vid, "rip/B1_t00.mkv", "JUNK-MIDRIP")
    mk(vid, "rip/Failme (1999)_t00.mkv", "FAILME")
    mk(vid, "rip/Qux (2004)_t00.mkv", "QUX-MAIN")

    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err

    assert rc == 1
    L = vid / "library"
    assert (L / "Bar (2002)" / "Bar (2002).mkv").read_text() == \
        "BAR-P1-BYTES-BYTES-BYTES-BYTESBAR-P2-BYTES-BYTES-BYTES-BYTES-BYTES-BYTES"
    assert (L / "Foo (2001)" / "Foo (2001).mkv").read_text() == "FOO-MAIN-BYTES-BYTES-BYTES"
    assert (L / "Foo (2001)" / "extras" / "Foo (2001)_t01.mkv").read_text() == "FOO-EXTRA"
    assert (L / "Bar (2002)" / "extras" / "Bar (2002)_t00.mkv").read_text() == "BAR-DECOY"
    assert (L / "Bar (2002)" / "extras" / "Bar (2002)_t02.mkv").read_text() == "BAR-EXTRA"
    assert (L / "Baz Extras (2003)" / "extras" / "Baz Extras (2003)_e00.mkv").read_text() == "BAZ-E0"
    assert (L / "Baz Extras (2003)" / "extras" / "Baz Extras (2003)_e02.mkv").read_text() == "BAZ-E2"
    assert (L / "Qux (2004)" / "Qux (2004).mkv").read_text() == "QUX-MAIN"
    assert sorted(p.name for p in (vid / "rip").iterdir()) == ["B1_t00.mkv", "Failme (1999)_t00.mkv"]
    assert "skip (junk label" in err
    assert "FAILED (handbrake)" in err
    assert "  total  " in (env / "rip.log").read_text()

    hb = (env / "hb-calls.log").read_text()
    assert len(hb.strip().splitlines()) == 9  # incl. failed Failme
    assert "--preset H.265 MKV 1080p30 " in hb
    assert "--audio-copy-mask aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac,pcm " in hb
    assert "--subtitle-burned=none" in hb
    assert "--audio-fallback ac3" in hb
    assert "--aencoder copy" in hb
    assert "--encoder" not in hb  # default x265 passes no --encoder flag


# -- scenario 2: -Keep archive + clobber refusal + .p2 kept -------------------
def test_scenario2_keep_archive(env, capsys):
    vid = env / "vid"
    mk(vid, "rip/Keep (2005)_t00.mkv", "KEEP-MAIN")
    mk(vid, "rip/Keep (2005)_e01.p2.mkv", "KEEP-EXTRA-P2")

    rc = transcode.run_transcode(Args(keep=True))
    out = capsys.readouterr().out

    assert rc == 0
    assert list((vid / "rip").iterdir()) == []
    assert sorted(p.name for p in (vid / "rip-DONE").iterdir()) == \
        ["Keep (2005)_e01.p2.mkv", "Keep (2005)_t00.mkv"]
    assert (vid / "library" / "Keep (2005)" / "extras" / "Keep (2005)_e01.p2.mkv").read_text() == "KEEP-EXTRA-P2"
    assert "--keep: lossless sources moved to" in out


def test_keep_clobber_refusal(env, capsys):
    vid = env / "vid"
    mk(vid, "rip/Keep (2005)_t00.mkv", "KEEP-MAIN")
    done = vid / "rip-DONE"
    done.mkdir()
    (done / "Keep (2005)_t00.mkv").write_text("EXISTING")

    rc = transcode.run_transcode(Args(keep=True))
    err = capsys.readouterr().err

    assert rc == 0
    assert "already exists -- source left in" in err
    assert (vid / "rip" / "Keep (2005)_t00.mkv").exists()
    assert (done / "Keep (2005)_t00.mkv").read_text() == "EXISTING"


# -- scenario 3: prefix run = one phase, no total; no-match prefix exits 1 ----
def test_scenario3_prefix_single_phase_total(env, capsys):
    vid = env / "vid"
    mk(vid, "rip/Solo (2006)_t00.mkv", "SOLO-MAIN")

    rc = transcode.run_transcode(Args(prefixes=["Solo (2006)"]))

    assert rc == 0
    assert (vid / "library" / "Solo (2006)" / "Solo (2006).mkv").read_text() == "SOLO-MAIN"
    assert "  total  " in (env / "rip.log").read_text()


def test_scenario3b_no_match_prefix_exits_1(env, capsys):
    vid = env / "vid"
    mk(vid, "rip/Solo (2006)_t00.mkv", "SOLO-MAIN")

    rc = transcode.run_transcode(Args(prefixes=["Nope (2999)"]))

    assert rc == 1
    assert "no staged MKVs match" in capsys.readouterr().err


def test_scenario3c_colon_prefix_matches_sanitized_file(env):
    vid = env / "vid"
    mk(vid, "rip/Team America World Police (2004)_t00.mkv", "TEAM-MAIN")

    rc = transcode.run_transcode(Args(prefixes=["Team America: World Police (2004)"]))

    assert rc == 0
    assert (vid / "library" / "Team America World Police (2004)"
            / "Team America World Police (2004).mkv").read_text() == "TEAM-MAIN"


# -- scenario 4: empty rip dir -> exit 0; log line shape ----------------------
def test_scenario4_empty_rip(env, capsys):
    rc = transcode.run_transcode(Args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "no MKVs in" in out


def test_log_line_shape(env):
    vid = env / "vid"
    mk(vid, "rip/Solo (2006)_t00.mkv", "SOLO-MAIN")
    transcode.run_transcode(Args(prefixes=["Solo (2006)"]))
    first = (env / "rip.log").read_text().splitlines()[0]
    cols = first.split("  ")
    assert len(cols) == 8
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", cols[0])
    assert re.match(r"^\d+m\d{2}$", cols[6])
    assert cols[7] == "started"


# -- scenario 5: -Encoder nvenc swaps GPU flags; x265 has none ----------------
def test_scenario5_nvenc_flags(env):
    vid = env / "vid"
    mk(vid, "rip/Gpu (2009)_t00.mkv", "GPU-MAIN")

    rc = transcode.run_transcode(Args(encoder="nvenc"))

    assert rc == 0
    assert (vid / "library" / "Gpu (2009)" / "Gpu (2009).mkv").read_text() == "GPU-MAIN"
    hb = (env / "hb-calls.log").read_text()
    assert "--encoder nvenc_h265" in hb
    assert "--quality 28" in hb


# -- scenario 6: TV seasons/specials/double/junk + movie beside ----------------
def test_scenario6_tv_seasons_specials_junk(env, capsys):
    vid = env / "vid"
    mk(vid, "rip/TV/Show (2010)_S01E02.mkv", "TV-E02")
    mk(vid, "rip/TV/Show (2010)_S01E05-E06.mkv", "TV-DOUBLE")
    mk(vid, "rip/TV/Show (2010)_S00E01.mkv", "TV-SPECIAL")
    mk(vid, "rip/TV/Show (2010)_e03.mkv", "TV-EXTRA")
    mk(vid, "rip/TV/B1_S01E02.mkv", "TV-JUNK")
    mk(vid, "rip/Mov (2011)_t00.mkv", "MOV-MAIN")

    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err

    assert rc == 0
    TL = vid / "library" / "TV" / "Show (2010)"
    assert (TL / "Season 01" / "Show (2010) - S01E02.mkv").read_text() == "TV-E02"
    assert (TL / "Season 01" / "Show (2010) - S01E05-E06.mkv").read_text() == "TV-DOUBLE"
    assert (TL / "Specials" / "Show (2010) - S00E01.mkv").read_text() == "TV-SPECIAL"
    assert (TL / "extras" / "Show (2010)_e03.mkv").read_text() == "TV-EXTRA"
    assert (vid / "library" / "Mov (2011)" / "Mov (2011).mkv").read_text() == "MOV-MAIN"
    assert [p.name for p in (vid / "rip" / "TV").iterdir()] == ["B1_S01E02.mkv"]
    assert "skip (junk label" in err
    assert len((env / "hb-calls.log").read_text().strip().splitlines()) == 5


# -- scenario 7: TV-extras-only disc routes to the TV tree --------------------
def test_scenario7_tv_extras_disc(env):
    vid = env / "vid"
    mk(vid, "rip/TV/Solo Show (2012)_e04.mkv", "TVONLY-EXTRA")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    assert (vid / "library" / "TV" / "Solo Show (2012)" / "extras" /
            "Solo Show (2012)_e04.mkv").read_text() == "TVONLY-EXTRA"
    assert not (vid / "library" / "Solo Show (2012)").exists()
    assert len((env / "hb-calls.log").read_text().strip().splitlines()) == 1


# -- spec extras ---------------------------------------------------------------
def test_merge_orders_nosuffix_first_then_parts(env):
    vid = env / "vid"
    mk(vid, "rip/Multi (2003)_t00.mkv", "A-NOSUFFIX")
    mk(vid, "rip/Multi (2003)_t00.p2.mkv", "B-P2")
    mk(vid, "rip/Multi (2003)_t00.p1.mkv", "C-P1")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    assert (vid / "library" / "Multi (2003)" / "Multi (2003).mkv").read_text() == "A-NOSUFFIXC-P1B-P2"


def test_merge_failure_leaves_sources_and_continues(env, capsys):
    vid = env / "vid"
    mk(vid, "rip/Bad (2001)_t00.p1.mkv", "BADMERGE-P1")
    mk(vid, "rip/Bad (2001)_t00.p2.mkv", "BADMERGE-P2")
    mk(vid, "rip/Good (2002)_t00.mkv", "GOOD")

    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err

    assert rc == 1
    assert "FAILED mkvmerge" in err
    assert sorted(p.name for p in (vid / "rip").iterdir()) == \
        ["Bad (2001)_t00.p1.mkv", "Bad (2001)_t00.p2.mkv"]
    assert (vid / "library" / "Good (2002)" / "Good (2002).mkv").read_text() == "GOOD"


def test_unreachable_share_fails_loudly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VIDEOS", str(tmp_path / "nope"))
    rc = transcode.run_transcode(Args())
    assert rc == 1
    assert "not reachable" in capsys.readouterr().err


def test_tv_lowercase_root_is_tv(env):
    vid = env / "vid"
    mk(vid, "rip/tv/Show (2013)_S01E01.mkv", "LOW-TV")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    assert (vid / "library" / "TV" / "Show (2013)" / "Season 01" /
            "Show (2013) - S01E01.mkv").read_text() == "LOW-TV"


def test_copy2_reports_progress(tmp_path, capsys):
    src = tmp_path / "big.mkv"
    src.write_bytes(b"\0" * (3 * 1024 * 1024))
    dst = tmp_path / "copy.mkv"
    transcode._copy2(str(src), str(dst))
    out = capsys.readouterr().out
    assert dst.read_bytes() == src.read_bytes()
    assert "33%" in out and "100%" in out


def test_no_third_party_imports():
    src = pathlib.Path(__file__).parent.parent / "windows" / "transcode.py"
    tree = ast.parse(src.read_text())
    stdlib = {"argparse", "os", "re", "shutil", "subprocess", "sys",
              "tempfile", "dataclasses", "datetime", "pathlib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] in stdlib, a.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] in stdlib, node.module


def test_dvd_not_upscaled(env):
    vid = env / "vid"
    mk(vid, "rip/Dvd (2001)_t00.mkv", "480")  # ffprobe stub sees "480" -> SD

    rc = transcode.run_transcode(Args())

    assert rc == 0
    hb = (env / "hb-calls.log").read_text()
    assert "--preset H.265 MKV 480p30" in hb
    assert "--detelecine" in hb and "--decomb" in hb


def test_uhd_kept_at_4k(env):
    vid = env / "vid"
    mk(vid, "rip/Uhd (2002)_t00.mkv", "2160")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    hb = (env / "hb-calls.log").read_text()
    assert "--preset H.265 MKV 2160p60 4K" in hb
    assert "--detelecine" not in hb


def test_runs_standalone_help():
    p = pathlib.Path(__file__).parent.parent / "windows" / "transcode.py"
    r = subprocess.run([sys.executable, str(p), "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "usage:" in r.stdout
