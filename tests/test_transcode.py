"""skive transcode (BC-250 slice 02) — this slice's scenarios ported from
tests/test_windows_transcode.py to the skive command.

Fake tools are the Windows suite's /bin/sh fakes with the Linux names
(HandBrakeCLI/mkvmerge/ffprobe/seconv/tesseract, no .exe) on PATH:
HandBrakeCLI copies input->output (exit 3 on FAILME) and logs its argv;
ffprobe answers height (csv) and duration (csv, format=duration — the
validation encode record's probe) keyed off content markers, plus the
subtitle-inventory and merge-guard query shapes the later slices reuse.
VIDEOS_MOUNT replaces the Windows suite's VIDEOS env; Args drives
skive.transcode.run_transcode directly. The sub-pass/merge/TV/extras/keep/
retry scenarios stay in the Windows file until slices 03-06 port them, and
this slice's scope changes show up as adapted assertions: split mains skip
whole (slice 05 merges), extras and TV fall through unencoded (slices 04/05),
and skive's RipLog skips the total when a clean run had exactly one phase.
"""

import json
import os
import pathlib
import re

import pytest

from skive import transcode
from skive.common import Interrupted


class Args:
    def __init__(self, prefixes=None, keep=False, encoder="x265", workdir=None, verbose=0, retry=False):
        self.prefixes = prefixes or []
        self.keep = keep
        self.encoder = encoder
        self.workdir = workdir
        self.verbose = verbose
        self.retry = retry


def mk(vid, rel, content):
    p = pathlib.Path(vid) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def run_log(tmp_path) -> list[dict]:
    """The transcode run log (VIDEOS_MOUNT points at tmp_path/vid), one dict per line."""
    p = tmp_path / "vid" / "logs" / "rip.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Stub tools on PATH + VIDEOS_MOUNT/VIDEO_WORK_DIR pointing at tmp dirs."""
    vid = tmp_path / "vid"
    (vid / "rip").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    hb_log = tmp_path / "hb-calls.log"
    seconv_log = tmp_path / "seconv-calls.log"

    (bin_dir / "HandBrakeCLI").write_text(
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
    (bin_dir / "mkvmerge").write_text(
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
    (bin_dir / "ffprobe").write_text(
        """#!/bin/sh
# four query shapes: height (csv), duration (csv, format=duration — the
# validation encode record's probe), subtitle inventory (json, -select_streams s),
# full-stream signature (json, no select) for the merge guard
in=""; fmt=""; sel=""; prev=""
for a in "$@"; do
  [ "$prev" = "-select_streams" ] && sel="$a"
  [ "$prev" = "-of" ] && fmt="$a"
  in="$a"
  prev="$a"
done
if grep -q PROBEBAD "$in" 2>/dev/null; then echo boom >&2; exit 1; fi
case "$*" in
  *format=duration*)
    # whole seconds; a DUR-6251 content marker names its own duration
    if grep -q DUR-6251 "$in" 2>/dev/null; then echo 6251.244000
    else echo 5580.000000
    fi
    exit 0 ;;
esac
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
    (bin_dir / "seconv").write_text(
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
    (bin_dir / "tesseract").write_text("#!/bin/sh\nexit 0\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)

    monkeypatch.setenv("VIDEOS_MOUNT", str(vid))
    monkeypatch.setenv("VIDEO_WORK_DIR", str(work))
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.chdir(tmp_path)
    return tmp_path


# -- staging walk: both resting depths, junk labels ----------------------------

def test_scan_finds_both_resting_depths(env):
    """_scan finds promoted files at <type>/<Name>/<file>.mkv AND fresh files at
    <type>/<Name>/d{disc}/<file>.mkv; the type level is still the first path
    segment under the input root."""
    vid = env / "vid"
    promoted = vid / "rip" / "movies" / "Deep (2001)" / "Deep (2001)_t00.mkv"
    fresh = vid / "rip" / "movies" / "Deep (2001)" / "d2" / "Deep (2001)_t01.mkv"
    tv_deep = vid / "rip" / "tv" / "Deep Show (2002)" / "d3" / "Deep Show (2002)_S01E01.mkv"
    for p in (promoted, fresh, tv_deep):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("X")

    items, _ = transcode._scan(str(vid / "rip"), [])

    assert sorted(i.path for i in items) == sorted(str(p) for p in (promoted, fresh, tv_deep))
    by_name = {i.name: i for i in items}
    assert by_name["Deep (2001)_t00.mkv"].is_tv is False
    assert by_name["Deep (2001)_t01.mkv"].is_tv is False
    assert by_name["Deep Show (2002)_S01E01.mkv"].is_tv is True


def test_junk_label_skipped_and_stays(env, capsys):
    """A junk-labeled file (mid-rip name, no (YYYY) tail) is skipped loudly and
    stays in staging while the good title beside it encodes."""
    vid = env / "vid"
    mk(vid, "rip/movies/Baz Extras (2003)/d2/B1_t00.mkv", "JUNK-MIDRIP")
    mk(vid, "rip/movies/Good One (2001)/d0/Good One (2001)_t00.mkv", "GOOD-MAIN")

    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err

    assert rc == 0
    assert (vid / "library" / "movies" / "Good One (2001)" / "Good One (2001).mkv").read_text() == "GOOD-MAIN"
    assert (vid / "rip" / "movies" / "Baz Extras (2003)" / "d2" / "B1_t00.mkv").exists()
    assert "skip (junk label" in err
    assert len((env / "hb-calls.log").read_text().strip().splitlines()) == 1


# -- prefix filter ---------------------------------------------------------------

def test_prefix_run_ships_matching_title(env):
    vid = env / "vid"
    mk(vid, "rip/movies/Solo (2006)/d0/Solo (2006)_t00.mkv", "SOLO-MAIN")

    rc = transcode.run_transcode(Args(prefixes=["Solo (2006)"]))

    assert rc == 0
    assert (vid / "library" / "movies" / "Solo (2006)" / "Solo (2006).mkv").read_text() == "SOLO-MAIN"


def test_no_match_prefix_exits_1(env, capsys):
    vid = env / "vid"
    mk(vid, "rip/movies/Solo (2006)/d0/Solo (2006)_t00.mkv", "SOLO-MAIN")

    rc = transcode.run_transcode(Args(prefixes=["Nope (2999)"]))

    assert rc == 1
    assert "no staged MKVs match" in capsys.readouterr().err


def test_colon_prefix_matches_sanitized_file(env):
    vid = env / "vid"
    mk(vid, "rip/movies/Team America World Police (2004)/d0/Team America World Police (2004)_t00.mkv", "TEAM-MAIN")

    rc = transcode.run_transcode(Args(prefixes=["Team America: World Police (2004)"]))

    assert rc == 0
    assert (vid / "library" / "movies" / "Team America World Police (2004)"
            / "Team America World Police (2004).mkv").read_text() == "TEAM-MAIN"


# -- movie main pick: doc-recorded main vs biggest-bytes fallback ----------------

def _movie_doc(main, **over):
    """A minimal movie validation.json body (skive/validation.movie_document shape)."""
    d = {"tmdb": {"id": "476", "type": "movie", "title": "Doc (2000)",
                  "runtime_s": 6480},
         "volume_label": "LABEL", "run": "deadbeef", "main": main,
         "titles": [], "encodes": []}
    d.update(over)
    return json.dumps(d)


def test_recorded_main_beats_bytes_and_appends_encodes(env):
    """The Mamma Mia shape: bytes say t12, the doc says t21 -- t21 encodes as
    the main, the encode is appended to the doc's encodes with the probed
    actual duration, and the doc copies beside the encode into the library
    folder; the source is gone after the verified ship. The byte-winner _t
    title stays in staging (it files as an extra from slice 04)."""
    vid = env / "vid"
    mk(vid, "rip/movies/Mamma Mia! (2008)/Mamma Mia! (2008)_t12.mkv",
       "RED-BYTES-BYTES-BYTES-BYTES-BYTES")  # biggest bytes: the wrong film
    mk(vid, "rip/movies/Mamma Mia! (2008)/Mamma Mia! (2008)_t21.mkv",
       "MAMMA-DUR-6251")                     # the recorded main
    mk(vid, "rip/movies/Mamma Mia! (2008)/validation.json", _movie_doc("t21"))

    rc = transcode.run_transcode(Args())

    assert rc == 0
    L = vid / "library" / "movies" / "Mamma Mia! (2008)"
    assert (L / "Mamma Mia! (2008).mkv").read_text() == "MAMMA-DUR-6251"
    entry = {"file": "Mamma Mia! (2008).mkv", "src": "t21", "dur_s": 6251}
    assert json.loads((L / "validation.json").read_text())["encodes"] == [entry]
    staged = vid / "rip" / "movies" / "Mamma Mia! (2008)" / "validation.json"
    assert json.loads(staged.read_text())["encodes"] == [entry]
    # the shipped main's source is deleted; the demoted byte-winner waits for
    # slice 04's extras arm
    assert not (vid / "rip" / "movies" / "Mamma Mia! (2008)" / "Mamma Mia! (2008)_t21.mkv").exists()
    assert (vid / "rip" / "movies" / "Mamma Mia! (2008)" / "Mamma Mia! (2008)_t12.mkv").exists()
    assert not (L / "extras").exists()


def test_null_main_and_absent_doc_keep_bytes_fallback(env):
    """Legacy behavior: an absent doc and a doc recording main=null both fall
    back to biggest-bytes; only an existing doc gets the append + copy."""
    vid = env / "vid"
    mk(vid, "rip/movies/Shorts (2001)/Shorts (2001)_t12.mkv", "NULL-BIG-BYTES")
    mk(vid, "rip/movies/Shorts (2001)/Shorts (2001)_t21.mkv", "NULL-SMALL")
    mk(vid, "rip/movies/Shorts (2001)/validation.json", _movie_doc(None))
    mk(vid, "rip/movies/Legacy (2002)/Legacy (2002)_t12.mkv", "LEGACY-BIG-BYTES")
    mk(vid, "rip/movies/Legacy (2002)/Legacy (2002)_t21.mkv", "LEGACY-SMALL")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    L = vid / "library" / "movies"
    assert (L / "Shorts (2001)" / "Shorts (2001).mkv").read_text() == "NULL-BIG-BYTES"
    assert (L / "Legacy (2002)" / "Legacy (2002).mkv").read_text() == "LEGACY-BIG-BYTES"
    # the existing (null-main) doc still records the fallback encode's result
    assert json.loads((L / "Shorts (2001)" / "validation.json").read_text())["encodes"] == [
        {"file": "Shorts (2001).mkv", "src": "t12", "dur_s": 5580}]
    # no doc ever existed for the legacy folder -> no library copy
    assert not (L / "Legacy (2002)" / "validation.json").exists()
    # the non-main _t title waits for slice 04's extras arm
    assert (vid / "rip" / "movies" / "Shorts (2001)" / "Shorts (2001)_t21.mkv").exists()


def test_split_main_skips_whole_until_merge(env, capsys):
    """Slice 05 adds the part merge; until then a split main (.pN parts)
    skips whole — no partial ship, no source move, one stderr + log note."""
    vid = env / "vid"
    mk(vid, "rip/movies/Bar (2002)/d1/Bar (2002)_t01.p1.mkv", "BAR-P1-BYTES")
    mk(vid, "rip/movies/Bar (2002)/d1/Bar (2002)_t01.p2.mkv", "BAR-P2-BYTES")

    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err

    assert rc == 0
    assert not (vid / "library" / "movies" / "Bar (2002)").exists()
    assert (vid / "rip" / "movies" / "Bar (2002)" / "d1" / "Bar (2002)_t01.p1.mkv").exists()
    assert (vid / "rip" / "movies" / "Bar (2002)" / "d1" / "Bar (2002)_t01.p2.mkv").exists()
    assert "split main (2 parts)" in err
    log = " ".join(l["status"] for l in run_log(env))
    assert "split main (2 parts)" in log


# -- HandBrake argument mapping --------------------------------------------------

def test_x265_default_arg_list(env):
    """The default x265 run's HandBrake argv is the Windows script's, byte for
    byte (mod -i/-o paths): preset by probed height, no --encoder/--quality,
    copy mask, AC-3 fallback, and the language-list trick selecting zero subs."""
    vid = env / "vid"
    mk(vid, "rip/movies/Blur (2008)/d0/Blur (2008)_t00.mkv", "BLUR-MAIN")  # stub height: 1080

    rc = transcode.run_transcode(Args())

    assert rc == 0
    line = (env / "hb-calls.log").read_text().strip().splitlines()[0]
    assert re.search(
        r"ARGS: -i .+? -o .+? --preset H\.265 MKV 1080p30 "
        r"--all-audio --aencoder copy "
        r"--audio-copy-mask aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac,pcm "
        r"--audio-fallback ac3 --subtitle-lang-list zzz --all-subtitles --subtitle-burned=none$",
        line)
    assert "--encoder" not in line  # default x265 passes no --encoder flag
    assert "--quality" not in line


def test_nvenc_flags(env):
    vid = env / "vid"
    mk(vid, "rip/movies/Gpu (2009)/d0/Gpu (2009)_t00.mkv", "GPU-MAIN")

    rc = transcode.run_transcode(Args(encoder="nvenc"))

    assert rc == 0
    assert (vid / "library" / "movies" / "Gpu (2009)" / "Gpu (2009).mkv").read_text() == "GPU-MAIN"
    hb = (env / "hb-calls.log").read_text()
    assert "--encoder nvenc_h265" in hb
    assert "--quality 28" in hb


def test_dvd_not_upscaled(env):
    vid = env / "vid"
    mk(vid, "rip/movies/Dvd (2001)/d0/Dvd (2001)_t00.mkv", "480")  # ffprobe stub sees "480" -> SD

    rc = transcode.run_transcode(Args())

    assert rc == 0
    hb = (env / "hb-calls.log").read_text()
    assert "--preset H.265 MKV 480p30" in hb
    assert "--detelecine" in hb and "--decomb" in hb


# -- run log / raw log shapes -----------------------------------------------------

def test_log_line_shape(env):
    vid = env / "vid"
    mk(vid, "rip/movies/Solo (2006)/d0/Solo (2006)_t00.mkv", "SOLO-MAIN")
    transcode.run_transcode(Args(prefixes=["Solo (2006)"]))
    lines = run_log(env)
    first = lines[0]
    assert list(first) == ["ts", "run", "script", "software", "name", "phase", "status", "elapsed_s"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", first["ts"])
    assert re.fullmatch(r"[0-9a-f]{8}", first["run"])
    assert first["script"] == "skive transcode"
    assert first["software"] == "handbrake"
    assert first["name"] == "-"
    assert first["phase"] == "-"
    assert first["status"] == "started"
    assert first["elapsed_s"] == 0
    # skive's RipLog: a clean run with exactly one phase logs that phase and
    # no total (the lone phase line IS the run record)
    assert lines[-1]["phase"] == "encode" and lines[-1]["status"] == "ok"
    assert lines[-1]["run"] == first["run"]
    assert not [l for l in lines if l["phase"] == "total"]
    # the raw log is deleted on a clean exit
    assert list((vid / "logs" / "raw").iterdir()) == []


def test_failed_encode_keeps_source_and_raw_log(env, capsys):
    """HandBrake failing (the fake exits 3 on FAILME) keeps the source in
    staging and the run's raw log on the share, and fails the run."""
    vid = env / "vid"
    mk(vid, "rip/movies/Failme (1999)/d1/Failme (1999)_t00.mkv", "FAILME")

    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err

    assert rc == 1
    assert (vid / "rip" / "movies" / "Failme (1999)" / "d1" / "Failme (1999)_t00.mkv").exists()
    assert not (vid / "library" / "movies" / "Failme (1999)" / "Failme (1999).mkv").exists()
    assert "FAILED handbrake" in err
    lines = run_log(env)
    assert any(l["phase"] == "total" and l["status"] == "FAIL(1)" for l in lines)
    # a failed run keeps the raw log (only finish(0) deletes it)
    assert list((vid / "logs" / "raw").iterdir()) != []


def test_unreachable_share_fails_loudly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VIDEOS_MOUNT", str(tmp_path / "nope"))
    rc = transcode.run_transcode(Args())
    assert rc == 1
    assert "not reachable" in capsys.readouterr().err


# -- Interrupt (Ctrl+C), skive.common's POSIX arm ---------------------------------

def test_interrupt_drops_temp_partial_source_stays_log_130(env, capsys, monkeypatch):
    """Ctrl+C mid-encode: the partial .h265 temp output and the local copy are
    dropped, the SOURCE stays in staging (sources only move on success), the
    run log says interrupted, exit 130."""
    vid = env / "vid"
    mk(vid, "rip/movies/Stop (2007)/d0/Stop (2007)_t00.mkv", "STOP-MAIN")
    work = pathlib.Path(os.environ["VIDEO_WORK_DIR"])

    def fake_encode(src, out, *a, **k):
        pathlib.Path(out).write_text("PARTIAL-ENCODE")  # the in-flight temp output
        raise Interrupted()

    monkeypatch.setattr(transcode, "_encode", fake_encode)

    rc = transcode.run_transcode(Args())

    assert rc == 130
    assert (vid / "rip" / "movies" / "Stop (2007)" / "d0" / "Stop (2007)_t00.mkv").exists()  # source untouched
    assert list(work.iterdir()) == []  # local copy + partial encode dropped
    log = run_log(env)
    assert log[-1]["phase"] == "total" and log[-1]["status"] == "interrupted"
    assert "interrupted -- partial temp output deleted, source left in staging" \
        in capsys.readouterr().err
