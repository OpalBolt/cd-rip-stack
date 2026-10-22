"""Port of windows/test-transcode.sh's seven scenarios to pytest.

Stubs HandBrakeCLI/mkvmerge/ffprobe/seconv/tesseract with the same shell
semantics as the bash test: HandBrakeCLI copies input->output and exits 3 on
input containing FAILME; mkvmerge concatenates args' contents in order and
exits 2 on BADMERGE; ffprobe answers height (csv), subtitle inventory
(-select_streams s) and full-stream signature (merge guard) queries keyed off
content markers; seconv writes one SRT into its --output-folder and exits 1 on
SUBS-FAIL input.
"""

import ast
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timedelta

from unittest.mock import Mock

import pytest

_WINDOWS = pathlib.Path(__file__).parent.parent / "windows"
# transcode.py does `import subs`; when run as a script the script dir is on
# sys.path, but spec_from_file_location does not add it
sys.path.insert(0, str(_WINDOWS))
_SPEC = importlib.util.spec_from_file_location("transcode", _WINDOWS / "transcode.py")
transcode = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(transcode)


class Args:
    def __init__(self, prefixes=None, keep=False, encoder="x265", workdir=None, verbose=0):
        self.prefixes = prefixes or []
        self.keep = keep
        self.encoder = encoder
        self.workdir = workdir
        self.verbose = verbose


def mk(vid, rel, content):
    p = pathlib.Path(vid) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def run_log(tmp_path) -> list[dict]:
    """The transcode run log (VIDEOS points at tmp_path/vid), one dict per line."""
    p = tmp_path / "vid" / "logs" / "rip.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]


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
    seconv_log = tmp_path / "seconv-calls.log"

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
# three query shapes: height (csv), subtitle inventory (json, -select_streams s),
# full-stream signature (json, no select) for the merge guard
in=""; fmt=""; sel=""; prev=""
for a in "$@"; do
  [ "$prev" = "-select_streams" ] && sel="$a"
  [ "$prev" = "-of" ] && fmt="$a"
  in="$a"
  prev="$a"
done
if grep -q PROBEBAD "$in" 2>/dev/null; then echo boom >&2; exit 1; fi
if [ "$fmt" != "json" ]; then
  if grep -q 2160 "$in" 2>/dev/null; then echo 2160
  elif grep -q 576 "$in" 2>/dev/null; then echo 576
  elif grep -q 480 "$in" 2>/dev/null; then echo 480
  else echo 1080; fi
  exit 0
fi
if [ "$sel" = "s" ]; then
  if grep -q SUBS-FAIL "$in" 2>/dev/null; then
    printf '%s\\n' '{"streams":[{"index":2,"codec_name":"dvd_subtitle","disposition":{"forced":0},"tags":{"language":"eng"}}]}'
  elif grep -q SUBS-ENG-DAN "$in" 2>/dev/null; then
    printf '%s\\n' '{"streams":[{"index":2,"codec_name":"dvd_subtitle","disposition":{"forced":1},"tags":{"language":"dan"}},{"index":3,"codec_name":"dvd_subtitle","disposition":{"forced":0},"tags":{"language":"eng"}}]}'
  else
    printf '%s\\n' '{"streams":[]}'
  fi
  exit 0
fi
# guard signatures: all parts identical unless a part carries TRACKS-DIFFER
if grep -q TRACKS-DIFFER "$in" 2>/dev/null; then
  printf '%s\\n' '{"streams":[{"codec_type":"video","codec_name":"mpeg2video","tags":{"language":"und"}},{"codec_type":"audio","codec_name":"ac3","tags":{"language":"eng"}}]}'
else
  printf '%s\\n' '{"streams":[{"codec_type":"video","codec_name":"mpeg2video","tags":{"language":"und"}},{"codec_type":"audio","codec_name":"ac3","tags":{"language":"eng"}},{"codec_type":"subtitle","codec_name":"dvd_subtitle","tags":{"language":"eng"}}]}'
fi
"""
    )
    (bin_dir / "seconv.exe").write_text(
        f"""#!/bin/sh
{{ printf 'CALL:'; printf ' %s' "$@"; echo; }} >> "{seconv_log}"
in="$1"
if grep -q SUBS-FAIL "$in" 2>/dev/null; then exit 1; fi
out=""
for a in "$@"; do
  case "$a" in --output-folder:*) out="${{a#--output-folder:}}" ;; esac
done
printf '1\\n00:00:01,000 --> 00:00:02,000\\ncue\\n\\n' > "$out/track.srt"
"""
    )
    (bin_dir / "tesseract.exe").write_text("#!/bin/sh\nexit 0\n")
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
    mk(vid, "rip/Baz Extras (2003) - Extra 00.mkv", "BAZ-E0")
    mk(vid, "rip/Baz Extras (2003) - Extra 02.mkv", "BAZ-E2")
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
    assert (L / "Baz Extras (2003)" / "extras" / "Baz Extras (2003) - Extra 00.mkv").read_text() == "BAZ-E0"
    assert (L / "Baz Extras (2003)" / "extras" / "Baz Extras (2003) - Extra 02.mkv").read_text() == "BAZ-E2"
    assert (L / "Qux (2004)" / "Qux (2004).mkv").read_text() == "QUX-MAIN"
    assert sorted(p.name for p in (vid / "rip").iterdir()) == ["B1_t00.mkv", "Failme (1999)_t00.mkv"]
    assert "skip (junk label" in err
    assert "FAILED (handbrake)" in err
    assert any(l["phase"] == "total" for l in run_log(env))

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
    mk(vid, "rip/Keep (2005) - Extra 01.p2.mkv", "KEEP-EXTRA-P2")

    rc = transcode.run_transcode(Args(keep=True))
    out = capsys.readouterr().out

    assert rc == 0
    assert list((vid / "rip").iterdir()) == []
    assert sorted(p.name for p in (vid / "rip-DONE").iterdir()) == \
        ["Keep (2005) - Extra 01.p2.mkv", "Keep (2005)_t00.mkv"]
    assert (vid / "library" / "Keep (2005)" / "extras" / "Keep (2005) - Extra 01.p2.mkv").read_text() == "KEEP-EXTRA-P2"
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
    assert any(l["phase"] == "total" for l in run_log(env))


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


# -- scenario 4: empty rip dir -> exit 0; run-log line shape ------------------
def test_scenario4_empty_rip(env, capsys):
    rc = transcode.run_transcode(Args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "no MKVs in" in out


def test_log_line_shape(env):
    vid = env / "vid"
    mk(vid, "rip/Solo (2006)_t00.mkv", "SOLO-MAIN")
    transcode.run_transcode(Args(prefixes=["Solo (2006)"]))
    lines = run_log(env)
    first = lines[0]
    assert list(first) == ["ts", "run", "script", "software", "name", "phase", "status", "elapsed_s"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", first["ts"])
    assert re.fullmatch(r"[0-9a-f]{8}", first["run"])
    assert first["script"] == "transcode.py"
    assert first["software"] == "handbrake"
    assert first["name"] == "-"
    assert first["phase"] == "-"
    assert first["status"] == "started"
    assert first["elapsed_s"] == 0
    # a clean single-item run ends with an ok total on the same run id
    assert lines[-1]["phase"] == "total"
    assert lines[-1]["status"] == "ok"
    assert lines[-1]["run"] == first["run"]


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
    mk(vid, "rip/TV/Show (2010) - Extra 03.mkv", "TV-EXTRA")
    mk(vid, "rip/TV/B1_S01E02.mkv", "TV-JUNK")
    mk(vid, "rip/Mov (2011)_t00.mkv", "MOV-MAIN")

    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err

    assert rc == 0
    TL = vid / "library" / "TV" / "Show (2010)"
    assert (TL / "Season 01" / "Show (2010) - S01E02.mkv").read_text() == "TV-E02"
    assert (TL / "Season 01" / "Show (2010) - S01E05-E06.mkv").read_text() == "TV-DOUBLE"
    assert (TL / "Specials" / "Show (2010) - S00E01.mkv").read_text() == "TV-SPECIAL"
    assert (TL / "extras" / "Show (2010) - Extra 03.mkv").read_text() == "TV-EXTRA"
    assert (vid / "library" / "Mov (2011)" / "Mov (2011).mkv").read_text() == "MOV-MAIN"
    assert [p.name for p in (vid / "rip" / "TV").iterdir()] == ["B1_S01E02.mkv"]
    assert "skip (junk label" in err
    assert len((env / "hb-calls.log").read_text().strip().splitlines()) == 5


# -- scenario 7: TV-extras-only disc routes to the TV tree --------------------
def test_scenario7_tv_extras_disc(env):
    vid = env / "vid"
    mk(vid, "rip/TV/Solo Show (2012) - Extra 04.mkv", "TVONLY-EXTRA")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    assert (vid / "library" / "TV" / "Solo Show (2012)" / "extras" /
            "Solo Show (2012) - Extra 04.mkv").read_text() == "TVONLY-EXTRA"
    assert not (vid / "library" / "Solo Show (2012)").exists()
    assert len((env / "hb-calls.log").read_text().strip().splitlines()) == 1


def test_legacy_eNN_extras_still_file(env):
    """Old _eNN staged names still parse and file under extras/ (backward compat)."""
    vid = env / "vid"
    mk(vid, "rip/Old (2001)_e02.mkv", "OLD-EXTRA")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    assert (vid / "library" / "Old (2001)" / "extras" /
            "Old (2001) - Extra 02.mkv").read_text() == "OLD-EXTRA"


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
    stdlib = {"argparse", "json", "os", "re", "shutil", "signal", "subprocess", "sys",
              "tempfile", "uuid", "dataclasses", "datetime", "pathlib"}
    local = {"subs"}  # windows/subs.py ships beside transcode.py
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] in stdlib | local, a.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] in stdlib | local, node.module


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


# -- slice 03: inline subtitle pass in the transcode batch ---------------------
_ARGV_TAIL = ('--preset H.265 MKV 1080p30 --all-audio --aencoder copy '
              '--audio-copy-mask aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac,pcm '
              '--audio-fallback ac3 ')


def test_feature_argv_zero_subs_and_extras_argv_byte_identical(env):
    vid = env / "vid"
    mk(vid, "rip/Arg (2001)_t00.mkv", "ARG-MAIN-BYTES")  # outweighs t01 so t00 is the main
    mk(vid, "rip/Arg (2001)_t01.mkv", "ARG-EXTRA")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    lines = (env / "hb-calls.log").read_text().strip().splitlines()
    assert len(lines) == 2
    feat = next(l for l in lines if "_t00.h265.mkv" in l)
    extra = next(l for l in lines if "_t01.h265.mkv" in l)
    # feature: the no-match language list rides in front of --all-subtitles
    assert feat.split(".h265.mkv ", 1)[1] == _ARGV_TAIL + \
        "--subtitle-lang-list zzz --all-subtitles --subtitle-burned=none"
    # extra: byte-identical to today's pass-through argv
    assert extra.split(".h265.mkv ", 1)[1] == \
        _ARGV_TAIL + "--all-subtitles --subtitle-burned=none"


def test_guard_mismatch_quarantines_all_parts_no_encode(env, capsys):
    vid = env / "vid"
    mk(vid, "rip/Two (2001)_t00.p1.mkv", "PART-ONE")
    mk(vid, "rip/Two (2001)_t00.p2.mkv", "TRACKS-DIFFER")
    mk(vid, "rip/Next (2002)_t00.mkv", "NEXT")

    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err

    assert rc == 1
    issues = vid / "rip-ISSUES" / "movies"
    assert sorted(p.name for p in issues.iterdir()) == \
        ["Two (2001)_t00.p1.mkv", "Two (2001)_t00.p2.mkv"]
    assert sorted(p.name for p in (vid / "rip").iterdir()) == []
    assert not (vid / "library" / "Two (2001)").exists()
    assert (vid / "library" / "Next (2002)" / "Next (2002).mkv").read_text() == "NEXT"
    assert "FAILED (track guard)" in err
    log = " ".join(l["status"] for l in run_log(env))
    assert "FAIL tracks differ" in log and "p2" in log
    hb = (env / "hb-calls.log").read_text()
    assert len(hb.strip().splitlines()) == 1  # only Next encoded


def test_guard_ffprobe_failure_quarantines(env):
    vid = env / "vid"
    mk(vid, "rip/Rip (2001)_t00.p1.mkv", "PROBEBAD")
    mk(vid, "rip/Rip (2001)_t00.p2.mkv", "PROBEBAD")

    rc = transcode.run_transcode(Args())

    assert rc == 1
    assert sorted(p.name for p in (vid / "rip-ISSUES" / "movies").iterdir()) == \
        ["Rip (2001)_t00.p1.mkv", "Rip (2001)_t00.p2.mkv"]
    assert not (env / "hb-calls.log").exists()


def test_guard_match_still_merges_and_passes_merged_file(env):
    vid = env / "vid"
    mk(vid, "rip/Merge (2003)_t00.p1.mkv", "SUBS-ENG-DAN")
    mk(vid, "rip/Merge (2003)_t00.p2.mkv", "SUBS-ENG-DAN")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    L = vid / "library" / "Merge (2003)"
    assert (L / "Merge (2003).mkv").read_text() == "SUBS-ENG-DANSUBS-ENG-DAN"
    assert (L / "Merge (2003).dan.forced.srt").exists()
    assert (L / "Merge (2003).eng.srt").exists()
    # the pass ran once, on the merged file, never per part
    sc = (env / "seconv-calls.log").read_text().strip().splitlines()
    assert len(sc) == 2 and all("merged.mkv" in l for l in sc)
    assert sorted(p.name for p in (vid / "rip").iterdir()) == []


def test_pass_success_sidecars_beside_encode_and_archived(env):
    vid = env / "vid"
    mk(vid, "rip/Side (2001)_t00.mkv", "SUBS-ENG-DAN")

    rc = transcode.run_transcode(Args(keep=True))

    assert rc == 0
    L = vid / "library" / "Side (2001)"
    assert (L / "Side (2001).mkv").read_text() == "SUBS-ENG-DAN"
    assert (L / "Side (2001).dan.forced.srt").read_text().count(" --> ") == 1
    assert (L / "Side (2001).eng.srt").exists()
    assert sorted(p.name for p in (vid / "rip-DONE").iterdir()) == ["Side (2001)_t00.mkv"]
    assert len((env / "seconv-calls.log").read_text().strip().splitlines()) == 2
    assert list((env / "work").iterdir()) == []  # sidecar temp dir cleaned up


def test_pass_failure_ships_encode_and_quarantines_source(env, capsys):
    vid = env / "vid"
    mk(vid, "rip/Ocr (2001)_t00.mkv", "SUBS-FAIL")

    rc = transcode.run_transcode(Args(keep=True))
    err = capsys.readouterr().err

    assert rc == 1
    assert (vid / "library" / "Ocr (2001)" / "Ocr (2001).mkv").read_text() == "SUBS-FAIL"
    assert list((vid / "rip-DONE").iterdir()) == []  # never archived despite -k
    assert (vid / "rip-ISSUES" / "movies" / "Ocr (2001)_t00.mkv").read_text() == "SUBS-FAIL"
    assert "FAILED (subtitle pass)" in err
    log = " ".join(l["status"] for l in run_log(env))
    assert "FAIL subtitle pass" in log and "seconv failed" in log


def test_tv_episode_pass_and_zero_subs_encode(env):
    vid = env / "vid"
    mk(vid, "rip/TV/Show (2010)_S01E02.mkv", "SUBS-ENG-DAN")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    S1 = vid / "library" / "TV" / "Show (2010)" / "Season 01"
    assert (S1 / "Show (2010) - S01E02.mkv").read_text() == "SUBS-ENG-DAN"
    assert (S1 / "Show (2010) - S01E02.dan.forced.srt").exists()
    assert (S1 / "Show (2010) - S01E02.eng.srt").exists()
    assert "--subtitle-lang-list zzz" in (env / "hb-calls.log").read_text()
    assert sorted(p.name for p in (vid / "rip" / "TV").iterdir()) == []


def test_tv_extra_unchanged_no_pass(env):
    vid = env / "vid"
    mk(vid, "rip/TV/Show (2010) - Extra 03.mkv", "SUBS-ENG-DAN")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    ex = vid / "library" / "TV" / "Show (2010)" / "extras"
    assert (ex / "Show (2010) - Extra 03.mkv").read_text() == "SUBS-ENG-DAN"
    assert not list(ex.glob("*.srt"))  # embedded subs stay, no sidecars
    assert not (env / "seconv-calls.log").exists()  # no pass ran
    assert "--subtitle-lang-list" not in (env / "hb-calls.log").read_text()


# -- slice 02: JSONL RipLog (run log + raw log) --------------------------------
class TestRipLogJsonl:
    """Windows RipLog: same JSONL shapes as skive/common.py via the share root."""

    @staticmethod
    def _log(tmp_path, times):
        ts_iter = iter(times)
        return transcode.RipLog(root=str(tmp_path), now=lambda: next(ts_iter))

    def test_run_log_via_videos_env(self, tmp_path, monkeypatch):
        """Without an explicit root the run log lands under $VIDEOS/logs."""
        monkeypatch.setenv("VIDEOS", str(tmp_path))
        log = transcode.RipLog(now=lambda: datetime(2024, 1, 15, 10, 30, 0))
        log.start()
        lines = [json.loads(l) for l in (tmp_path / "logs" / "rip.jsonl").read_text(encoding="utf-8").splitlines()]
        assert lines == [{
            "ts": "2024-01-15T10:30:00", "run": log.run, "script": "transcode.py",
            "software": "handbrake", "name": "-", "phase": "-",
            "status": "started", "elapsed_s": 0,
        }]

    def test_phase_and_note_lines(self, tmp_path):
        # start, phase start, phase end, note
        times = [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 31, 0),
                 datetime(2024, 1, 15, 10, 36, 0), datetime(2024, 1, 15, 10, 36, 10)]
        log = self._log(tmp_path, times)
        log.start()
        log.phase("encode")
        log.phase_end()
        log.note("encode", "FAILED handbrake: X (2001)_t00.mkv -- source left in place")
        lines = [json.loads(l) for l in (tmp_path / "logs" / "rip.jsonl").read_text(encoding="utf-8").splitlines()]
        assert lines[1]["phase"] == "encode" and lines[1]["status"] == "ok" and lines[1]["elapsed_s"] == 300
        assert lines[2]["phase"] == "encode"
        assert lines[2]["status"] == "FAILED handbrake: X (2001)_t00.mkv -- source left in place"
        assert lines[2]["elapsed_s"] == 0

    def test_finish_fail_total_and_short_uuid(self, tmp_path):
        ts_iter = iter([datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 30, 5)])
        log = transcode.RipLog(root=str(tmp_path), now=lambda: next(ts_iter))
        assert re.fullmatch(r"[0-9a-f]{8}", log.run)  # short UUID minted per invocation
        log.start()
        log.finish(2)
        lines = [json.loads(l) for l in (tmp_path / "logs" / "rip.jsonl").read_text(encoding="utf-8").splitlines()]
        assert lines[-1] == {
            "ts": "2024-01-15T10:30:05", "run": log.run, "script": "transcode.py",
            "software": "handbrake", "name": "-", "phase": "total",
            "status": "FAIL(2)", "elapsed_s": 5,
        }

    def test_raw_log_write_and_delete_on_rc0(self, tmp_path):
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 30, 1), datetime(2024, 1, 15, 10, 30, 2)])
        log.start()
        log.raw("handbrake", "Encoding: task 1")
        raw = tmp_path / "logs" / "raw" / f"{log.run}.jsonl"
        assert [json.loads(l) for l in raw.read_text(encoding="utf-8").splitlines()] == [
            {"ts": "2024-01-15T10:30:01", "run": log.run, "tool": "handbrake", "line": "Encoding: task 1"},
        ]
        log.finish(0)
        assert not raw.exists()  # deleted on a clean exit

    def test_raw_log_kept_on_failure(self, tmp_path):
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 30, 1), datetime(2024, 1, 15, 10, 30, 2)])
        log.start()
        log.raw("mkvmerge", "Error: something broke")
        log.finish(1)
        raw = tmp_path / "logs" / "raw" / f"{log.run}.jsonl"
        assert raw.exists()  # kept on failure
        lines = [json.loads(l) for l in (tmp_path / "logs" / "rip.jsonl").read_text(encoding="utf-8").splitlines()]
        assert lines[-1]["phase"] == "total" and lines[-1]["status"] == "FAIL(1)"

    def test_raw_absent_when_no_tool_ran(self, tmp_path):
        """No raw writes -> no raw file (and nothing to clean on success)."""
        log = self._log(tmp_path, [datetime(2024, 1, 15, 10, 30, 0), datetime(2024, 1, 15, 10, 30, 5)])
        log.start()
        log.finish(0)
        assert not (tmp_path / "logs" / "raw").exists()


def test_handbrake_output_lands_in_raw_log_on_failure(env, capsys):
    """Raw capture wiring: every HandBrake output line reaches the run's raw log
    (kept on failure). The console hides them at the default verbosity, then
    replays them verbatim because the encode failed (story 7)."""
    vid = env / "vid"
    (env / "bin" / "HandBrakeCLI.exe").write_text("""#!/bin/sh
in=""
while [ $# -gt 0 ]; do
  case "$1" in
    -i) in="$2"; shift 2 ;;
    *)  shift ;;
  esac
done
echo "Encoding: task 1 of 1" >&2
echo "work: average 12.3 fps" >&2
echo "Encode done!"
if grep -q FAILME "$in" 2>/dev/null; then exit 3; fi
""")
    mk(vid, "rip/Fail (1999)_t00.mkv", "FAILME")

    rc = transcode.run_transcode(Args())
    out = capsys.readouterr().out

    assert rc == 1
    # failure replay: hidden output reaches the console verbatim
    assert "Encoding: task 1 of 1" in out
    assert "Encode done!" in out
    raws = list((vid / "logs" / "raw").glob("*.jsonl"))
    assert len(raws) == 1  # failure keeps the run's raw log
    lines = [json.loads(l) for l in raws[0].read_text(encoding="utf-8").splitlines()]
    hb = [l for l in lines if l["tool"] == "handbrake"]
    assert [l["line"] for l in hb] == \
        ["Encoding: task 1 of 1", "work: average 12.3 fps", "Encode done!"]


def test_mkvmerge_output_captured_and_replayed_on_failure(env, capsys):
    """Raw capture wiring for the merge: every mkvmerge line reaches the run's
    raw log (tool "mkvmerge", stderr included); the console hides them at the
    default verbosity and replays them verbatim because the merge failed."""
    vid = env / "vid"
    (env / "bin" / "mkvmerge.exe").write_text("""#!/bin/sh
out="$2"; shift 2
echo "mkvmerge v99.0"
echo "Progress: 50%"
echo "Error: the source is broken" >&2
rm -f "$out"
exit 2
""")
    mk(vid, "rip/Bad (2001)_t00.p1.mkv", "BAD-P1")
    mk(vid, "rip/Bad (2001)_t00.p2.mkv", "BAD-P2")

    rc = transcode.run_transcode(Args())
    out = capsys.readouterr().out

    assert rc == 1
    # failure replay: hidden mkvmerge output reaches the console verbatim
    assert "mkvmerge v99.0" in out
    assert "Progress: 50%" in out
    assert "Error: the source is broken" in out
    raws = list((vid / "logs" / "raw").glob("*.jsonl"))
    assert len(raws) == 1  # failure keeps the run's raw log
    lines = [json.loads(l) for l in raws[0].read_text(encoding="utf-8").splitlines()]
    mkv = [l for l in lines if l["tool"] == "mkvmerge"]
    assert [l["line"] for l in mkv] == \
        ["mkvmerge v99.0", "Progress: 50%", "Error: the source is broken"]


# -- verbosity tiers (-v/-vv) ---------------------------------------------------

def test_verbose_flag_levels():
    """Same three levels on the transcoder's argparse: absent 0, -v 1, -vv 2."""
    assert transcode.parse_args([]).verbose == 0
    assert transcode.parse_args(["-v"]).verbose == 1
    assert transcode.parse_args(["-vv"]).verbose == 2


def _hb_stub(env, lines, exit_code=0):
    """HandBrakeCLI stub that prints the given lines then encodes (or fails)."""
    echos = "".join(f"echo {json.dumps(line)}\n" for line in lines)
    (env / "bin" / "HandBrakeCLI.exe").write_text(f"""#!/bin/sh
in=""; out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -i) in="$2"; shift 2 ;;
    -o) out="$2"; shift 2 ;;
    *)  shift ;;
  esac
done
{echos}if grep -q FAILME "$in" 2>/dev/null; then exit 3; fi
cat "$in" > "$out"
exit {exit_code}
""")


def test_encode_quiet_at_default(env, capsys):
    """Level 0: HandBrake lines and the subtitle pass's captured ffprobe output
    stay off the console on a clean run (the raw log still records them)."""
    vid = env / "vid"
    _hb_stub(env, ["Encoding: task 1 of 1", "Encode done!"])
    mk(vid, "rip/tv/Quiet (2020)_S01E01.mkv", "SUBS-ENG-DAN")
    rc = transcode.run_transcode(Args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "Encoding: task 1 of 1" not in out
    assert '"streams"' not in out  # subtitle-pass ffprobe capture stays quiet


def test_encode_error_lines_stream_at_v(env, capsys):
    """Level 1: HandBrake ERROR lines stream; progress chatter stays hidden."""
    vid = env / "vid"
    _hb_stub(env, ["Encoding: task 1 of 1", "ERROR: something broke"])
    mk(vid, "rip/Err (2001)_t00.mkv", "OK")
    rc = transcode.run_transcode(Args(verbose=1))
    out = capsys.readouterr().out
    assert rc == 0
    assert "ERROR: something broke" in out
    assert "Encoding: task 1 of 1" not in out


def test_encode_raw_passthrough_at_vv(env, capsys):
    """Level 2: every HandBrake line streams as captured."""
    vid = env / "vid"
    _hb_stub(env, ["Encoding: task 1 of 1", "ERROR: something broke"])
    mk(vid, "rip/Raw (2001)_t00.mkv", "OK")
    rc = transcode.run_transcode(Args(verbose=2))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Encoding: task 1 of 1" in out
    assert "ERROR: something broke" in out


def test_encode_failure_replays_hidden_lines_at_default(env, capsys):
    """Level 0 failure: hidden HandBrake output replays verbatim (story 7)."""
    vid = env / "vid"
    _hb_stub(env, ["Encoding: task 1 of 1", "ERROR: doomed"], exit_code=3)
    mk(vid, "rip/Replay (1999)_t00.mkv", "FAILME")
    rc = transcode.run_transcode(Args())
    out = capsys.readouterr().out
    assert rc == 1
    assert "Encoding: task 1 of 1" in out
    assert "ERROR: doomed" in out


def test_sub_pass_raw_passthrough_at_vv(env, capsys):
    """-vv passes the subtitle pass's captured ffprobe output to the console."""
    vid = env / "vid"
    mk(vid, "rip/tv/Subs (2020)_S01E01.mkv", "SUBS-ENG-DAN")
    rc = transcode.run_transcode(Args(verbose=2))
    out = capsys.readouterr().out
    assert rc == 0
    assert '"streams"' in out


# -- Interrupt (Ctrl+C), Windows arm (CONTEXT.md: Interrupt) -------------------

class FakeChild:
    """A stub Popen: records terminate/kill/waits, streams a stdout iterable."""

    def __init__(self, stdout=iter(()), stalls=False):
        self.stdout = stdout
        self.stalls = stalls
        self.terminated = False
        self.killed = False
        self.waits = []

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.stalls:
            self.stalls = False
            raise subprocess.TimeoutExpired(cmd="child", timeout=timeout)
        return 0

    def kill(self):
        self.killed = True


def test_sigint_handler_first_raises_interrupted():
    """One Ctrl+C becomes the clean Interrupted exception path."""
    transcode._sigint_count = 0
    with pytest.raises(transcode.Interrupted):
        transcode._sigint_handler(2, None)


def test_sigint_handler_second_exits_without_cleanup(monkeypatch):
    """A second Ctrl+C restores the default disposition and re-raises: instant
    exit, no cleanup, no traceback."""
    calls = []
    monkeypatch.setattr(transcode, "_exit_immediately", lambda: calls.append(1))
    transcode._sigint_count = 1
    transcode._sigint_handler(2, None)  # must not raise
    assert calls == [1]


def test_kill_child_terminates_waits_grace_then_kills():
    """Windows child arm: terminate, wait grace seconds, kill, reap."""
    proc = FakeChild(stalls=True)
    transcode._kill_child(proc, grace=0.1)
    assert proc.terminated and proc.killed
    assert proc.waits == [0.1, None]  # grace wait + post-kill reap


def test_kill_child_exiting_inside_grace_not_killed():
    proc = FakeChild()
    transcode._kill_child(proc, grace=5)
    assert proc.terminated and not proc.killed
    assert proc.waits == [5]


def test_windows_riplog_interrupted_keeps_raw_and_silences_finish(tmp_path):
    """interrupted() writes the status=interrupted total, keeps the raw log,
    and a later finish() adds no second total line."""
    times = iter([datetime(2024, 1, 15, 10, 30, 0) + timedelta(seconds=n) for n in range(4)])
    log = transcode.RipLog(root=str(tmp_path), now=lambda: next(times))
    log.start()
    log.raw("handbrake", "Encoding: task 1 of 1")
    log.interrupted()
    log.finish(1)
    lines = [json.loads(line)
             for line in (tmp_path / "logs" / "rip.jsonl").read_text().splitlines()]
    assert [line["status"] for line in lines] == ["started", "interrupted"]
    assert lines[-1]["elapsed_s"] == 2  # raw call was at +1s
    assert (tmp_path / "logs" / "raw" / f"{log.run}.jsonl").exists()


def test_encode_interrupt_kills_child_and_reraises(monkeypatch):
    """Ctrl+C mid-encode: the HandBrake child goes through the terminate/kill
    helper and Interrupted propagates to run_transcode's exit."""
    def raising_stdout():
        yield "Encoding: task 1 of 1"
        raise transcode.Interrupted()

    proc = FakeChild(stdout=raising_stdout(), stalls=True)  # wedged: killed after grace
    monkeypatch.setattr(transcode.subprocess, "run",
                        lambda *a, **k: Mock(stdout="1080\n", returncode=0))
    monkeypatch.setattr(transcode.subprocess, "Popen", lambda *a, **k: proc)

    with pytest.raises(transcode.Interrupted):
        transcode._encode("src", "out", "x265", "hb.exe", "ffprobe.exe")

    assert proc.terminated and proc.killed
    assert proc.waits == [5.0, None]  # grace wait, then the post-kill reap


def test_interrupt_drops_temp_partial_source_stays_log_130(env, capsys, monkeypatch):
    """Ctrl+C mid-encode: the partial .h265 temp output and the local copy are
    dropped, the SOURCE stays in staging (sources only move on success), the
    run log says interrupted, exit 130."""
    vid = env / "vid"
    mk(vid, "rip/Stop (2007)_t00.mkv", "STOP-MAIN")
    work = pathlib.Path(os.environ["VIDEO_WORK_DIR"])

    def fake_encode(src, out, *a, **k):
        pathlib.Path(out).write_text("PARTIAL-ENCODE")  # the in-flight temp output
        raise transcode.Interrupted()

    monkeypatch.setattr(transcode, "_encode", fake_encode)

    rc = transcode.run_transcode(Args())

    assert rc == 130
    assert (vid / "rip" / "Stop (2007)_t00.mkv").exists()  # source untouched
    assert list(work.iterdir()) == []  # local copy + partial encode dropped
    log = run_log(env)
    assert log[-1]["phase"] == "total" and log[-1]["status"] == "interrupted"
    assert "transcode: interrupted -- partial temp output deleted, source left in staging" \
        in capsys.readouterr().err


def test_interrupt_drops_tv_sidecar_dir_and_temp(env, monkeypatch):
    """Ctrl+C during a TV episode's encode also drops the never-shipped
    sidecar dir from the work dir."""
    vid = env / "vid"
    mk(vid, "rip/tv/Halt (2011)_S01E01.mkv", "HALT-EPISODE")
    work = pathlib.Path(os.environ["VIDEO_WORK_DIR"])

    fake_side = None
    real_sub_pass = transcode._sub_pass

    def spy_sub_pass(local_src, side_dir, base, *a, **k):
        nonlocal fake_side
        fake_side = side_dir
        res = real_sub_pass(local_src, side_dir, base, *a, **k)
        return res

    def fake_encode(src, out, *a, **k):
        raise transcode.Interrupted()

    monkeypatch.setattr(transcode, "_sub_pass", spy_sub_pass)
    monkeypatch.setattr(transcode, "_encode", fake_encode)

    rc = transcode.run_transcode(Args())

    assert rc == 130
    assert list(work.iterdir()) == []  # local copy, sidecar dir, no partial shipped
    assert (vid / "rip" / "tv" / "Halt (2011)_S01E01.mkv").exists()


def test_interrupt_drops_part_copies_in_multi_part_copy_window(env, capsys, monkeypatch):
    """Ctrl+C during a split main's part copies: the local part copies are
    registered in `inflight` as they are created, so the interrupt drops them
    (the completed part-1 copy plus the partial part-2 copy) instead of leaving
    merge work behind; the sources stay in staging, exit 130."""
    vid = env / "vid"
    mk(vid, "rip/Slit (2004)_t00.p1.mkv", "SLIT-P1")
    mk(vid, "rip/Slit (2004)_t00.p2.mkv", "SLIT-P2")
    work = pathlib.Path(os.environ["VIDEO_WORK_DIR"])

    real_copy2 = transcode._copy2

    def fake_copy2(src, dst):
        if pathlib.Path(dst).name.endswith(".p2.mkv"):
            pathlib.Path(dst).write_text("PARTIAL-COPY")  # half-copied second part
            raise transcode.Interrupted()
        real_copy2(src, dst)

    monkeypatch.setattr(transcode, "_copy2", fake_copy2)

    rc = transcode.run_transcode(Args())

    assert rc == 130
    assert (vid / "rip" / "Slit (2004)_t00.p1.mkv").exists()  # both sources untouched
    assert (vid / "rip" / "Slit (2004)_t00.p2.mkv").exists()
    assert list(work.iterdir()) == []  # part-1 local copy + partial part-2 dropped
    log = run_log(env)
    assert log[-1]["phase"] == "total" and log[-1]["status"] == "interrupted"
    assert "transcode: interrupted -- partial temp output deleted, source left in staging" \
        in capsys.readouterr().err
