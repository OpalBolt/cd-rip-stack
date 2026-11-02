"""Tests for skive/subs.py — fake inventories + stubbed subprocess, no real tools.

subprocess.run is replaced wholesale: the fake ffprobe returns a canned
inventory (shape hand-checked against real library MKVs), the fake seconv
writes SRT files into whatever --output-folder it was given.
"""

import json
import pathlib

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
CUES = {3: 6, 4: 100, 5: 40, 6: 9}  # keyed by the TID seconv receives (ffprobe index + 1)


def stub_run(monkeypatch, dirs, fail_track=0, empty_track=0):
    """Fake ffprobe (canned inventory) + fake seconv writing
    <stem>.<lang>.srt into its --output-folder — the same name for both eng
    full tracks, so a shared dir would collide and the test would catch it."""
    def run(cmd, **kw):
        if pathlib.Path(cmd[0]).name.startswith("ffprobe"):
            return R(0, json.dumps(INV), "")
        num = int(next(a.split(":", 1)[1] for a in cmd if a.startswith("--track-number:")))
        lang = next(a.split(":", 1)[1] for a in cmd if a.startswith("--ocr-language:"))
        out_dir = next(a.split(":", 1)[1] for a in cmd if a.startswith("--output-folder:"))
        dirs.append(out_dir)
        if num == fail_track:
            return R(1, "", "boom")
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


def test_run_pass_empty_sidecar_fails(tmp_path, monkeypatch):
    dirs = []
    stub_run(monkeypatch, dirs, empty_track=5)
    out = tmp_path / "out"
    res = subs.run_pass("x.mkv", str(out), "Movie (2001)", "seconv.exe", "ffprobe.exe")
    assert not res.ok
    assert "FAIL eng (track 4): empty sidecar (0 events)" in res.notes
    # winner ships; the empty loser never became .sdh
    assert res.sidecars == ["Movie (2001).dan.forced.srt", "Movie (2001).eng.srt"]


def test_run_pass_no_eng_dan_tracks(tmp_path, monkeypatch):
    canned = {"streams": [{"index": 9, "codec_name": "dvd_subtitle",
                           "disposition": {"forced": 0}, "tags": {"language": "nor"}}]}
    monkeypatch.setattr(subs.subprocess, "run",
                        lambda cmd, **kw: R(0, json.dumps(canned), ""))
    res = subs.run_pass("x.mkv", str(tmp_path / "out"), "Movie (2001)",
                        "seconv.exe", "ffprobe.exe")
    assert res.ok and res.sidecars == []
    assert res.notes == ["skip nor (tracks 9): not eng/dan"]
