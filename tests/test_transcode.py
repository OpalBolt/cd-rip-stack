"""skive transcode (BC-250 slice 02) — this slice's scenarios ported from
tests/test_windows_transcode.py to the skive command.

Fake tools are the Windows suite's /bin/sh fakes with the Linux names
(HandBrakeCLI/mkvmerge/ffprobe/seconv/tesseract, no .exe) on PATH:
HandBrakeCLI copies input->output (exit 3 on FAILME) and logs its argv;
ffprobe answers height (csv) and duration (csv, format=duration — the
validation encode record's probe) keyed off content markers, plus the
subtitle-inventory and merge-guard query shapes the later slices reuse.
VIDEOS_MOUNT replaces the Windows suite's VIDEOS env; Args drives
skive.transcode.run_transcode directly. Slice 03 ported the sub-pass
scenarios; slice 04 ported the TV scenarios (seasons/specials/junk, the
show-doc copy and its absence, TV extras, the episode pass, the TV
interrupt); slice 05 ported the part-merge scenarios (ordering, guard,
mkvmerge failure) and the movie-extras scenarios; slice 06 ported the
keep/retry scenarios. The earlier slices'
scope changes show up as adapted assertions: demoted byte-winners now file
as extras, and skive's RipLog skips the total when a clean run had exactly
one phase.
"""

import json
import os
import pathlib
import re
import shutil

import pytest

from skive import transcode, ui
from skive.common import Interrupted


class Args:
    def __init__(self, prefixes=None, keep=False, encoder="x265", workdir=None, verbose=0, retry=False,
                 limit=4):
        self.prefixes = prefixes or []
        self.keep = keep
        self.encoder = encoder
        # production ignores $VIDEO_WORK_DIR: the fixture's var stands in for
        # the -w flag so every test shares one work dir
        self.workdir = workdir if workdir is not None else os.environ.get("VIDEO_WORK_DIR")
        self.verbose = verbose
        self.retry = retry
        self.limit = limit


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
if [ "$sel" = "v:0" ] && [ "$fmt" = "json" ]; then
  # frame geometry (json, the crop detector's probe): a LETTERBOX-WxH content
  # marker names its own geometry
  if grep -q PROBEFRAMEBAD "$in" 2>/dev/null; then echo boom >&2; exit 1; fi
  if grep -q LETTERBOX-720x576 "$in" 2>/dev/null; then
    printf '%s\n' '{"streams":[{"width":720,"height":576,"field_order":"progressive"}],"format":{"duration":"5580.0"}}'
  elif grep -q LETTERBOX-720x480 "$in" 2>/dev/null; then
    printf '%s\n' '{"streams":[{"width":720,"height":480,"field_order":"progressive"}],"format":{"duration":"5580.0"}}'
  else
    printf '%s\n' '{"streams":[{"width":1920,"height":1080,"field_order":"progressive"}],"format":{"duration":"5580.0"}}'
  fi
  exit 0
fi
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
  elif grep -q SUBS-SETTLE "$in" 2>/dev/null; then
    printf '%s\\n' '{"streams":[{"index":2,"codec_name":"dvd_subtitle","disposition":{"forced":1},"tags":{"language":"eng"}},{"index":3,"codec_name":"dvd_subtitle","disposition":{"forced":0},"tags":{"language":"eng"}},{"index":4,"codec_name":"dvd_subtitle","disposition":{"forced":0},"tags":{"language":"eng"}}]}'
  elif grep -q SUBS-EMPTY "$in" 2>/dev/null || grep -q SUBS-BARREN "$in" 2>/dev/null; then
    printf '%s\\n' '{"streams":[{"index":2,"codec_name":"dvd_subtitle","disposition":{"forced":0},"tags":{"language":"eng"}}]}'
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
if grep -q SUBS-BARREN "$in" 2>/dev/null; then
  echo 'No subtitle tracks matched'
  exit 1
fi
if grep -q SUBS-FAIL "$in" 2>/dev/null; then exit 1; fi
out=""
for a in "$@"; do
  case "$a" in --output-folder:*) out="${{a#--output-folder:}}" ;; esac
done
if grep -q SUBS-EMPTY "$in" 2>/dev/null; then
  : > "$out/track.srt"  # OCR ran but produced zero cues
else
  printf '1\\n00:00:01,000 --> 00:00:02,000\\ncue\\n\\n' > "$out/track.srt"
fi
"""
    )
    (bin_dir / "tesseract").write_text("#!/bin/sh\nexit 0\n")
    ffmpeg_log = tmp_path / "ffmpeg-calls.log"
    (bin_dir / "ffmpeg").write_text(
        f"""#!/bin/sh
{{ printf 'ARGS:'; printf ' %s' "$@"; echo; }} >> "{ffmpeg_log}"
in=""; out=""; prev=""
for a in "$@"; do
  [ "$prev" = "-i" ] && in="$a"
  out="$a"
  prev="$a"
done
case "$*" in
  *cropdetect*)
    # cropdetect probe window (stderr): a BARS-l/t/r/b content marker drives
    # the settled read; BARS-UNSTABLE alternates reads so one side dissents
    if grep -q BARS-UNSTABLE "$in" 2>/dev/null; then
      n=$(cat "$in.cnt" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$in.cnt"
      if [ $((n % 2)) -eq 0 ]; then echo 'crop=720:432:36:72' >&2
      else echo 'crop=720:432:0:72' >&2; fi
    elif grep -q BARS-0/72/0/72 "$in" 2>/dev/null; then
      echo 'crop=720:432:0:72' >&2
    elif grep -q BARS-0/36/0/36 "$in" 2>/dev/null; then
      echo 'crop=720:504:0:36' >&2
    fi
    exit 0 ;;
esac
if grep -q FAILME "$in" 2>/dev/null; then exit 3; fi
cat "$in" > "$out"
"""
    )
    (bin_dir / "vainfo").write_text(
        """#!/bin/sh
# classic entrypoint by default; $VAINFO_LP flips to low-power-only (mobile
# APUs expose encode only as VAEntrypointEncSliceLP)
if [ "$VAINFO_LP" = "1" ]; then
  printf '%s\\n' '      VAProfileHEVCMain               :      VAEntrypointEncSliceLP'
else
  printf '%s\\n' '      VAProfileHEVCMain               :      VAEntrypointEncSlice'
fi
"""
    )
    for f in bin_dir.iterdir():
        f.chmod(0o755)

    monkeypatch.setenv("VIDEOS_MOUNT", str(vid))
    monkeypatch.setenv("VIDEO_WORK_DIR", str(work))
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.chdir(tmp_path)
    # _find_tool prefers /usr/bin on the real box (the mise shim bleed); the
    # tests must resolve the fixture's fakes on PATH instead
    monkeypatch.setattr(transcode, "_find_tool", lambda exe: shutil.which(exe))
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


# -- default workdir -------------------------------------------------------------

def test_workdir_defaults_to_cache_skive(env, monkeypatch):
    """No -w: work goes to ~/.cache/skive on disk, never $TMPDIR (tmpfs is
    RAM; a staged source plus its encode output died on "Disk quota
    exceeded"), and the dir is empty once the item ships."""
    home = env / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("VIDEO_WORK_DIR", raising=False)
    vid = env / "vid"
    mk(vid, "rip/movies/Cache (2002)/d0/Cache (2002)_t00.mkv", "MAIN")

    rc = transcode.run_transcode(Args())

    cache = home / ".cache" / "skive"
    assert rc == 0
    assert cache.is_dir()
    assert list(cache.iterdir()) == []  # cleaned after the ship


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
    title files as an extra beside it (slice 05's extras arm)."""
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
    entry = {"file": "Mamma Mia! (2008).mkv", "src": "t21", "dur_s": 6251,
             "crop": None, "size": "1920x1080"}
    assert json.loads((L / "validation.json").read_text())["encodes"] == [entry]
    staged = vid / "rip" / "movies" / "Mamma Mia! (2008)" / "validation.json"
    assert json.loads(staged.read_text())["encodes"] == [entry]
    # the shipped main's source is deleted; the unmarked byte-winner rides
    # the main's disposal (Extras marker) -- no extras file, staging emptied
    assert not (vid / "rip" / "movies" / "Mamma Mia! (2008)" / "Mamma Mia! (2008)_t21.mkv").exists()
    assert not (vid / "rip" / "movies" / "Mamma Mia! (2008)" / "Mamma Mia! (2008)_t12.mkv").exists()
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
        {"file": "Shorts (2001).mkv", "src": "t12", "dur_s": 5580,
         "crop": None, "size": "1920x1080"}]
    # no doc ever existed for the legacy folder -> no library copy
    assert not (L / "Legacy (2002)" / "validation.json").exists()
    # the unmarked non-main title rode the main's disposal (Extras marker)
    assert not (vid / "rip" / "movies" / "Shorts (2001)" / "Shorts (2001)_t21.mkv").exists()
    assert not (L / "Shorts (2001)" / "extras").exists()


# -- split main merge (slice 05): ordering, guard, mkvmerge failure -----------

def test_merge_orders_nosuffix_first_then_parts(env):
    """Windows test_merge_orders_nosuffix_first_then_parts: a two-part main
    with matching stream signatures ships as one merged file, the unsuffixed
    title first (rescues a forgotten --part on disc 1), then the .pN parts
    ascending."""
    vid = env / "vid"
    mk(vid, "rip/movies/Multi (2003)/d1/Multi (2003)_t00.mkv", "A-NOSUFFIX")
    mk(vid, "rip/movies/Multi (2003)/d1/Multi (2003)_t00.p2.mkv", "B-P2")
    mk(vid, "rip/movies/Multi (2003)/d1/Multi (2003)_t00.p1.mkv", "C-P1")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    assert (vid / "library" / "movies" / "Multi (2003)" / "Multi (2003).mkv").read_text() == "A-NOSUFFIXC-P1B-P2"


def test_merge_failure_leaves_sources_and_continues(env, capsys):
    """Windows test_merge_failure_leaves_sources_and_continues: an mkvmerge
    failure (rc 2) logs FAILED mkvmerge, leaves the parts in staging, fails
    the run — and the batch continues with the next movie."""
    vid = env / "vid"
    mk(vid, "rip/movies/Bad (2001)/d1/Bad (2001)_t00.p1.mkv", "BADMERGE-P1")
    mk(vid, "rip/movies/Bad (2001)/d1/Bad (2001)_t00.p2.mkv", "BADMERGE-P2")
    mk(vid, "rip/movies/Good (2002)/d1/Good (2002)_t00.mkv", "GOOD")

    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err

    assert rc == 1
    assert "FAILED mkvmerge" in err
    assert sorted(p.name for p in (vid / "rip" / "movies" / "Bad (2001)" / "d1").iterdir()) == \
        ["Bad (2001)_t00.p1.mkv", "Bad (2001)_t00.p2.mkv"]
    assert (vid / "library" / "movies" / "Good (2002)" / "Good (2002).mkv").read_text() == "GOOD"


def test_guard_mismatch_quarantines_all_parts_no_encode(env, capsys):
    """Windows test_guard_mismatch_quarantines_all_parts_no_encode: a part set
    whose stream signatures differ never merges or encodes — all parts
    quarantine to issues/movies with the run's evidence, the batch continues,
    exit non-zero. (Windows prints "FAILED (track guard)" itself; skive's
    log.note tee carries the FAIL words to stderr instead.)"""
    vid = env / "vid"
    mk(vid, "rip/movies/Two (2001)/d1/Two (2001)_t00.p1.mkv", "PART-ONE")
    mk(vid, "rip/movies/Two (2001)/d1/Two (2001)_t00.p2.mkv", "TRACKS-DIFFER")
    mk(vid, "rip/movies/Next (2002)/d1/Next (2002)_t00.mkv", "NEXT")

    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err

    assert rc == 1
    issues = vid / "issues" / "movies" / "Two (2001)"
    assert sorted(p.name for p in issues.glob("*.mkv")) == \
        ["Two (2001)_t00.p1.mkv", "Two (2001)_t00.p2.mkv"]
    assert (issues / "rip.jsonl").exists()  # the run's evidence rides along
    assert sorted(p.name for p in (vid / "rip" / "movies" / "Two (2001)" / "d1").iterdir()) == []
    assert not (vid / "library" / "movies" / "Two (2001)").exists()
    assert (vid / "library" / "movies" / "Next (2002)" / "Next (2002).mkv").read_text() == "NEXT"
    assert "FAIL tracks differ" in err
    log = " ".join(l["status"] for l in run_log(env))
    assert "FAIL tracks differ" in log and "p2" in log
    hb = (env / "hb-calls.log").read_text()
    assert len(hb.strip().splitlines()) == 1  # only Next encoded


def test_guard_quarantine_carries_run_evidence(env):
    """Windows test_guard_quarantine_carries_run_evidence: a quarantined
    item's issues folder holds the run's rip.jsonl lines (selected by the run
    field) and a copy of its raw log."""
    vid = env / "vid"
    # Next encodes first, so the run's raw log exists when the later guard
    # quarantines Two's parts — the copy carries the whole run's evidence.
    mk(vid, "rip/movies/Next (2002)/d1/Next (2002)_t00.mkv", "NEXT")
    mk(vid, "rip/movies/Two (2001)/d1/Two (2001)_t00.p1.mkv", "PART-ONE")
    mk(vid, "rip/movies/Two (2001)/d1/Two (2001)_t00.p2.mkv", "TRACKS-DIFFER")

    rc = transcode.run_transcode(Args())

    assert rc == 1
    folder = vid / "issues" / "movies" / "Two (2001)"
    runs = {json.loads(l)["run"] for l in (vid / "logs" / "rip.jsonl").read_text().splitlines()}
    assert len(runs) == 1
    run = runs.pop()
    ev_lines = [json.loads(l) for l in (folder / "rip.jsonl").read_text().splitlines()]
    assert ev_lines and all(l["run"] == run for l in ev_lines)
    assert any("FAIL tracks differ" in l["status"] for l in ev_lines)
    assert any(l["phase"] == "encode" for l in ev_lines)  # Next's shipped phase
    # both parts quarantine into the same folder — each evidence line exactly
    # once, not once per part (and only the run's own raw-log copy beside them)
    assert len(ev_lines) == len({json.dumps(l, sort_keys=True) for l in ev_lines})
    assert {p.name for p in folder.glob("*.jsonl")} == {"rip.jsonl", f"{run}.jsonl"}
    # the run failed, so its raw log exists in logs/raw/ AND is copied in
    raw = vid / "logs" / "raw" / f"{run}.jsonl"
    assert raw.exists()
    assert (folder / f"{run}.jsonl").read_text() == raw.read_text()


def test_guard_ffprobe_failure_quarantines(env):
    """Windows test_guard_ffprobe_failure_quarantines: a part ffprobe cannot
    read counts as a signature mismatch — the parts quarantine, nothing
    encodes."""
    vid = env / "vid"
    mk(vid, "rip/movies/Rip (2001)/d1/Rip (2001)_t00.p1.mkv", "PROBEBAD")
    mk(vid, "rip/movies/Rip (2001)/d1/Rip (2001)_t00.p2.mkv", "PROBEBAD")

    rc = transcode.run_transcode(Args())

    assert rc == 1
    assert sorted(p.name for p in (vid / "issues" / "movies" / "Rip (2001)").glob("*.mkv")) == \
        ["Rip (2001)_t00.p1.mkv", "Rip (2001)_t00.p2.mkv"]
    assert not (env / "hb-calls.log").exists()


def test_guard_match_still_merges_and_passes_merged_file(env):
    """Windows test_guard_match_still_merges_and_passes_merged_file: matching
    signatures merge, and the inline subtitle pass runs ONCE on the merged
    file, never per part."""
    vid = env / "vid"
    mk(vid, "rip/movies/Merge (2003)/d1/Merge (2003)_t00.p1.mkv", "SUBS-ENG-DAN")
    mk(vid, "rip/movies/Merge (2003)/d1/Merge (2003)_t00.p2.mkv", "SUBS-ENG-DAN")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    L = vid / "library" / "movies" / "Merge (2003)"
    assert (L / "Merge (2003).mkv").read_text() == "SUBS-ENG-DANSUBS-ENG-DAN"
    assert (L / "Merge (2003).dan.forced.srt").exists()
    assert (L / "Merge (2003).eng.srt").exists()
    # the pass ran once, on the merged file, never per part
    sc = (env / "seconv-calls.log").read_text().strip().splitlines()
    assert len(sc) == 2 and all("merged.mkv" in l for l in sc)
    assert sorted(p.name for p in (vid / "rip" / "movies" / "Merge (2003)" / "d1").iterdir()) == []


def test_mkvmerge_output_captured_and_replayed_on_failure(env, capsys):
    """Windows test_mkvmerge_output_captured_and_replayed_on_failure: every
    mkvmerge line reaches the run's raw log (tool "mkvmerge", stderr
    included); the console hides them at the default verbosity and replays
    them verbatim because the merge failed."""
    vid = env / "vid"
    (env / "bin" / "mkvmerge").write_text("""#!/bin/sh
out="$2"; shift 2
echo "mkvmerge v99.0"
echo "Progress: 50%"
echo "Error: the source is broken" >&2
rm -f "$out"
exit 2
""")
    mk(vid, "rip/movies/Bad (2001)/d0/Bad (2001)_t00.p1.mkv", "BAD-P1")
    mk(vid, "rip/movies/Bad (2001)/d0/Bad (2001)_t00.p2.mkv", "BAD-P2")

    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err

    assert rc == 1
    # failure replay: hidden mkvmerge output reaches the console verbatim
    assert "mkvmerge v99.0" in err
    assert "Progress: 50%" in err
    assert "Error: the source is broken" in err
    raws = list((vid / "logs" / "raw").glob("*.jsonl"))
    assert len(raws) == 1  # failure keeps the run's raw log
    lines = [json.loads(l) for l in raws[0].read_text(encoding="utf-8").splitlines()]
    mkv = [l for l in lines if l["tool"] == "mkvmerge"]
    assert [l["line"] for l in mkv] == \
        ["mkvmerge v99.0", "Progress: 50%", "Error: the source is broken"]


def test_interrupt_drops_part_copies_in_multi_part_copy_window(env, capsys, monkeypatch):
    """Windows test_interrupt_drops_part_copies_in_multi_part_copy_window:
    Ctrl+C during a split main's part copies drops the local copies registered
    in `inflight` AS CREATED (the completed part-1 copy plus the partial
    part-2 copy); the sources stay in staging, exit 130."""
    vid = env / "vid"
    mk(vid, "rip/movies/Slit (2004)/d1/Slit (2004)_t00.p1.mkv", "SLIT-P1")
    mk(vid, "rip/movies/Slit (2004)/d2/Slit (2004)_t00.p2.mkv", "SLIT-P2")
    work = pathlib.Path(os.environ["VIDEO_WORK_DIR"])

    real_copy2 = transcode._copy2

    def fake_copy2(src, dst):
        if pathlib.Path(dst).name.endswith(".p2.mkv"):
            pathlib.Path(dst).write_text("PARTIAL-COPY")  # half-copied second part
            raise Interrupted()
        real_copy2(src, dst)

    monkeypatch.setattr(transcode, "_copy2", fake_copy2)

    rc = transcode.run_transcode(Args())

    assert rc == 130
    assert (vid / "rip" / "movies" / "Slit (2004)" / "d1" / "Slit (2004)_t00.p1.mkv").exists()
    assert (vid / "rip" / "movies" / "Slit (2004)" / "d2" / "Slit (2004)_t00.p2.mkv").exists()
    assert list(work.iterdir()) == []  # part-1 local copy + partial part-2 dropped
    log = run_log(env)
    assert log[-1]["phase"] == "total" and log[-1]["status"] == "interrupted"
    err = capsys.readouterr().err
    # the summary stays in scrollback: the in-flight item reads interrupted,
    # no --keep banner (nothing parked), and the result line closes the run
    row = [l for l in err.splitlines() if l.strip().startswith("Slit (2004)")]
    assert len(row) == 1 and row[0].rstrip().endswith("interrupted")
    assert "--keep:" not in err
    assert re.search(r"fail  interrupted · 0 ok · \d+s — partial deleted\n$", err)


def test_recorded_main_parts_still_merge(env):
    """Windows test_recorded_main_parts_still_merge: the recorded main's .pN
    parts merge under the stream-signature guard; one encodes entry records
    the merged encode."""
    vid = env / "vid"
    mk(vid, "rip/movies/Parts (2007)/Parts (2007)_t21.p1.mkv", "P1-DUR-6251")
    mk(vid, "rip/movies/Parts (2007)/Parts (2007)_t21.p2.mkv", "P2")
    mk(vid, "rip/movies/Parts (2007)/validation.json", _movie_doc("t21"))

    rc = transcode.run_transcode(Args())

    assert rc == 0
    L = vid / "library" / "movies" / "Parts (2007)"
    assert (L / "Parts (2007).mkv").read_text() == "P1-DUR-6251P2"
    assert json.loads((L / "validation.json").read_text())["encodes"] == [
        {"file": "Parts (2007).mkv", "src": "t21", "dur_s": 6251,
         "crop": None, "size": "1920x1080"}]


# -- inline subtitle pass on mains (slice 03) -------------------------------------

def test_pass_success_sidecars_beside_encode_and_archived(env):
    """Windows test_pass_success_sidecars_beside_encode_and_archived: the main
    ships zero-track with its OCR'd sidecars beside the encode (forced dan
    always kept, full eng shipped), the source archives to lossless with -k
    only after that verified ship, and no temp dirs survive."""
    vid = env / "vid"
    mk(vid, "rip/movies/Side (2001)/d0/Side (2001)_t00.mkv", "SUBS-ENG-DAN")

    rc = transcode.run_transcode(Args(keep=True))

    assert rc == 0
    L = vid / "library" / "movies" / "Side (2001)"
    assert (L / "Side (2001).mkv").read_text() == "SUBS-ENG-DAN"
    assert (L / "Side (2001).dan.forced.srt").read_text().count(" --> ") == 1
    assert (L / "Side (2001).eng.srt").exists()
    assert sorted(p.name for p in (vid / "lossless" / "movies" / "Side (2001)").iterdir()) == ["Side (2001)_t00.mkv"]
    assert len((env / "seconv-calls.log").read_text().strip().splitlines()) == 2
    assert list((env / "work").iterdir()) == []  # sidecar temp dir cleaned up


def test_pass_settles_full_track_dupes_to_sdh(env):
    """The settlement triple (tests/test_subs.py's run_pass settlement, through
    the transcode flow): a forced eng track always keeps .eng.forced.srt while
    the two full eng tracks settle by OCR'd cue count into .eng.srt (winner)
    and .eng.sdh.srt (>=30% runner-up) beside the encode."""
    vid = env / "vid"
    mk(vid, "rip/movies/Settle (2004)/d0/Settle (2004)_t00.mkv", "SUBS-SETTLE")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    L = vid / "library" / "movies" / "Settle (2004)"
    assert (L / "Settle (2004).mkv").read_text() == "SUBS-SETTLE"
    assert sorted(p.name for p in L.glob("*.srt")) == [
        "Settle (2004).eng.forced.srt", "Settle (2004).eng.sdh.srt",
        "Settle (2004).eng.srt"]
    assert (L / "Settle (2004).eng.srt").read_text().count(" --> ") == 1
    assert len((env / "seconv-calls.log").read_text().strip().splitlines()) == 3


def test_pass_failure_ships_encode_and_quarantines_source(env, capsys):
    """Windows test_pass_failure_ships_encode_and_quarantines_source, plus the
    batch contract: a failing OCR track ships the encode subless, parks the
    source in issues/movies/<Name (Year)> with the run's rip.jsonl lines and
    raw log, never archives despite -k, fails the run (exit non-zero) — and
    the next staged movie still encodes."""
    vid = env / "vid"
    # After encodes first, so the run's raw log is final when Ocr's quarantine
    # copies it as evidence (same ordering trick as the windows guard test).
    mk(vid, "rip/movies/After (2002)/d0/After (2002)_t00.mkv", "AFTER-MAIN")
    mk(vid, "rip/movies/Ocr (2001)/d0/Ocr (2001)_t00.mkv", "SUBS-FAIL")

    rc = transcode.run_transcode(Args(keep=True))
    err = capsys.readouterr().err

    assert rc == 1
    assert (vid / "library" / "movies" / "Ocr (2001)" / "Ocr (2001).mkv").read_text() == "SUBS-FAIL"
    assert not (vid / "lossless" / "movies" / "Ocr (2001)").exists()  # never archived despite -k
    issues = vid / "issues" / "movies" / "Ocr (2001)"
    assert (issues / "Ocr (2001)_t00.mkv").read_text() == "SUBS-FAIL"
    assert "FAILED (subtitle pass)" in err
    log = " ".join(l["status"] for l in run_log(env))
    assert "FAIL subtitle pass" in log and "seconv failed" in log
    # the quarantined item carries the run's evidence: rip.jsonl lines and the
    # raw log (the encode ran, so a raw log exists and is kept by the failure)
    runs = {json.loads(l)["run"] for l in (vid / "logs" / "rip.jsonl").read_text().splitlines()}
    assert len(runs) == 1
    run = runs.pop()
    ev = [json.loads(l) for l in (issues / "rip.jsonl").read_text().splitlines()]
    assert ev and all(l["run"] == run for l in ev)
    assert (issues / f"{run}.jsonl").read_text() == (vid / "logs" / "raw" / f"{run}.jsonl").read_text()
    # batch continued: the second movie shipped (and archived, -k)
    assert (vid / "library" / "movies" / "After (2002)" / "After (2002).mkv").read_text() == "AFTER-MAIN"
    assert not (vid / "rip" / "movies" / "After (2002)" / "d0" / "After (2002)_t00.mkv").exists()
    assert (vid / "lossless" / "movies" / "After (2002)" / "After (2002)_t00.mkv").exists()


def test_pass_empty_sidecar_drops_track_as_barren(env, capsys):
    """The barren drop path through the transcode flow (tests/test_subs.py's
    test_run_pass_empty_sidecar_drops_track_as_barren): a zero-cue sidecar is
    a barren track -- the encode ships, no sidecar, the barren note on the
    console, no issues folder, run exits 0."""
    vid = env / "vid"
    mk(vid, "rip/movies/Blank (2006)/d0/Blank (2006)_t00.mkv", "SUBS-EMPTY")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    L = vid / "library" / "movies" / "Blank (2006)"
    assert (L / "Blank (2006).mkv").read_text() == "SUBS-EMPTY"
    assert not list(L.glob("*.srt"))  # the barren sidecar never shipped
    assert not (vid / "issues" / "movies" / "Blank (2006)").exists()
    # the barren note prints to the console (movies run _sub_pass directly;
    # the rip.jsonl subs-phase barren wiring is slice 03)
    assert "barren eng (track 2): OCR yielded 0 cues -- dropped" in capsys.readouterr().err


def test_pass_barren_seconv_failure_drops_track(env, capsys):
    """seconv rc=1 'No subtitle tracks matched' through the transcode flow:
    same barren drop -- encode ships, barren note on console, no issues, run
    exits 0."""
    vid = env / "vid"
    mk(vid, "rip/movies/Fallow (2007)/d0/Fallow (2007)_t00.mkv", "SUBS-BARREN")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    L = vid / "library" / "movies" / "Fallow (2007)"
    assert (L / "Fallow (2007).mkv").read_text() == "SUBS-BARREN"
    assert not list(L.glob("*.srt"))
    assert not (vid / "issues" / "movies" / "Fallow (2007)").exists()
    assert "barren eng (track 2): OCR yielded 0 cues -- dropped" in capsys.readouterr().err


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
        r"--audio-copy-mask aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac "
        r"--audio-fallback ac3 --subtitle-lang-list zzz --all-subtitles --subtitle-burned=none$",
        line)
    assert "--encoder" not in line  # default x265 passes no --encoder flag
    assert "--quality" not in line


def test_x265_letterboxed_main_encodes_cropped(env):
    """A letterboxed main on the x265 path: the detected crop reaches
    HandBrakeCLI as custom --crop in HandBrake's t:b:l:r order, the preset is
    picked from the CROPPED height (576 - 72 - 72 = 432 -> 480p30), and the
    validation encodes entry carries the applied crop."""
    vid = env / "vid"
    _mk_main(vid, "Bars (2001)", "LETTERBOX-720x576 BARS-0/72/0/72")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    dest = vid / "library" / "movies" / "Bars (2001)" / "Bars (2001).mkv"
    assert dest.read_text() == "LETTERBOX-720x576 BARS-0/72/0/72"
    line = (env / "hb-calls.log").read_text().strip().splitlines()[0]
    assert "--crop 72:72:0:0" in line  # t:b:l:r from gate crop (l=0,t=72,r=0,b=72)
    assert "--preset H.265 MKV 480p30" in line
    det = [l for l in run_log(env) if l.get("phase") == "detect"]
    assert "crop 0/72/0/72 -> 720x432" in det[0]["status"]
    lib_doc = json.loads((vid / "library" / "movies" / "Bars (2001)" /
                          "validation.json").read_text())
    assert lib_doc["encodes"][0]["crop"] == "0/72/0/72"


def test_x265_unstable_bars_refuse_and_ship_uncropped(env):
    """A gate refusal on the x265 path: no --crop in the HandBrake argv, the
    preset comes from the FULL probed height, the encode proceeds, and the
    detect line carries the refusal note."""
    vid = env / "vid"
    _mk_main(vid, "Wobbly (2002)", "LETTERBOX-720x576 BARS-UNSTABLE")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    line = (env / "hb-calls.log").read_text().strip().splitlines()[0]
    assert "--crop" not in line
    assert "--preset H.265 MKV 576p25" in line  # full 576, not the cropped 432
    det = [l for l in run_log(env) if l.get("phase") == "detect"]
    assert "no crop --" in det[0]["status"]


def test_vaapi_flags(env):
    """-e vaapi encodes through ffmpeg (HandBrake is never invoked): hevc_vaapi
    CQP 24, the bwdif/width-crop/hwupload chain, audio stream-copied, subs
    dropped for features (the no-subtitle trick: no 0:s mapping)."""
    vid = env / "vid"
    mk(vid, "rip/movies/Gpu (2009)/d0/Gpu (2009)_t00.mkv", "GPU-MAIN")

    rc = transcode.run_transcode(Args(encoder="vaapi"))

    assert rc == 0
    assert (vid / "library" / "movies" / "Gpu (2009)" / "Gpu (2009).mkv").read_text() == "GPU-MAIN"
    assert not (env / "hb-calls.log").exists()
    ff = (env / "ffmpeg-calls.log").read_text()
    assert "-c:v hevc_vaapi -rc_mode CQP -global_quality 24" in ff
    assert ("bwdif=mode=send_frame:parity=auto:deint=interlaced,"
            "crop=floor(iw/64)*64:ih,format=nv12|p010,hwupload") in ff
    assert "-map 0:a? -c:a copy" in ff
    assert "-map 0:s?" not in ff   # features encode subless (sidecars carry subs)
    assert "-low_power" not in ff  # classic entrypoint needs no low_power


def test_vaapi_low_power_entrypoint(env, monkeypatch):
    """A VCN that exposes encode only as VAEntrypointEncSliceLP (mobile APUs)
    gets -low_power 1 -- ffmpeg cannot reach the LP encoder without it."""
    vid = env / "vid"
    mk(vid, "rip/movies/Gpu (2009)/d0/Gpu (2009)_t00.mkv", "GPU-MAIN")
    monkeypatch.setenv("VAINFO_LP", "1")

    rc = transcode.run_transcode(Args(encoder="vaapi"))

    assert rc == 0
    assert "-low_power 1" in (env / "ffmpeg-calls.log").read_text()


# -- black-bar crop detection (the crop gate, ported from the trial oracle) ------


def test_gate_matches_trial_self_check():
    """The ported gate returns the trial script's self-check verdicts exactly
    (scripts/crop_detect_test.py --self-check is the oracle)."""
    cases = [  # (readings, width, height, expected crop or None)
        ([(0, 0, 0, 0)] * 6, 1024, 576, None),              # clean frame
        ([(0, 70, 0, 70)] * 6, 1024, 576, (0, 70, 0, 70)),  # plain letterbox
        ([(8, 0, 8, 0)] * 6, 1024, 576, None),              # under 16 px = noise
        ([(0, 200, 0, 200)] * 6, 1024, 576, None),          # 69% of height = error
        ([(70, 0, 70, 0), (70, 0, 70, 0), (0, 0, 0, 0),
          (70, 0, 70, 0), (70, 0, 70, 0), (70, 0, 70, 0)],
         1024, 576, (70, 0, 70, 0)),                      # lone dissent absorbed
        ([(70, 0, 70, 0)] * 3 + [(0, 0, 0, 0)] * 3,
         1024, 576, None),                                # half dissent = mixed AR
        ([(20, 0, 40, 0)] * 6, 1024, 576, (20, 0, 40, 0)),  # asymmetric pillarbox
    ]
    for readings, w, h, want in cases:
        crop, _reason, _detail = transcode._gate(readings, w, h)
        assert crop == want


def _mk_main(vid, movie, content):
    mk(vid, f"rip/movies/{movie}/d0/{movie}_t00.mkv", content)
    mk(vid, f"rip/movies/{movie}/validation.json", _movie_doc("t00"))


def test_vaapi_letterboxed_main_encodes_cropped(env):
    """A letterboxed main on the vaapi path: the detect phase runs between
    copy and encode, the bar crop enters the vf chain before the 64-px
    alignment crop, and the validation encodes entry carries crop + size."""
    vid = env / "vid"
    _mk_main(vid, "Bars (2001)", "LETTERBOX-720x576 BARS-0/72/0/72")

    rc = transcode.run_transcode(Args(encoder="vaapi"))

    assert rc == 0
    dest = vid / "library" / "movies" / "Bars (2001)" / "Bars (2001).mkv"
    assert dest.read_text() == "LETTERBOX-720x576 BARS-0/72/0/72"
    ff = (env / "ffmpeg-calls.log").read_text()
    # the cropdetect probes also carry a -vf; the encode call is the last one
    vf = re.findall(r"-vf (\S+)", ff)[-1]
    bar = vf.index("crop=iw-0-0:ih-72-72:0:72")  # 720x576 minus 72+72 bottom/top
    align = vf.index("crop=floor(iw/64)*64:ih")
    assert bar < align  # the bar crop rides BEFORE the 64-px alignment crop
    assert "bwdif=mode=send_frame:parity=auto:deint=interlaced," in vf
    # run log: copy -> detect -> encode, and the detect line carries the crop
    lines = run_log(env)
    order = [l.get("phase") for l in lines]
    # a run-start note reuses phase "encode", so compare the item's tail
    assert order.index("copy") < order.index("subs") \
        < order.index("detect") < len(order) - order[::-1].index("encode") - 1
    det = [l for l in lines if l.get("phase") == "detect"]
    assert "crop 0/72/0/72 -> 720x432" in det[0]["status"]
    # the validation encodes entry carries the applied crop and the geometry
    lib_doc = json.loads((vid / "library" / "movies" / "Bars (2001)" /
                          "validation.json").read_text())
    entry = lib_doc["encodes"][0]
    assert entry["crop"] == "0/72/0/72"
    assert entry["size"] == "720x576"
    assert entry["src"] == "t0"  # f"t{num}", the pre-existing record shape


def test_vaapi_unstable_bars_refuse_and_ship_uncropped(env):
    """Too many dissenting probes (a mixed-AR read): no bar crop in the vf
    chain, the loud refusal note lands in the run log, and the encode ships
    uncropped with a clean exit."""
    vid = env / "vid"
    _mk_main(vid, "Wobbly (2002)", "LETTERBOX-720x576 BARS-UNSTABLE")

    rc = transcode.run_transcode(Args(encoder="vaapi"))

    assert rc == 0
    assert (vid / "library" / "movies" / "Wobbly (2002)" /
            "Wobbly (2002).mkv").exists()
    ff = (env / "ffmpeg-calls.log").read_text()
    assert "crop=iw-" not in ff
    assert ("bwdif=mode=send_frame:parity=auto:deint=interlaced,"
            "crop=floor(iw/64)*64:ih,format=nv12|p010,hwupload") in ff
    lines = run_log(env)
    det = [l for l in lines if l.get("phase") == "detect"]
    assert "unstable left" in det[0]["status"]


def test_vaapi_tight_main_detection_failed_ships_uncropped(env):
    """No readable bars at all (a tight title): detection failed, loud note,
    encode ships uncropped, exit clean."""
    vid = env / "vid"
    _mk_main(vid, "Tight (2003)", "TIGHT-MAIN")

    rc = transcode.run_transcode(Args(encoder="vaapi"))

    assert rc == 0
    assert (vid / "library" / "movies" / "Tight (2003)" /
            "Tight (2003).mkv").read_text() == "TIGHT-MAIN"
    assert "crop=iw-" not in (env / "ffmpeg-calls.log").read_text()
    lines = run_log(env)
    det = [l for l in lines if l.get("phase") == "detect"]
    assert "detection failed" in det[0]["status"]


def test_detect_never_fails_the_item(env):
    """A corrupt/failed probe (ffprobe errors) still ships the encode: the
    refusal never fails the item."""
    vid = env / "vid"
    _mk_main(vid, "Badprobe (2004)", "PROBEFRAMEBAD BARS-0/72/0/72")

    rc = transcode.run_transcode(Args(encoder="vaapi"))

    assert rc == 0
    assert (vid / "library" / "movies" / "Badprobe (2004)" /
            "Badprobe (2004).mkv").exists()
    det = [l for l in run_log(env) if l.get("phase") == "detect"]
    assert "detection failed" in det[0]["status"]


def test_auto_resolves_to_vaapi(env):
    """-e auto (the default) picks the VCN when vainfo shows the entrypoint."""
    vid = env / "vid"
    mk(vid, "rip/movies/Gpu (2009)/d0/Gpu (2009)_t00.mkv", "GPU-MAIN")

    rc = transcode.run_transcode(Args(encoder="auto"))

    assert rc == 0
    assert "hevc_vaapi" in (env / "ffmpeg-calls.log").read_text()
    assert not (env / "hb-calls.log").exists()


def test_auto_falls_back_to_x265_fast(env, monkeypatch):
    """No VCN (the BC-250, or a box without the freeworld driver): auto falls
    back to x265 at the fast preset."""
    vid = env / "vid"
    mk(vid, "rip/movies/Gpu (2009)/d0/Gpu (2009)_t00.mkv", "GPU-MAIN")
    monkeypatch.setattr(transcode, "_vcn_probe", lambda: None)

    rc = transcode.run_transcode(Args(encoder="auto"))

    assert rc == 0
    # x265 now applies the crop gate too: detection runs (ffmpeg probes),
    # but no crop is staged here (GPU-MAIN has no bars) and what must stay
    # absent is the vaapi encode
    ff = (env / "ffmpeg-calls.log").read_text()
    assert "hevc_vaapi" not in ff  # fallback: never the vaapi encode
    line = (env / "hb-calls.log").read_text().strip().splitlines()[0]
    assert "--encoder-preset fast" in line
    assert "--crop" not in line


def test_vaapi_required_but_absent(env, monkeypatch):
    """Explicit -e vaapi on a box without VCN fails fast with a pointer to the
    fix instead of encoding anything at a different shape."""
    vid = env / "vid"
    mk(vid, "rip/movies/Gpu (2009)/d0/Gpu (2009)_t00.mkv", "GPU-MAIN")
    monkeypatch.setattr(transcode, "_vcn_probe", lambda: None)

    rc = transcode.run_transcode(Args(encoder="vaapi"))

    assert rc == 1


def test_encode_vaapi_footer_progress_from_ffmpeg_time(tmp_path, monkeypatch):
    """ffmpeg progress lines carry time=/speed=, not a percent or ETA: the
    TTY footer derives both from the input's Duration: banner line."""
    out = tmp_path / "out.mkv"
    out.write_bytes(b"x")
    lines = [
        "Input #0, matroska,webm, from 'in.mkv':\n",
        "  Duration: 01:40:00.00, start: 0.000000, bitrate: 6234 kb/s\n",
        "frame= 9000 fps=120 q=-0.0 size=  204800KiB time=00:25:00.00 "
        "bitrate=1118.5kbits/s speed=5.00x\n",
    ]

    class FakeProc:
        stdout = iter(lines)

        def wait(self):
            return 0

    monkeypatch.setattr(transcode, "_find_tool", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(transcode, "_vcn_probe", lambda: {"low_power": False})
    monkeypatch.setattr(transcode.subprocess, "Popen", lambda *a, **k: FakeProc())
    view = ui.RunView(tty=True, live=False)
    ui.set_view(view)
    try:
        assert transcode._encode_vaapi("in.mkv", str(out))
    finally:
        ui.clear_view()

    rendered = view.render()
    # 25 of 100 minutes; 75 minutes left at 5x = 15 minutes
    assert "25%" in rendered and "ETA 00h15m00s" in rendered


def test_dvd_not_upscaled(env):
    vid = env / "vid"
    mk(vid, "rip/movies/Dvd (2001)/d0/Dvd (2001)_t00.mkv", "480")  # ffprobe stub sees "480" -> SD

    rc = transcode.run_transcode(Args())

    assert rc == 0
    hb = (env / "hb-calls.log").read_text()
    assert "--preset H.265 MKV 480p30" in hb
    assert "--detelecine" in hb and "--decomb" in hb


# -- movie extras (slice 05): rolled names, legacy labels, pass-through argv ---

def test_extra_number_rolls_past_taken_names(env):
    """Windows test_extra_number_rolls_past_taken_names: a bonus _t title files
    as ` - Extra NN` — its disc number when free, rolled forward when an
    existing Extra file already claims it; never a collision, never a staging
    label in the library."""
    vid = env / "vid"
    mk(vid, "rip/movies/Coll (2006)/d1/Coll (2006)_t00.mkv",
       "COLL-MAIN-BYTES-BYTES-BYTES")
    mk(vid, "rip/movies/Coll (2006)/d1/Coll (2006)_t03.mkv", "COLL-EXTRA")
    mk(vid, "rip/movies/Coll (2006)/validation.json",
       _movie_doc("t00", extras={"include": True, "quality": "superfast"}))
    ex = vid / "library" / "movies" / "Coll (2006)" / "extras"
    ex.mkdir(parents=True)
    (ex / "Coll (2006) - Extra 03.mkv").write_text("TAKEN")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    assert (ex / "Coll (2006) - Extra 03.mkv").read_text() == "TAKEN"
    assert (ex / "Coll (2006) - Extra 04.mkv").read_text() == "COLL-EXTRA"


def test_legacy_eNN_extras_still_file(env):
    """Windows test_legacy_eNN_extras_still_file: old _eNN staged names still
    parse and file under extras/ (backward compat)."""
    vid = env / "vid"
    mk(vid, "rip/movies/Old (2001)/d0/Old (2001)_e02.mkv", "OLD-EXTRA")
    mk(vid, "rip/movies/Old (2001)/validation.json",
       _movie_doc(None, extras={"include": True, "quality": "superfast"}))

    rc = transcode.run_transcode(Args())

    assert rc == 0
    assert (vid / "library" / "movies" / "Old (2001)" / "extras" /
            "Old (2001) - Extra 02.mkv").read_text() == "OLD-EXTRA"


_ARGV_TAIL = ('--preset H.265 MKV 1080p30 --all-audio --aencoder copy '
              '--audio-copy-mask aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac '
              '--audio-fallback ac3 ')


def test_feature_argv_zero_subs_and_extras_argv_byte_identical(env):
    """Windows test_feature_argv_zero_subs_and_extras_argv_byte_identical:
    the feature's HandBrake argv rides the no-match language list in front of
    --all-subtitles (zero subtitle tracks) while an extra's argv is the bare
    pass-through (every embedded track stays) — byte for byte, mod -i/-o
    paths."""
    vid = env / "vid"
    mk(vid, "rip/movies/Arg (2001)/d0/Arg (2001)_t00.mkv", "ARG-MAIN-BYTES")  # outweighs t01 so t00 is the main
    mk(vid, "rip/movies/Arg (2001)/d0/Arg (2001)_t01.mkv", "ARG-EXTRA")
    mk(vid, "rip/movies/Arg (2001)/validation.json",
       _movie_doc("t00", extras={"include": True, "quality": "superfast"}))

    rc = transcode.run_transcode(Args())

    assert rc == 0
    lines = (env / "hb-calls.log").read_text().strip().splitlines()
    assert len(lines) == 2
    feat = next(l for l in lines if "_t00.h265.mkv" in l)
    extra = next(l for l in lines if "_t01.h265.mkv" in l)
    # feature: the no-match language list rides in front of --all-subtitles
    assert feat.split(".h265.mkv ", 1)[1] == _ARGV_TAIL + \
        "--subtitle-lang-list zzz --all-subtitles --subtitle-burned=none"
    # extra: byte-identical to the pass-through argv, plus the superfast
    # encoder preset the marker asked for (Extras marker)
    assert extra.split(".h265.mkv ", 1)[1] == \
        _ARGV_TAIL + "--all-subtitles --subtitle-burned=none --encoder-preset superfast"


# -- -k keep + --retry (slice 06): source disposition -------------------------

def test_scenario2_keep_archive(env, capsys):
    """Windows test_scenario2_keep_archive: -k parks the verified lossless
    sources (main and extras alike) under lossless/<type>/<Name (Year)>/
    with rip/ staging emptied; the encode still ships normally."""
    vid = env / "vid"
    mk(vid, "rip/movies/Keep (2005)/d1/Keep (2005)_t00.mkv", "KEEP-MAIN")
    mk(vid, "rip/movies/Keep (2005)/d2/Keep (2005) - Extra 01.p2.mkv", "KEEP-EXTRA-P2")

    rc = transcode.run_transcode(Args(keep=True))
    err = capsys.readouterr().err

    assert rc == 0
    assert not list((vid / "rip" / "movies" / "Keep (2005)" / "d1").iterdir())  # sources moved out
    assert sorted(p.name for p in (vid / "lossless" / "movies" / "Keep (2005)").iterdir()) == \
        ["Keep (2005) - Extra 01.p2.mkv", "Keep (2005)_t00.mkv"]
    # the unmarked extra rode the main's disposal (Extras marker): parked,
    # never encoded
    assert not (vid / "library" / "movies" / "Keep (2005)" / "extras").exists()
    assert "--keep: lossless sources moved to" in err


def test_keep_clobber_refusal(env, capsys):
    """Windows test_keep_clobber_refusal: an existing lossless target is
    refused, never overwritten — the source stays in staging."""
    vid = env / "vid"
    mk(vid, "rip/movies/Keep (2005)/d1/Keep (2005)_t00.mkv", "KEEP-MAIN")
    done = vid / "lossless" / "movies" / "Keep (2005)"
    done.mkdir(parents=True)
    (done / "Keep (2005)_t00.mkv").write_text("EXISTING")

    rc = transcode.run_transcode(Args(keep=True))
    err = capsys.readouterr().err

    assert rc == 0
    assert "already exists -- source left in" in err
    assert (vid / "rip" / "movies" / "Keep (2005)" / "d1" / "Keep (2005)_t00.mkv").exists()
    assert (done / "Keep (2005)_t00.mkv").read_text() == "EXISTING"


def test_retry_success_ships_and_empties_issues(env):
    """Windows test_retry_success_ships_and_empties_issues: --retry scans
    issues/ as its input tree; success ships to the normal library
    destination and the source leaves issues/ (moved out only on success)."""
    vid = env / "vid"
    mk(vid, "issues/movies/Heal (2002)/Heal (2002)_t00.mkv", "HEAL-MAIN")

    rc = transcode.run_transcode(Args(retry=True))

    assert rc == 0
    assert (vid / "library" / "movies" / "Heal (2002)" / "Heal (2002).mkv").read_text() == "HEAL-MAIN"
    assert not list((vid / "issues" / "movies" / "Heal (2002)").glob("*.mkv"))


def test_retry_success_with_keep_archives_to_lossless(env):
    """Windows test_retry_success_with_keep_archives_to_lossless: a --retry
    success under -k parks the source in lossless/, never back into
    issues/."""
    vid = env / "vid"
    mk(vid, "issues/tv/Ret Show (2003)/Ret Show (2003)_S01E01.mkv", "SUBS-ENG-DAN")

    rc = transcode.run_transcode(Args(retry=True, keep=True))

    assert rc == 0
    assert (vid / "library" / "tv" / "Ret Show (2003)" / "Season 01" /
            "Ret Show (2003) - S01E01.mkv").read_text() == "SUBS-ENG-DAN"
    assert sorted(p.name for p in (vid / "lossless" / "tv" / "Ret Show (2003)").iterdir()) == \
        ["Ret Show (2003)_S01E01.mkv"]
    assert not list((vid / "issues" / "tv" / "Ret Show (2003)").glob("*.mkv"))


def test_retry_failure_requarantines_in_place(env, capsys):
    """Windows test_retry_failure_requarantines_in_place: --retry on a
    still-failing item — the source never leaves its issues folder, the run
    fails, and the retried run's evidence lands beside the source like in
    every other arm."""
    vid = env / "vid"
    mk(vid, "issues/movies/Stuck (2004)/Stuck (2004)_t00.mkv", "FAILME")

    rc = transcode.run_transcode(Args(retry=True))
    err = capsys.readouterr().err

    assert rc == 1
    assert "FAILED handbrake" in err  # skive's log.note tee carries the FAIL words
    folder = vid / "issues" / "movies" / "Stuck (2004)"
    assert (folder / "Stuck (2004)_t00.mkv").read_text() == "FAILME"
    assert not list((vid / "library" / "movies" / "Stuck (2004)").glob("*.mkv"))  # nothing shipped
    # the handbrake arm copied the run's evidence beside the in-issues source:
    # the run's rip.jsonl lines (incl. the FAILED note) and the kept raw log
    runs = {json.loads(l)["run"] for l in (vid / "logs" / "rip.jsonl").read_text().splitlines()}
    assert len(runs) == 1
    run = runs.pop()
    ev = [json.loads(l) for l in (folder / "rip.jsonl").read_text().splitlines()]
    assert ev and all(l["run"] == run for l in ev)
    assert any("FAILED handbrake: Stuck (2004)_t00.mkv" in l["status"] for l in ev)
    assert (folder / f"{run}.jsonl").read_text() == (vid / "logs" / "raw" / f"{run}.jsonl").read_text()


def test_retry_subtitle_failure_leaves_source_in_issues(env, capsys):
    """Windows test_retry_subtitle_failure_leaves_source_in_issues: a
    --retry whose encode succeeds but whose subtitle pass fails ships the
    encode subless and re-quarantines the source in place with fresh
    evidence."""
    vid = env / "vid"
    mk(vid, "issues/movies/Ocr2 (2005)/Ocr2 (2005)_t00.mkv", "SUBS-FAIL")

    rc = transcode.run_transcode(Args(retry=True))
    err = capsys.readouterr().err

    assert rc == 1
    folder = vid / "issues" / "movies" / "Ocr2 (2005)"
    assert (folder / "Ocr2 (2005)_t00.mkv").read_text() == "SUBS-FAIL"
    assert "already exists -- source left in place" in err  # re-quarantine no-clobber
    # the retried run's evidence lands beside the source again
    assert (folder / "rip.jsonl").exists()


def test_retry_obeys_traveled_doc(env):
    """Windows test_retry_obeys_traveled_doc: a doc that traveled into
    issues/ beside the quarantined source still steers the --retry run:
    recorded main, encodes append, library copy."""
    vid = env / "vid"
    mk(vid, "issues/movies/Heal (2008)/Heal (2008)_t12.mkv", "HEAL-BIG-BYTES-BYTES")
    mk(vid, "issues/movies/Heal (2008)/Heal (2008)_t21.mkv", "HEAL-REAL")
    mk(vid, "issues/movies/Heal (2008)/validation.json", _movie_doc("t21"))

    rc = transcode.run_transcode(Args(retry=True))

    assert rc == 0
    L = vid / "library" / "movies" / "Heal (2008)"
    assert (L / "Heal (2008).mkv").read_text() == "HEAL-REAL"
    assert json.loads((L / "validation.json").read_text())["encodes"] == [
        {"file": "Heal (2008).mkv", "src": "t21", "dur_s": 5580,
         "crop": None, "size": "1920x1080"}]


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
    # a clean item logs one line per part (copy, subs), the detect phase,
    # the durable item-success event, and the run total
    assert [l["phase"] for l in lines] == ["-", "copy", "subs", "detect", "encode", "ok", "total"]
    assert re.match(r"^\d+ MB at \d+ MB/s$", lines[1]["status"])
    assert lines[2]["status"] == "0 sidecar(s)"
    assert re.match(r"^ok, \d+ MB$", lines[4]["status"])
    assert lines[-1]["phase"] == "total" and lines[-1]["status"] == "ok"
    assert lines[-1]["run"] == first["run"]
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


def test_encode_exit0_without_output_fails_like_encode_failure(env, tmp_path, capsys):
    """HandBrake exiting 0 but writing no output (a rejected job init, the
    Conan root cause) is a real failure: the console-suppressed output
    replays, the 'wrote no output' note lands on stderr, the source stays,
    the run exits 1, and no downstream FATAL happens."""
    (tmp_path / "bin" / "HandBrakeCLI").write_text(
        f"""#!/bin/sh
{{ printf 'ARGS:'; printf ' %s' "$@"; echo; }} >> "{tmp_path / 'hb-calls.log'}"
echo "hb: work initialization error"
exit 0
"""
    )
    vid = env / "vid"
    mk(vid, "rip/movies/Noout (2003)/d0/Noout (2003)_t00.mkv", "NOOUT")

    rc = transcode.run_transcode(Args())
    captured = capsys.readouterr()

    assert rc == 1
    assert "hb: work initialization error" in captured.err  # hidden lines replayed
    assert "handbrake exited 0 but wrote no output" in captured.err
    assert "FATAL" not in captured.err
    assert (vid / "rip" / "movies" / "Noout (2003)" / "d0" / "Noout (2003)_t00.mkv").exists()
    assert not (vid / "library" / "movies" / "Noout (2003)" / "Noout (2003).mkv").exists()


def test_progress_lines_throttled_and_never_replayed(env, tmp_path, capsys, monkeypatch):
    """At quiet verbosity HandBrake's 'Encoding: x%' progress updates print
    one a minute (fake emits three in quick succession, monotonic ticks are
    scripted 30 s apart), they never enter the failure replay, and the real
    error line does."""
    (tmp_path / "bin" / "HandBrakeCLI").write_text(
        """#!/bin/sh
echo "Encoding: title 10.00 %"
echo "Encoding: title 50.00 %"
echo "Encoding: title 90.00 %"
echo "ERROR: boom"
exit 3
"""
    )
    # pairs of (check, set-after-print) ticks: line 1 prints (1000), lines
    # 2 and 3 are within the 60 s window and drop
    ticks = iter([1000.0, 1000.0, 1030.0, 1030.0, 1060.0, 1060.0])
    monkeypatch.setattr(transcode.time, "monotonic", lambda: next(ticks, 1060.0))
    vid = env / "vid"
    mk(vid, "rip/movies/Slow (2004)/d0/Slow (2004)_t00.mkv", "SLOW")

    rc = transcode.run_transcode(Args())
    captured = capsys.readouterr()

    assert rc == 1
    assert captured.err.count("handbrake  Encoding:") == 1
    assert "ERROR: boom" in captured.err  # replayed on failure
    assert "FAILED handbrake" in captured.err


def test_progress_lines_all_print_at_vv(env, tmp_path, capsys):
    """At -vv every progress update prints (and the run still succeeds)."""
    (tmp_path / "bin" / "HandBrakeCLI").write_text(
        f"""#!/bin/sh
{{ printf 'ARGS:'; printf ' %s' "$@"; echo; }} >> "{tmp_path / 'hb-calls.log'}"
in=""; out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -i) in="$2"; shift 2 ;;
    -o) out="$2"; shift 2 ;;
    *)  shift ;;
  esac
done
echo "Encoding: title 10.00 %"
echo "Encoding: title 50.00 %"
echo "Encoding: title 90.00 %"
cat "$in" > "$out"
"""
    )
    vid = env / "vid"
    mk(vid, "rip/movies/Verbose (2005)/d0/Verbose (2005)_t00.mkv", "VERBOSE")

    rc = transcode.run_transcode(Args(verbose=2))
    captured = capsys.readouterr()

    assert rc == 0
    assert captured.err.count("handbrake  Encoding:") == 3
    assert (vid / "library" / "movies" / "Verbose (2005)" / "Verbose (2005).mkv").read_text() == "VERBOSE"


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
    enc = [l for l in log if l["phase"] == "encode"]
    assert enc[-1]["status"] == "interrupted" and enc[-1]["elapsed_s"] >= 0
    assert log[-1]["phase"] == "total" and log[-1]["status"] == "interrupted"
    err = capsys.readouterr().err
    row = [l for l in err.splitlines() if l.strip().startswith("Stop (2007)")]
    assert len(row) == 1 and "copy" not in row[0] and row[0].rstrip().endswith("interrupted")
    assert re.search(r"fail  interrupted · 0 ok · \d+s — partial deleted\n$", err)


def test_fatal_logs_note_and_closes_open_phase(env, capsys, monkeypatch):
    """An exception mid-encode: the FATAL lands in the run log as a note, the
    open encode phase closes FAIL with its elapsed, and the run still fails
    with rc 1 (exit behavior unchanged)."""
    vid = env / "vid"
    mk(vid, "rip/movies/Boom (1998)/d0/Boom (1998)_t00.mkv", "BOOM")

    def boom(src, out, *a, **k):
        raise RuntimeError("disc vanished")

    monkeypatch.setattr(transcode, "_encode", boom)

    rc = transcode.run_transcode(Args())

    assert rc == 1
    assert "FATAL: disc vanished" in capsys.readouterr().err
    log = run_log(env)
    assert any(l["phase"] == "fail" and l["status"] == "FATAL: disc vanished" for l in log)
    enc = [l for l in log if l["phase"] == "encode"]
    assert enc[-1]["status"] == "FAIL" and enc[-1]["elapsed_s"] >= 0
    assert log[-1]["phase"] == "total" and log[-1]["status"].startswith("FAIL")


def test_phase_lines_carry_statuses_and_item_summary(env, capsys):
    """A clean item logs one timed phase per part with its numbers -- copy
    throughput, the subs sidecar/barren tally, the encode size -- plus the
    per-item console summary and the batch ok/failed counts."""
    vid = env / "vid"
    mk(vid, "rip/movies/Marked (2009)/d0/Marked (2009)_t00.mkv", "SUBS-EMPTY")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    lines = run_log(env)
    assert [l["phase"] for l in lines] == ["-", "copy", "subs", "detect", "encode", "ok", "total"]
    by = {l["phase"]: l for l in lines[1:]}
    assert re.match(r"^\d+ MB at \d+ MB/s$", by["copy"]["status"])
    assert by["subs"]["status"] == "0 sidecar(s), 1 barren"
    assert re.match(r"^ok, \d+ MB$", by["encode"]["status"])
    assert by["copy"]["elapsed_s"] >= 0 and by["encode"]["elapsed_s"] >= 0
    cap = capsys.readouterr()
    assert re.search(
        r"ok    Marked \(2009\)_t00\.mkv: copy \S+ \+ subs \S+ \+ detect \S+ \+ encode \S+\n", cap.err)
    assert re.search(r"ok    1 ok · \d+s\n$", cap.err)  # the result line


def test_keep_banner_prints_only_when_something_parked(env, capsys):
    """The --keep banner is honest: it prints after a real lossless park,
    stays silent when keep=0 deleted the source, and silent when a failed run
    parked nothing."""
    vid = env / "vid"

    # success + keep=1: parked, banner prints
    mk(vid, "rip/movies/Park (2011)/d0/Park (2011)_t00.mkv", "PARK-MAIN")
    rc = transcode.run_transcode(Args(keep=True))
    err = capsys.readouterr().err
    assert rc == 0
    assert "--keep: lossless sources moved to" in err
    assert list((vid / "lossless" / "movies").rglob("Park (2011)_t00.mkv"))

    # success + keep=0: deleted, no banner
    mk(vid, "rip/movies/Wisp (2012)/d0/Wisp (2012)_t00.mkv", "WISP-MAIN")
    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err
    assert rc == 0
    assert "--keep: lossless sources moved to" not in err

    # failure: source stays in staging, nothing parked, no banner
    mk(vid, "rip/movies/Failme (1999)/d1/Failme (1999)_t00.mkv", "FAILME")
    rc = transcode.run_transcode(Args(keep=True))
    err = capsys.readouterr().err
    assert rc == 1
    assert "--keep: lossless sources moved to" not in err


# -- TV branch (slice 04): seasons/specials/doubles/junk, show doc, extras -----

def test_scenario6_tv_seasons_specials_junk(env, capsys):
    """Windows test_scenario6: the TV root encodes every staged episode (the
    double stays one S01E05-E06 file), season 0 files into Specials, the TV
    extra into the show's extras/, the junk label stays in staging, and the
    movie beside the TV tree still picks and encodes its main — no TV item
    ever fires a main pick or merge."""
    vid = env / "vid"
    mk(vid, "rip/tv/Show (2010)/d0/Show (2010)_S01E02.mkv", "TV-E02")
    mk(vid, "rip/tv/Show (2010)/d0/Show (2010)_S01E05-E06.mkv", "TV-DOUBLE")
    mk(vid, "rip/tv/Show (2010)/d0/Show (2010)_S00E01.mkv", "TV-SPECIAL")
    mk(vid, "rip/tv/Show (2010)/d0/Show (2010) - Extra 03.mkv", "TV-EXTRA")
    mk(vid, "rip/tv/Show (2010)/d1/B1_S01E02.mkv", "TV-JUNK")  # parallel drive's junk label
    mk(vid, "rip/movies/Mov (2011)/d0/Mov (2011)_t00.mkv", "MOV-MAIN")

    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err

    assert rc == 0
    TL = vid / "library" / "tv" / "Show (2010)"
    assert (TL / "Season 01" / "Show (2010) - S01E02.mkv").read_text() == "TV-E02"
    assert (TL / "Season 01" / "Show (2010) - S01E05-E06.mkv").read_text() == "TV-DOUBLE"
    assert (TL / "Specials" / "Show (2010) - S00E01.mkv").read_text() == "TV-SPECIAL"
    assert (TL / "extras" / "Show (2010) - Extra 03.mkv").read_text() == "TV-EXTRA"
    assert (vid / "library" / "movies" / "Mov (2011)" / "Mov (2011).mkv").read_text() == "MOV-MAIN"
    assert [p.name for p in (vid / "rip" / "tv" / "Show (2010)" / "d1").iterdir()] == ["B1_S01E02.mkv"]
    assert "skip (junk label" in err
    assert len((env / "hb-calls.log").read_text().strip().splitlines()) == 5


def test_tv_show_doc_copies_into_season_folders(env):
    """Windows test_tv_show_doc_copies_into_season_folders: a show carrying
    validation.json (the whole-show document, every season) ships the doc
    into each Season folder beside its encodes — the checker reads it there;
    the extras folder gets no copy."""
    vid = env / "vid"
    doc = json.dumps({
        "tmdb": {"id": "456", "type": "tv", "title": "Show (2010)"},
        "seasons": [{"season": 1, "episodes": [], "discs": []},
                    {"season": 0, "episodes": [], "discs": []}],
        "encodes": []})
    mk(vid, "rip/tv/Show (2010)/validation.json", doc)
    mk(vid, "rip/tv/Show (2010)/d0/Show (2010)_S01E02.mkv", "TV-E02")
    mk(vid, "rip/tv/Show (2010)/d1/Show (2010)_S00E01.mkv", "TV-SPECIAL")
    mk(vid, "rip/tv/Show (2010)/d1/Show (2010) - Extra 03.mkv", "TV-EXTRA")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    TL = vid / "library" / "tv" / "Show (2010)"
    assert json.loads((TL / "Season 01" / "validation.json").read_text())["tmdb"]["id"] == "456"
    assert json.loads((TL / "Specials" / "validation.json").read_text())["tmdb"]["id"] == "456"
    assert not (TL / "extras" / "validation.json").exists()


def test_tv_show_doc_absent_is_silent(env):
    """Windows test_tv_show_doc_absent_is_silent: no validation.json in
    staging (a pre-feature show) — the encodes ship exactly as before, no
    doc, no crash, no note."""
    vid = env / "vid"
    mk(vid, "rip/tv/Old (2009)/d0/Old (2009)_S01E01.mkv", "TV-E01")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    TL = vid / "library" / "tv" / "Old (2009)"
    assert (TL / "Season 01" / "Old (2009) - S01E01.mkv").read_text() == "TV-E01"
    assert not (TL / "Season 01" / "validation.json").exists()


def test_scenario7_tv_extras_disc(env):
    """Windows test_scenario7: a TV-extras-only disc routes to the TV tree's
    extras/ folder — never a movie folder, never a merge."""
    vid = env / "vid"
    mk(vid, "rip/tv/Solo Show (2012)/d0/Solo Show (2012) - Extra 04.mkv", "TVONLY-EXTRA")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    assert (vid / "library" / "tv" / "Solo Show (2012)" / "extras" /
            "Solo Show (2012) - Extra 04.mkv").read_text() == "TVONLY-EXTRA"
    assert not (vid / "library" / "movies" / "Solo Show (2012)").exists()
    assert len((env / "hb-calls.log").read_text().strip().splitlines()) == 1


def test_tv_lowercase_root_is_tv(env):
    """Windows test_tv_lowercase_root_is_tv: rip/tv (the SPEC layout's root)
    decides TV, so the episode encodes into Season NN — no main-feature
    pick."""
    vid = env / "vid"
    mk(vid, "rip/tv/Show (2013)/d0/Show (2013)_S01E01.mkv", "LOW-TV")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    assert (vid / "library" / "tv" / "Show (2013)" / "Season 01" /
            "Show (2013) - S01E01.mkv").read_text() == "LOW-TV"


def test_tv_episode_pass_and_zero_subs_encode(env):
    """Windows test_tv_episode_pass_and_zero_subs_encode: the episode ships
    zero-track with the inline pass's sidecars beside it (the sidecar base
    name is the episode label, not the bare show name), and the source
    leaves staging."""
    vid = env / "vid"
    mk(vid, "rip/tv/Show (2010)/d0/Show (2010)_S01E02.mkv", "SUBS-ENG-DAN")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    S1 = vid / "library" / "tv" / "Show (2010)" / "Season 01"
    assert (S1 / "Show (2010) - S01E02.mkv").read_text() == "SUBS-ENG-DAN"
    assert (S1 / "Show (2010) - S01E02.dan.forced.srt").exists()
    assert (S1 / "Show (2010) - S01E02.eng.srt").exists()
    assert "--subtitle-lang-list zzz" in (env / "hb-calls.log").read_text()
    assert sorted(p.name for p in (vid / "rip" / "tv" / "Show (2010)" / "d0").iterdir()) == []


def test_tv_extra_unchanged_no_pass(env):
    """Windows test_tv_extra_unchanged_no_pass: a TV extra keeps every
    embedded track — no subtitle pass, no sidecars, no zero-subs trick."""
    vid = env / "vid"
    mk(vid, "rip/tv/Show (2010)/d0/Show (2010) - Extra 03.mkv", "SUBS-ENG-DAN")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    ex = vid / "library" / "tv" / "Show (2010)" / "extras"
    assert (ex / "Show (2010) - Extra 03.mkv").read_text() == "SUBS-ENG-DAN"
    assert not list(ex.glob("*.srt"))  # embedded subs stay, no sidecars
    assert not (env / "seconv-calls.log").exists()  # no pass ran
    assert "--subtitle-lang-list" not in (env / "hb-calls.log").read_text()


def test_interrupt_drops_tv_sidecar_dir_and_temp(env, monkeypatch):
    """Windows test_interrupt_drops_tv_sidecar_dir_and_temp: Ctrl+C during a
    TV episode's encode also drops the never-shipped sidecar dir from the
    work dir."""
    vid = env / "vid"
    mk(vid, "rip/tv/Halt (2011)/d0/Halt (2011)_S01E01.mkv", "HALT-EPISODE")
    work = pathlib.Path(os.environ["VIDEO_WORK_DIR"])

    fake_side = None
    real_sub_pass = transcode._sub_pass

    def spy_sub_pass(local_src, side_dir, base, *a, **k):
        nonlocal fake_side
        fake_side = side_dir
        res = real_sub_pass(local_src, side_dir, base, *a, **k)
        return res

    def fake_encode(src, out, *a, **k):
        raise Interrupted()

    monkeypatch.setattr(transcode, "_sub_pass", spy_sub_pass)
    monkeypatch.setattr(transcode, "_encode", fake_encode)

    rc = transcode.run_transcode(Args())

    assert rc == 130
    assert fake_side is not None  # the sidecar dir existed for the interrupt to drop
    assert list(work.iterdir()) == []  # local copy, sidecar dir, no partial shipped
    assert (vid / "rip" / "tv" / "Halt (2011)" / "d0" / "Halt (2011)_S01E01.mkv").exists()


# -- extras marker + session cap ------------------------------------------------

def test_marked_extra_encodes_unmarked_rides_disposal(env, capsys):
    """The extras marker gates the extras arm: include=true encodes at
    superfast; unmarked extras never encode — they ride the main's disposal
    (deleted without -k)."""
    vid = env / "vid"
    mk(vid, "rip/movies/Gate (2001)/d0/Gate (2001)_t00.mkv", "GATE-MAIN")
    mk(vid, "rip/movies/Gate (2001)/d0/Gate (2001)_t01.mkv", "GATE-KEEPER")
    mk(vid, "rip/movies/Gate (2001)/validation.json",
       _movie_doc("t00", extras={"include": True, "quality": "superfast"}))
    mk(vid, "rip/movies/Plain (2002)/d0/Plain (2002)_t00.mkv", "PLAIN-MAIN")
    mk(vid, "rip/movies/Plain (2002)/d0/Plain (2002)_t01.mkv", "PLAIN-EX")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    L = vid / "library" / "movies"
    assert (L / "Gate (2001)" / "extras" / "Gate (2001) - Extra 01.mkv").read_text() == "GATE-KEEPER"
    assert not (L / "Plain (2002)" / "extras").exists()
    assert not (vid / "rip" / "movies" / "Plain (2002)" / "d0" / "Plain (2002)_t01.mkv").exists()
    err = capsys.readouterr().err
    assert "disposed unmarked extra with main: Plain (2002)_t01.mkv" in err


def test_session_cap_defers_beyond_limit(env, capsys):
    """-n caps mains per session: the rest stays in staging, deferred loudly,
    and rc stays 0."""
    vid = env / "vid"
    mk(vid, "rip/movies/Aaa (2001)/Aaa (2001)_t00.mkv", "AAA")
    mk(vid, "rip/movies/Bbb (2002)/Bbb (2002)_t00.mkv", "BBB")

    rc = transcode.run_transcode(Args(limit=1))

    assert rc == 0
    L = vid / "library" / "movies"
    assert (L / "Aaa (2001)" / "Aaa (2001).mkv").exists()
    assert not (L / "Bbb (2002)").exists()
    assert (vid / "rip" / "movies" / "Bbb (2002)" / "Bbb (2002)_t00.mkv").exists()
    assert "info  deferred (session cap 1): Bbb (2002)" in capsys.readouterr().err


# -- prefetch lookahead: the next unit's copy overlaps the current encode ---------

def _slow_hb(tmp_path):
    """The standard HandBrakeCLI fake, plus a SLOWENCODE arm that sleeps so
    the next unit's background prefetch has an encode window to land in."""
    (tmp_path / "bin" / "HandBrakeCLI").write_text(f"""#!/bin/sh
{{ printf 'ARGS:'; printf ' %s' "$@"; echo; }} >> "{tmp_path / 'hb-calls.log'}"
in=""; out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -i) in="$2"; shift 2 ;;
    -o) out="$2"; shift 2 ;;
    *)  shift ;;
  esac
done
if grep -q FAILME "$in" 2>/dev/null; then exit 3; fi
if grep -q SLOWENCODE "$in" 2>/dev/null; then sleep 2; fi
cat "$in" > "$out"
"""
    )


def test_prefetch_overlaps_encode(env, tmp_path, capsys):
    """Slice 03: with two staged mains, unit 2's source copy happens in the
    background during unit 1's encode — the prefetch note's run-log timestamp
    falls inside item 1's encode window, and item 2's copy phase says
    prefetched (no second sequential copy)."""
    _slow_hb(tmp_path)
    vid = env / "vid"
    mk(vid, "rip/movies/One (2001)/d0/One (2001)_t00.mkv", "SLOWENCODE")
    mk(vid, "rip/movies/Two (2002)/d0/Two (2002)_t00.mkv", "TWO")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    L = vid / "library" / "movies"
    assert (L / "One (2001)" / "One (2001).mkv").read_text() == "SLOWENCODE"
    assert (L / "Two (2002)" / "Two (2002).mkv").read_text() == "TWO"
    log = run_log(env)
    notes = [l for l in log if l["phase"] == "prefetch"]
    assert len(notes) == 1 and "in the background" in notes[0]["status"]
    # the run log only emits phase END lines: One's copy end and encode end
    # bracket the encode window, and the sleep guarantees it is > 1 s wide
    enc = [l for l in log if l["phase"] == "encode"]
    copies = [l for l in log if l["phase"] == "copy"]
    assert copies[0]["ts"] <= notes[0]["ts"] < enc[0]["ts"]
    assert copies[1]["status"] == "prefetched — overlapped with the previous encode"
    assert "MB/s" in copies[0]["status"]
    out = capsys.readouterr().out
    assert "copying Two (2002)_t00.mkv to local temp" not in capsys.readouterr().err


def test_prefetch_overlaps_encode_retry(env, tmp_path, capsys):
    """--retry walks issues/ the same way: two encode units resting in
    issues/ still overlap — unit 2's copy phase reports the prefetched
    status, the run ships, and the sources leave issues/ only on success."""
    _slow_hb(tmp_path)
    vid = env / "vid"
    mk(vid, "issues/movies/One (2001)/One (2001)_t00.mkv", "SLOWENCODE")
    mk(vid, "issues/movies/Two (2002)/Two (2002)_t00.mkv", "TWO")

    rc = transcode.run_transcode(Args(retry=True))

    assert rc == 0
    L = vid / "library" / "movies"
    assert (L / "One (2001)" / "One (2001).mkv").read_text() == "SLOWENCODE"
    assert (L / "Two (2002)" / "Two (2002).mkv").read_text() == "TWO"
    assert not list((vid / "issues" / "movies" / "One (2001)").glob("*.mkv"))
    assert not list((vid / "issues" / "movies" / "Two (2002)").glob("*.mkv"))
    copies = [l for l in run_log(env) if l["phase"] == "copy"]
    assert copies[1]["status"] == "prefetched — overlapped with the previous encode"
    out = capsys.readouterr().out
    assert "copying Two (2002)_t00.mkv to local temp" not in capsys.readouterr().err


def test_prefetch_starved_workdir_falls_back_sequential(env, capsys, monkeypatch):
    """No workdir headroom: one loud note, no prefetch, both items still
    encode with plain sequential copies, rc 0 — nothing new can fail."""
    vid = env / "vid"
    mk(vid, "rip/movies/One (2001)/d0/One (2001)_t00.mkv", "ONE")
    mk(vid, "rip/movies/Two (2002)/d0/Two (2002)_t00.mkv", "TWO")

    usage = type("U", (), {"free": 0})
    monkeypatch.setattr(transcode.shutil, "disk_usage", lambda p: usage())

    rc = transcode.run_transcode(Args())

    assert rc == 0
    L = vid / "library" / "movies"
    assert (L / "One (2001)" / "One (2001).mkv").read_text() == "ONE"
    assert (L / "Two (2002)" / "Two (2002).mkv").read_text() == "TWO"
    notes = [l for l in run_log(env) if l["phase"] == "warn"]
    assert len(notes) == 1 and "no prefetch" in notes[0]["status"]
    assert "copies sequentially later" in capsys.readouterr().err
    copies = [l for l in run_log(env) if l["phase"] == "copy"]
    assert all("MB/s" in c["status"] for c in copies)


def test_interrupt_mid_prefetch_drops_partials(env, capsys, monkeypatch):
    """Ctrl+C (raised from the encode, the SIGINT-during-encode reality) while
    a prefetch copy is in flight: exit 130, the prefetched partial is deleted
    from the workdir, the staged sources stay put."""
    vid = env / "vid"
    mk(vid, "rip/movies/One (2001)/d0/One (2001)_t00.mkv", "ONE")
    mk(vid, "rip/movies/Two (2002)/d0/Two (2002)_t00.mkv", "TWO")
    work = pathlib.Path(os.environ["VIDEO_WORK_DIR"])

    def fake_encode(src, out, *a, **k):
        raise Interrupted()

    monkeypatch.setattr(transcode, "_encode", fake_encode)

    rc = transcode.run_transcode(Args())

    assert rc == 130
    assert list(work.iterdir()) == []
    assert (vid / "rip" / "movies" / "One (2001)" / "d0" / "One (2001)_t00.mkv").exists()
    assert (vid / "rip" / "movies" / "Two (2002)" / "d0" / "Two (2002)_t00.mkv").exists()
    log = run_log(env)
    assert log[-1]["phase"] == "total" and log[-1]["status"] == "interrupted"


def test_prefetch_prefix_run(env, capsys):
    """A prefix-filtered two-title run prefetched exactly like a full run."""
    vid = env / "vid"
    mk(vid, "rip/movies/One (2001)/d0/One (2001)_t00.mkv", "ONE")
    mk(vid, "rip/movies/Two (2002)/d0/Two (2002)_t00.mkv", "TWO")

    rc = transcode.run_transcode(Args(prefixes=["One (2001)", "Two (2002)"]))

    assert rc == 0
    copies = [l for l in run_log(env) if l["phase"] == "copy"]
    assert len(copies) == 2
    assert copies[1]["status"] == "prefetched — overlapped with the previous encode"
    assert "MB/s" in copies[0]["status"]


def test_prefetch_multi_part_main_siblings_all_consumed(env):
    """A split main prefetched as ONE unit: both .pN siblings skip the
    sequential copy (prefetched), the merge still runs on the local copies,
    and the next movie behind it encodes too."""
    vid = env / "vid"
    mk(vid, "rip/movies/One (2001)/d0/One (2001)_t00.mkv", "ONE")
    mk(vid, "rip/movies/Parts (2007)/d0/Parts (2007)_t21.p1.mkv", "P1")
    mk(vid, "rip/movies/Parts (2007)/d0/Parts (2007)_t21.p2.mkv", "P2")
    mk(vid, "rip/movies/Two (2002)/d0/Two (2002)_t00.mkv", "TWO")

    rc = transcode.run_transcode(Args())

    assert rc == 0
    L = vid / "library" / "movies"
    assert (L / "Parts (2007)" / "Parts (2007).mkv").read_text() == "P1P2"
    assert (L / "Two (2002)" / "Two (2002).mkv").read_text() == "TWO"
    copies = [l for l in run_log(env) if l["phase"] == "copy"]
    assert len(copies) == 4
    assert [c["status"] == "prefetched — overlapped with the previous encode"
            for c in copies] == [False, True, True, True]


def test_run_summary_table_count_and_keep_banner(env, capsys):
    """Run end (CONTEXT.md Run summary): after the footer clears, stderr
    carries one table row per item — phase timings and ok/failed — the
    --keep banner only because a source actually parked, then the result
    line naming the run log and run id after the failure."""
    vid = env / "vid"
    mk(vid, "rip/movies/Park (2011)/d0/Park (2011)_t00.mkv", "PARK-MAIN")
    mk(vid, "rip/movies/Failme (1999)/d1/Failme (1999)_t00.mkv", "FAILME")

    rc = transcode.run_transcode(Args(keep=True))
    err = capsys.readouterr().err

    assert rc == 1
    for label, result in (("Park (2011)_t00", "ok"), ("Failme (1999)_t00", "failed")):
        row = [l for l in err.splitlines() if l.strip().startswith(label)]
        assert len(row) == 1
        assert result in row[0]
        assert re.search(r"\d+s", row[0])  # total over recorded phase secs
    banner, result = err.rstrip("\n").splitlines()[-2:]
    assert banner.startswith("--keep: lossless sources moved to")
    run = run_log(env)[0]["run"]
    assert re.fullmatch(
        rf"fail  1 failed · 1 ok · \d+s — see \S+/logs/rip\.jsonl \(run {run}\), "
        rf"raw \S+/logs/raw/{run}\.jsonl", result)


def test_fatal_outside_an_encode_unit_ends_on_a_failure_line(env, capsys, monkeypatch):
    """A fatal error with no item in flight (the --keep park of an unmarked
    extra with no staged main) fails the run, and the result line says so
    even though no summary row failed."""
    vid = env / "vid"
    mk(vid, "rip/movies/Lone (2003)/d0/Lone (2003) - Extra 01.mkv", "LONE-EXTRA")

    def refuse(*a, **k):
        raise OSError("lossless share read-only")

    monkeypatch.setattr(transcode, "_archive", refuse)

    rc = transcode.run_transcode(Args(keep=True))
    err = capsys.readouterr().err

    assert rc == 1
    assert "FATAL: lossless share read-only" in err
    run = run_log(env)[0]["run"]
    assert re.search(
        rf"fail  failed · 0 ok · \d+s — see \S+/logs/rip\.jsonl \(run {run}\), "
        rf"raw \S+/logs/raw/{run}\.jsonl\n$", err)


def test_fallback_warn_prints_once_no_summary_replay(env, capsys):
    """Fallback (non-TTY) warn/fail lines already sit in the scroll; the
    summary must not replay them."""
    vid = env / "vid"
    mk(vid, "rip/movies/Clob (2001)/d0/Clob (2001)_t00.mkv", "CLOB-MAIN")
    d = vid / "lossless" / "movies" / "Clob (2001)"
    d.mkdir(parents=True)
    (d / "Clob (2001)_t00.mkv").write_text("EXISTING")

    rc = transcode.run_transcode(Args(keep=True))
    err = capsys.readouterr().err

    assert rc == 0
    assert len([l for l in err.splitlines() if "already exists" in l]) == 1
    # the park was refused, so nothing parked: no banner (honesty rule)
    assert "--keep: lossless sources moved to" not in err


def test_prefetch_copy_line_in_background_panel(env, tmp_path):
    """Slice 04: during unit 1's encode the prefetch renders as one live
    background line (name + pct), updated in place per 10% step, and
    the key is cleared when the prefetch thread ends."""
    _slow_hb(tmp_path)
    vid = env / "vid"
    mk(vid, "rip/movies/One (2001)/d0/One (2001)_t00.mkv", "SLOWENCODE")
    body = "TWO" * (1024 * 1024)  # >1 MB so _copy2 emits >=2 pct steps
    mk(vid, "rip/movies/Two (2002)/d0/Two (2002)_t00.mkv", body)

    view = ui.RunView(tty=True, live=False)  # TTY render path, never a Live
    ui.set_view(view)
    history: list[str] = []
    real_bg = view.bg
    view.bg = lambda k, t: (history.append(t), real_bg(k, t))
    try:
        rc = transcode.run_transcode(Args())
    finally:
        ui.clear_view()

    assert rc == 0
    assert history, "no background copy lines rendered"
    assert all(h.startswith("Two (2002)_t00.mkv  ") for h in history)
    pcts = [int(h.split("  ")[1].split("%")[0]) for h in history]
    assert pcts == sorted(pcts) and history[-1].startswith(
        "Two (2002)_t00.mkv  100%")
    assert view._bg == {}  # the key is cleared on completion
    assert "Two (2002)_t00.mkv" not in view.render()  # footer line 2 gone with the key
