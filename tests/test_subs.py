"""Tests for skive/subs.py — fake inventories + stubbed subprocess, no real tools.

subprocess.run is replaced wholesale: the fake ffprobe returns a canned
inventory (shape hand-checked against real library MKVs), the fake seconv
writes SRT files into whatever --output-folder it was given.
"""

import json
import pathlib
import threading

import pytest

from skive import subs


def trk(i, lang, codec="dvd_subtitle", forced=False):
    return subs.Track(index=i, lang=lang, codec=codec, forced=forced)


class R:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def write_srt(path, cues):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            f"{i}\n00:00:{i:02d},000 --> 00:00:{i + 1:02d},000\ncue {i}\n\n"
            for i in range(1, cues + 1)
        ),
        encoding="utf-8",
    )


# -- decide --------------------------------------------------------------------
def test_decide_forced_plus_full_pair():
    keeps, notes = subs.decide(
        [trk(3, "eng", forced=True), trk(4, "eng"), trk(5, "dan", forced=True)]
    )
    assert [(k.track.index, k.flag) for k in keeps] == [(3, "forced"), (4, ""), (5, "forced")]
    assert notes == []


def test_decide_skips_stragglers_per_language():
    keeps, notes = subs.decide(
        [trk(4, "swe"), trk(2, "eng", forced=True), trk(5, "swe"),
         trk(6, "und"), trk(7, "nor")]
    )
    assert [(k.track.index, k.flag) for k in keeps] == [(2, "forced")]
    assert notes == [
        "skip swe (tracks 4,5): not eng/dan",
        "skip und (tracks 6): not eng/dan",
        "skip nor (tracks 7): not eng/dan",
    ]


# -- settle --------------------------------------------------------------------
def test_settle_dupe_at_or_above_30pct_takes_sdh():
    keeps, notes = subs.settle([(trk(3, "eng"), 100), (trk(4, "eng"), 40)])
    assert [(k.track.index, k.flag) for k in keeps] == [(3, ""), (4, "sdh")]
    assert notes == []


def test_settle_dupe_below_threshold_dropped_and_reported():
    keeps, notes = subs.settle([(trk(3, "eng"), 100), (trk(4, "eng"), 20)])
    assert [(k.track.index, k.flag) for k in keeps] == [(3, "")]
    assert notes == ["drop eng track 4 (20 events): below 30% of track 3 (100 events)"]


def test_settle_tie_lower_stream_index_wins():
    keeps, _ = subs.settle([(trk(4, "dan"), 50), (trk(3, "dan"), 50)])
    assert [(k.track.index, k.flag) for k in keeps] == [(3, ""), (4, "sdh")]


# -- naming + counting ----------------------------------------------------------
def test_sidecar_name_flags():
    assert subs.sidecar_name("Movie (2001)", "eng", "") == "Movie (2001).eng.srt"
    assert subs.sidecar_name("Movie (2001)", "dan", "forced") == "Movie (2001).dan.forced.srt"
    assert subs.sidecar_name("Movie (2001)", "eng", "sdh") == "Movie (2001).eng.sdh.srt"


def test_count_events_robust_to_trailing_newlines(tmp_path):
    p = tmp_path / "a.srt"
    write_srt(p, 3)
    p.write_text(p.read_text(encoding="utf-8").rstrip("\n") + "\r\n", encoding="utf-8")
    assert subs.count_events(p) == 3
    (tmp_path / "empty.srt").write_text("")
    assert subs.count_events(tmp_path / "empty.srt") == 0


# -- inventory (canned ffprobe) --------------------------------------------------
def test_inventory_missing_language_tag_is_und(monkeypatch):
    canned = {"streams": [
        {"index": 3, "codec_name": "hdmv_pgs_subtitle", "disposition": {"forced": 0}},
        {"index": 4, "codec_name": "dvd_subtitle", "disposition": {"forced": 1},
         "tags": {"language": "dan"}},
    ]}
    monkeypatch.setattr(subs.subprocess, "run",
                        lambda cmd, **kw: R(0, json.dumps(canned), ""))
    assert subs.inventory("x.mkv", "ffprobe.exe") == [
        subs.Track(3, "und", "hdmv_pgs_subtitle", False),
        subs.Track(4, "dan", "dvd_subtitle", True),
    ]


def test_inventory_maps_noncontiguous_track_numbers(monkeypatch):
    """MakeMKV writes gaps in Matroska TrackNumbers (Corpse Bride: 1..4 then
    6): the dan track at ffprobe index 4 is TrackNumber 6 — the old index + 1
    guess asked seconv for track 5, which matched nothing."""
    canned = {"streams": [
        {"index": 3, "codec_name": "hdmv_pgs_subtitle", "disposition": {"forced": 0},
         "tags": {"language": "eng"}},
        {"index": 4, "codec_name": "hdmv_pgs_subtitle", "disposition": {"forced": 0},
         "tags": {"language": "dan"}},
    ]}
    numbers = {"tracks": [{"id": 3, "properties": {"number": 4}},
                          {"id": 4, "properties": {"number": 6}}]}

    def run(cmd, **kw):
        if pathlib.Path(cmd[0]).name.startswith("mkvmerge"):
            return R(0, json.dumps(numbers), "")
        return R(0, json.dumps(canned), "")

    monkeypatch.setattr(subs.shutil, "which", lambda name: "mkvmerge")
    monkeypatch.setattr(subs.subprocess, "run", run)
    tracks = subs.inventory("x.mkv", "ffprobe.exe")
    assert [(t.index, t.number) for t in tracks] == [(3, 4), (4, 6)]


def test_convert_selects_real_track_number(tmp_path, monkeypatch):
    """seconv gets the TrackNumber from the inventory, not index + 1."""
    seen = []

    def run(cmd, **kw):
        seen.append(next(a for a in cmd if a.startswith("--track-number:")))
        out = next(a.split(":", 1)[1] for a in cmd if a.startswith("--output-folder:"))
        write_srt(pathlib.Path(out) / "Movie (2001).dan.srt", 5)
        return R(0, "", "")

    monkeypatch.setattr(subs.subprocess, "run", run)
    srt = subs.convert("x.mkv",
                       subs.Track(4, "dan", "hdmv_pgs_subtitle", False, number=6),
                       "seconv.exe", str(tmp_path))
    assert seen == ["--track-number:6"]
    assert subs.count_events(srt) == 5


# -- run_pass (stubbed tools) -----------------------------------------------------
INV = {"streams": [
    {"index": 2, "codec_name": "dvd_subtitle", "disposition": {"forced": 1},
     "tags": {"language": "dan"}},
    {"index": 3, "codec_name": "hdmv_pgs_subtitle", "disposition": {"forced": 0},
     "tags": {"language": "eng"}},
    {"index": 4, "codec_name": "hdmv_pgs_subtitle", "disposition": {"forced": 0},
     "tags": {"language": "eng"}},
    {"index": 5, "codec_name": "dvd_subtitle", "disposition": {"forced": 0},
     "tags": {"language": "swe"}},
]}
CUES = {3: 6, 4: 100, 5: 40, 6: 9}  # keyed by the TID seconv receives (contiguous fake: index + 1)


def stub_run(monkeypatch, dirs, fail_track=0, empty_track=0, barren_track=0):
    """Fake mkvmerge + ffprobe (canned inventory) + fake seconv writing
    <stem>.<lang>.srt into its --output-folder — the same name for both eng
    full tracks, so a shared dir would collide and the test would catch it."""
    def run(cmd, **kw):
        if pathlib.Path(cmd[0]).name.startswith("mkvmerge"):
            # contiguous TrackNumbers: id i -> number i+1, so CUES keys stay index + 1
            return R(0, json.dumps({"tracks": [
                {"id": i, "properties": {"number": i + 1}} for i in range(10)]}), "")
        if pathlib.Path(cmd[0]).name.startswith("ffprobe"):
            return R(0, json.dumps(INV), "")
        num = int(next(a.split(":", 1)[1] for a in cmd if a.startswith("--track-number:")))
        lang = next(a.split(":", 1)[1] for a in cmd if a.startswith("--ocr-language:"))
        out_dir = next(a.split(":", 1)[1] for a in cmd if a.startswith("--output-folder:"))
        dirs.append(out_dir)
        if num == barren_track:
            return R(1, "No subtitle tracks matched", "")
        if num == fail_track:
            return R(1, "3 image(s) produced no OCR text and were dropped", "boom")
        write_srt(pathlib.Path(out_dir) / f"Movie (2001).{lang}.srt",
                  0 if num == empty_track else CUES[num])
        return R(0, "", "")

    monkeypatch.setattr(subs.subprocess, "run", run)


def test_run_pass_temp_dir_isolation_and_final_renames(tmp_path, monkeypatch):
    dirs = []
    stub_run(monkeypatch, dirs)
    out = tmp_path / "out"
    res = subs.run_pass(str(tmp_path / "Movie (2001).mkv"), str(out), "Movie (2001)",
                        "seconv.exe", "ffprobe.exe")

    # one fresh temp dir per converted track, all cleaned up afterwards
    assert len(dirs) == 3 and len(set(dirs)) == 3
    assert all(not pathlib.Path(d).exists() for d in dirs)

    assert res.ok
    assert res.sidecars == [
        "Movie (2001).dan.forced.srt", "Movie (2001).eng.srt", "Movie (2001).eng.sdh.srt",
    ]
    for name, cues in [("Movie (2001).dan.forced.srt", 6),
                       ("Movie (2001).eng.srt", 100),
                       ("Movie (2001).eng.sdh.srt", 40)]:
        assert subs.count_events(out / name) == cues
    assert "skip swe (tracks 5): not eng/dan" in res.notes
    assert not any(n.startswith("FAIL") for n in res.notes)


def test_run_pass_failed_conversion_is_partial_success(tmp_path, monkeypatch):
    dirs = []
    stub_run(monkeypatch, dirs, fail_track=5)
    out = tmp_path / "out"
    res = subs.run_pass("x.mkv", str(out), "Movie (2001)", "seconv.exe", "ffprobe.exe")
    assert not res.ok
    assert "FAIL eng (track 4): seconv failed" in res.notes
    # the survivors still ship
    assert res.sidecars == ["Movie (2001).dan.forced.srt", "Movie (2001).eng.srt"]
    assert (out / "Movie (2001).eng.srt").exists()


def test_run_pass_empty_sidecar_drops_track_as_barren(tmp_path, monkeypatch):
    """Zero-cue sidecar is a barren track (CONTEXT.md): a drop-with-note, the
    pass stays ok, the winner ships, the empty loser never became .sdh."""
    dirs = []
    stub_run(monkeypatch, dirs, empty_track=5)
    out = tmp_path / "out"
    res = subs.run_pass("x.mkv", str(out), "Movie (2001)", "seconv.exe", "ffprobe.exe")
    assert res.ok
    assert ("barren eng (track 4): OCR yielded 0 cues -- dropped") in res.notes
    assert res.sidecars == ["Movie (2001).dan.forced.srt", "Movie (2001).eng.srt"]


def test_run_pass_barren_seconv_failure_drops_track(tmp_path, monkeypatch):
    """seconv rc=1 with 'No subtitle tracks matched' (every image dropped as
    unreadable) is the barren sentinel: drop with a note, pass stays ok, no
    quarantine-worthy failure."""
    dirs = []
    stub_run(monkeypatch, dirs, barren_track=5)
    out = tmp_path / "out"
    res = subs.run_pass("x.mkv", str(out), "Movie (2001)", "seconv.exe", "ffprobe.exe")
    assert res.ok
    assert ("barren eng (track 4): OCR yielded 0 cues -- dropped") in res.notes
    assert not any(n.startswith("FAIL") for n in res.notes)
    assert res.sidecars == ["Movie (2001).dan.forced.srt", "Movie (2001).eng.srt"]


def test_convert_real_failure_replays_full_output_on_console(tmp_path, monkeypatch, capsys):
    """A real seconv failure replays the FULL captured output (stdout + stderr)
    on stderr -- the Conan diagnosis lived in stdout."""
    dirs = []
    stub_run(monkeypatch, dirs, fail_track=5)
    subs.run_pass("x.mkv", str(tmp_path / "out"), "Movie (2001)",
                  "seconv.exe", "ffprobe.exe")
    err = capsys.readouterr().err
    assert "seconv failed: track 4 (eng)" in err
    assert "3 image(s) produced no OCR text and were dropped" in err  # stdout
    assert "boom" in err  # stderr


def test_run_pass_ocrs_kept_tracks_concurrently(tmp_path, monkeypatch):
    """Proof of concurrency: the fake seconv blocks on a barrier sized to the
    3 kept tracks; sequential OCR would deadlock on the first wait."""
    barrier = threading.Barrier(3)
    dirs = []

    def run(cmd, **kw):
        if pathlib.Path(cmd[0]).name.startswith("mkvmerge"):
            return R(0, json.dumps({"tracks": [
                {"id": i, "properties": {"number": i + 1}} for i in range(10)]}), "")
        if pathlib.Path(cmd[0]).name.startswith("ffprobe"):
            return R(0, json.dumps(INV), "")
        num = int(next(a.split(":", 1)[1] for a in cmd if a.startswith("--track-number:")))
        lang = next(a.split(":", 1)[1] for a in cmd if a.startswith("--ocr-language:"))
        out_dir = next(a.split(":", 1)[1] for a in cmd if a.startswith("--output-folder:"))
        dirs.append(out_dir)
        barrier.wait(timeout=15)
        write_srt(pathlib.Path(out_dir) / f"Movie (2001).{lang}.srt", CUES[num])
        return R(0, "", "")

    monkeypatch.setattr(subs.subprocess, "run", run)
    out = tmp_path / "out"
    res = subs.run_pass(str(tmp_path / "Movie (2001).mkv"), str(out), "Movie (2001)",
                        "seconv.exe", "ffprobe.exe")

    # reassembly in keeps order: same settlement as the sequential pass
    assert res.ok
    assert res.sidecars == [
        "Movie (2001).dan.forced.srt", "Movie (2001).eng.srt", "Movie (2001).eng.sdh.srt",
    ]
    assert len(dirs) == 3 and len(set(dirs)) == 3


def test_run_pass_no_eng_dan_tracks(tmp_path, monkeypatch):
    canned = {"streams": [{"index": 9, "codec_name": "dvd_subtitle",
                           "disposition": {"forced": 0}, "tags": {"language": "nor"}}]}
    monkeypatch.setattr(subs.subprocess, "run",
                        lambda cmd, **kw: R(0, json.dumps(canned), ""))
    res = subs.run_pass("x.mkv", str(tmp_path / "out"), "Movie (2001)",
                        "seconv.exe", "ffprobe.exe")
    assert res.ok and res.sidecars == []
    assert res.notes == ["skip nor (tracks 9): not eng/dan"]


def test_run_pass_each_ocr_track_gets_a_live_lane(tmp_path, monkeypatch):
    """On a TTY view the fan-out shows one live-box lane per kept track,
    cleared when the track finishes; plain runs keep the step line."""
    lanes = []

    class FakeView:
        tty = True

        def bg(self, key, text, pct=None, rate=None):
            lanes.append(("set", key))

        def bg_clear(self, key):
            lanes.append(("clear", key))

    monkeypatch.setattr(subs.ui, "view", lambda: FakeView())
    dirs = []
    stub_run(monkeypatch, dirs)
    res = subs.run_pass("x.mkv", str(tmp_path / "out"), "Movie (2001)",
                        "seconv.exe", "ffprobe.exe")
    assert res.ok
    set_ = [k for op, k in lanes if op == "set"]
    cleared = [k for op, k in lanes if op == "clear"]
    assert sorted(set_) == ["OCR dan track 2 (forced)", "OCR eng track 3", "OCR eng track 4"]
    assert sorted(cleared) == sorted(set_)
