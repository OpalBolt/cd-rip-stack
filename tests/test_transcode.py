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
mkvmerge failure) and the movie-extras scenarios. The keep/retry scenarios
stay in the Windows file until slice 06 ports them, and the earlier slices'
scope changes show up as adapted assertions: demoted byte-winners now file
as extras, and skive's RipLog skips the total when a clean run had exactly
one phase.
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
  elif grep -q SUBS-SETTLE "$in" 2>/dev/null; then
    printf '%s\\n' '{"streams":[{"index":2,"codec_name":"dvd_subtitle","disposition":{"forced":1},"tags":{"language":"eng"}},{"index":3,"codec_name":"dvd_subtitle","disposition":{"forced":0},"tags":{"language":"eng"}},{"index":4,"codec_name":"dvd_subtitle","disposition":{"forced":0},"tags":{"language":"eng"}}]}'
  elif grep -q SUBS-EMPTY "$in" 2>/dev/null; then
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
    entry = {"file": "Mamma Mia! (2008).mkv", "src": "t21", "dur_s": 6251}
    assert json.loads((L / "validation.json").read_text())["encodes"] == [entry]
    staged = vid / "rip" / "movies" / "Mamma Mia! (2008)" / "validation.json"
    assert json.loads(staged.read_text())["encodes"] == [entry]
    # the shipped main's source is deleted; the demoted byte-winner files as
    # an extra (slice 05's extras arm)
    assert not (vid / "rip" / "movies" / "Mamma Mia! (2008)" / "Mamma Mia! (2008)_t21.mkv").exists()
    assert not (vid / "rip" / "movies" / "Mamma Mia! (2008)" / "Mamma Mia! (2008)_t12.mkv").exists()
    assert (L / "extras" / "Mamma Mia! (2008) - Extra 12.mkv").read_text().startswith("RED-BYTES")


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
    # the non-main _t title files as an extra (slice 05's extras arm)
    assert (L / "Shorts (2001)" / "extras" / "Shorts (2001) - Extra 21.mkv").read_text() == "NULL-SMALL"
    assert not (vid / "rip" / "movies" / "Shorts (2001)" / "Shorts (2001)_t21.mkv").exists()


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
    assert "transcode: interrupted -- partial temp output deleted, source left in staging" \
        in capsys.readouterr().err


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
        {"file": "Parts (2007).mkv", "src": "t21", "dur_s": 6251}]


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


def test_pass_empty_sidecar_fails_like_a_failed_track(env, capsys):
    """The empty-sidecar failure path (tests/test_subs.py's
    test_run_pass_empty_sidecar_fails, through the transcode flow): seconv
    produces a zero-cue SRT, the encode still ships subless, no sidecar
    ships, the source parks in issues/ with the run's evidence, exit
    non-zero."""
    vid = env / "vid"
    mk(vid, "rip/movies/Blank (2006)/d0/Blank (2006)_t00.mkv", "SUBS-EMPTY")

    rc = transcode.run_transcode(Args())
    err = capsys.readouterr().err

    assert rc == 1
    L = vid / "library" / "movies" / "Blank (2006)"
    assert (L / "Blank (2006).mkv").read_text() == "SUBS-EMPTY"
    assert not list(L.glob("*.srt"))  # the empty sidecar never shipped
    issues = vid / "issues" / "movies" / "Blank (2006)"
    assert (issues / "Blank (2006)_t00.mkv").read_text() == "SUBS-EMPTY"
    assert "FAILED (subtitle pass)" in err
    log = " ".join(l["status"] for l in run_log(env))
    assert "empty sidecar (0 events)" in log


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

    rc = transcode.run_transcode(Args())

    assert rc == 0
    assert (vid / "library" / "movies" / "Old (2001)" / "extras" /
            "Old (2001) - Extra 02.mkv").read_text() == "OLD-EXTRA"


_ARGV_TAIL = ('--preset H.265 MKV 1080p30 --all-audio --aencoder copy '
              '--audio-copy-mask aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac,pcm '
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

    rc = transcode.run_transcode(Args())

    assert rc == 0
    lines = (env / "hb-calls.log").read_text().strip().splitlines()
    assert len(lines) == 2
    feat = next(l for l in lines if "_t00.h265.mkv" in l)
    extra = next(l for l in lines if "_t01.h265.mkv" in l)
    # feature: the no-match language list rides in front of --all-subtitles
    assert feat.split(".h265.mkv ", 1)[1] == _ARGV_TAIL + \
        "--subtitle-lang-list zzz --all-subtitles --subtitle-burned=none"
    # extra: byte-identical to the pass-through argv
    assert extra.split(".h265.mkv ", 1)[1] == \
        _ARGV_TAIL + "--all-subtitles --subtitle-burned=none"


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
