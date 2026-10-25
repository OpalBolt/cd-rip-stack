"""Tests for scripts/backfill_validation.py — fake TMDB + fake probes, no
network, no share. Everything the script needs from the world is injected:
search_first/fetch_season return canned picks and season lists, probe_duration
returns canned durations keyed by file name. Dry-run is the default under
test; --apply writes into tmp trees only."""

import importlib.util
import json
import pathlib
import sys

_REPO = pathlib.Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location(
    "backfill_validation", _REPO / "scripts" / "backfill_validation.py")
bf = importlib.util.module_from_spec(_spec)
sys.modules["backfill_validation"] = bf
_spec.loader.exec_module(bf)

from skive.tmdb import TMDBPick  # noqa: E402
from skive.validation import VALIDATION_FILENAME  # noqa: E402


def _install(monkeypatch, *, search=None, seasons=None, durs=None):
    """Inject the fake world: pick per (kind, query), season lists per
    (id, season), probe durations per file name."""
    def fake_search(kind, query):
        if search is None:
            raise AssertionError(f"unexpected TMDB search: {kind} {query}")
        return search.get((kind, query))  # absent key = no match, like the real one

    def fake_season(tv_id, season):
        if seasons is None:
            raise AssertionError(f"unexpected season fetch: {tv_id} S{season}")
        return seasons.get((tv_id, season), [])

    def fake_probe(path):
        return (durs or {}).get(pathlib.Path(path).name)

    monkeypatch.setattr(bf, "search_first", fake_search)
    monkeypatch.setattr(bf, "fetch_season", fake_season)
    monkeypatch.setattr(bf, "probe_duration", fake_probe)


def _mk(root, rel):
    p = pathlib.Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_movie_backfill_writes_null_provenance(tmp_path, monkeypatch):
    """A pre-feature movie folder: every _tNN source probed and graded
    against the searched runtime; main/volume_label/run null — the
    reconstruction never guesses what the rip run knew."""
    vid = tmp_path / "vid"
    _mk(vid, "rip/movies/Film (2001)/Film (2001)_t00.mkv")
    _mk(vid, "rip/movies/Film (2001)/Film (2001)_t12.p1.mkv")
    _mk(vid, "lossless/movies/Second (2002)/Second (2002)_t03.mkv")
    _install(monkeypatch,
             search={("movie", "Film"): TMDBPick("Film", "2001", "en", "movie",
                                                 "123", runtime_secs=6000),
                     ("movie", "Second"): TMDBPick("Second", "2002", "en", "movie",
                                                   "124", runtime_secs=6000)},
             durs={"Film (2001)_t00.mkv": 5880, "Film (2001)_t12.p1.mkv": 900,
                   "Second (2001)_t03.mkv": None, "Second (2002)_t03.mkv": 5880})

    assert bf.main(["--apply", "--root", str(vid)]) == 0

    doc = json.loads((vid / "rip/movies/Film (2001)" / VALIDATION_FILENAME)
                     .read_text(encoding="utf-8"))
    assert doc["tmdb"] == {"id": "123", "type": "movie", "title": "Film (2001)",
                           "runtime_s": 6000}
    assert doc["main"] is None and doc["volume_label"] is None and doc["run"] is None
    assert doc["titles"] == [
        {"file": "Film (2001)_t00.mkv", "t": 0, "dur_s": 5880,
         "ratio": 0.98, "band": "green"},
        {"file": "Film (2001)_t12.p1.mkv", "t": 12, "dur_s": 900,
         "ratio": None, "band": None},  # sub-floor: never graded
    ]
    # lossless folders backfill the same way
    assert (vid / "lossless/movies/Second (2002)" / VALIDATION_FILENAME).exists()


def test_movie_backfill_copies_into_library_when_folder_exists(tmp_path, monkeypatch):
    """A backfilled doc lands beside the library encode when the library
    folder exists and has no document of its own — the checker then reads it
    instead of searching TMDB. No library folder (still awaiting transcode)
    or an existing doc: no copy."""
    vid = tmp_path / "vid"
    _mk(vid, "rip/movies/Wait (2003)/Wait (2003)_t00.mkv")   # not transcoded yet
    _mk(vid, "lossless/movies/Done (2004)/Done (2004)_t00.mkv")
    _mk(vid, "library/movies/Done (2004)/Done (2004).mkv")
    _mk(vid, "library/movies/Live (2005)/Live (2005).mkv")   # doc already live
    _mk(vid, "rip/movies/Live (2005)/Live (2005)_t00.mkv")
    live_doc = {"tmdb": {"id": "9", "type": "movie", "title": "Live (2005)"}}
    (vid / "library/movies/Live (2005)" / VALIDATION_FILENAME).write_text(
        json.dumps(live_doc), encoding="utf-8")
    _install(monkeypatch,
             search={("movie", kind): TMDBPick(kind, "20xx", "en", "movie",
                                               {"Wait": "1", "Done": "2",
                                                "Live": "9"}[kind],
                                               runtime_secs=6000)
                     for kind in ("Wait", "Done", "Live")},
             durs={"Wait (2003)_t00.mkv": 5880, "Done (2004)_t00.mkv": 5880,
                   "Live (2005)_t00.mkv": 5880})

    bf.main(["--apply", "--root", str(vid)])

    # copied beside the encode, identical bytes
    copied = json.loads((vid / "library/movies/Done (2004)" / VALIDATION_FILENAME)
                        .read_text(encoding="utf-8"))
    assert copied["tmdb"]["id"] == "2"
    # no library folder yet: source-side doc only
    assert not (vid / "library/movies/Wait (2003)").exists()
    # a live library doc is never overwritten
    assert json.loads((vid / "library/movies/Live (2005)" / VALIDATION_FILENAME)
                      .read_text()) == live_doc


def test_movie_backfill_skips_existing_doc_and_failures(tmp_path, monkeypatch, capsys):
    """A live record always wins (never overwritten); no TMDB match and no
    (Year) each skip with their one note; no staged sources skips."""
    vid = tmp_path / "vid"
    live = vid / "rip/movies/Keeper (2006)"
    live.mkdir(parents=True)
    (live / VALIDATION_FILENAME).write_text('{"tmdb": {"id": "1"}}',
                                            encoding="utf-8")
    _mk(vid, "rip/movies/Nomatch (2007)/Nomatch (2007)_t00.mkv")
    _mk(vid, "rip/movies/Noyear/Noyear_t00.mkv")
    _mk(vid, "rip/movies/Empty (2008)/readme.txt")
    _install(monkeypatch, search={})

    assert bf.main(["--apply", "--root", str(vid)]) == 0

    assert (live / VALIDATION_FILENAME).read_text(encoding="utf-8") == '{"tmdb": {"id": "1"}}'
    err = capsys.readouterr().err
    assert "no TMDB match for 'Nomatch'" in err
    assert "Noyear: no (Year)" in err
    assert "Empty (2008): no staged sources" in err
    for name in ("Nomatch (2007)", "Noyear", "Empty (2008)"):
        assert not (vid / "rip/movies" / name / VALIDATION_FILENAME).exists()


def test_dry_run_default_writes_nothing(tmp_path, monkeypatch, capsys):
    """Default is the plan, not the write: every folder named, nothing on
    disk; --apply then writes."""
    vid = tmp_path / "vid"
    _mk(vid, "rip/movies/Film (2001)/Film (2001)_t00.mkv")
    _install(monkeypatch,
             search={("movie", "Film"): TMDBPick("Film", "2001", "en", "movie",
                                                 "123", runtime_secs=6000)},
             durs={"Film (2001)_t00.mkv": 5880})

    bf.main(["--root", str(vid)])

    assert not (vid / "rip/movies/Film (2001)" / VALIDATION_FILENAME).exists()
    out = capsys.readouterr().out
    assert "would write 1 document(s); 0 skipped" in out
    assert "write  rip/movies/Film (2001): 1 titles, main=null" in out

    bf.main(["--apply", "--root", str(vid)])
    assert (vid / "rip/movies/Film (2001)" / VALIDATION_FILENAME).exists()


def test_tv_backfill_builds_every_season_from_staged_names(tmp_path, monkeypatch):
    """A pre-feature show: seasons derive from the staged episode names, one
    seasons entry each, episodes probed and graded against the fetched
    per-episode runtimes, t null (no title numbers survive), discs empty
    (no disc provenance survives). Doubles record one row under their lo."""
    vid = tmp_path / "vid"
    show = "rip/tv/Show (2010)"
    _mk(vid, f"{show}/Show (2010)_S01E01.mkv")
    _mk(vid, f"{show}/Show (2010)_S01E02.mkv")
    _mk(vid, f"{show}/Show (2010)_S02E05-E06.mkv")
    _mk(vid, f"{show}/Show (2010) - Extra 01.mkv")  # never an episode row
    _install(monkeypatch,
             search={("tv", "Show"): TMDBPick("Show", "2010", "en", "tv", "456")},
             seasons={("456", 1): [(1, "a", 2580), (2, "b", 2580)],
                      ("456", 2): [(5, "e", 2580), (6, "f", 2580)]},
             durs={"Show (2010)_S01E01.mkv": 2600, "Show (2010)_S01E02.mkv": 2100,
                   "Show (2010)_S02E05-E06.mkv": 5160})

    bf.main(["--apply", "--root", str(vid)])

    doc = json.loads((vid / show / VALIDATION_FILENAME).read_text(encoding="utf-8"))
    assert list(doc) == ["tmdb", "seasons", "encodes"]
    assert doc["tmdb"] == {"id": "456", "type": "tv", "title": "Show (2010)"}
    assert [s["season"] for s in doc["seasons"]] == [1, 2]
    s1, s2 = doc["seasons"]
    assert [(r["ep"], r["ratio"], r["band"]) for r in s1["episodes"]] == [
        (1, 1.01, "green"), (2, 0.81, "yellow")]
    assert s1["discs"] == []
    assert [(r["ep"], r["dur_s"], r["ratio"]) for r in s2["episodes"]] == [
        (5, 5160, 1.0)]  # the double grades against the summed runtime
    assert all(r["t"] is None for r in s1["episodes"] + s2["episodes"])


def test_tv_backfill_rebuilds_old_shape_keeps_new_shape(tmp_path, monkeypatch, capsys):
    """An old one-season document (the first schema) is rebuilt whole — its
    partial record cannot grow into the seasons shape. A seasons-shaped
    document is a live record: kept, never touched."""
    vid = tmp_path / "vid"
    old_show = "rip/tv/Old (2011)"
    new_show = "rip/tv/New (2012)"
    _mk(vid, f"{old_show}/Old (2011)_S01E01.mkv")
    _mk(vid, f"{old_show}/Old (2011)_S01E02.mkv")
    (vid / old_show / VALIDATION_FILENAME).write_text(json.dumps({
        "tmdb": {"id": "111", "type": "tv", "title": "Old (2011)", "season": 1},
        "volume_label": "X", "run": "r1",
        "episodes": [{"ep": 1, "file": "Old (2011)_S01E01.mkv", "t": 0,
                      "dur_s": 1, "runtime_s": 2580, "ratio": 0.0,
                      "band": "yellow"}],
        "encodes": []}), encoding="utf-8")
    _mk(vid, f"{new_show}/New (2012)_S01E01.mkv")
    (vid / new_show / VALIDATION_FILENAME).write_text(json.dumps({
        "tmdb": {"id": "222", "type": "tv", "title": "New (2012)"},
        "seasons": [{"season": 1, "episodes": [], "discs": [
            {"volume_label": "Y", "run": "r2", "staged": [1]}]}],
        "encodes": []}), encoding="utf-8")
    _install(monkeypatch,
             search={("tv", "Old"): TMDBPick("Old", "2011", "en", "tv", "111")},
             seasons={("111", 1): [(1, "a", 2580), (2, "b", 2580)]},
             durs={"Old (2011)_S01E01.mkv": 2600, "Old (2011)_S01E02.mkv": 2600})

    bf.main(["--apply", "--root", str(vid)])

    rebuilt = json.loads((vid / old_show / VALIDATION_FILENAME)
                         .read_text(encoding="utf-8"))
    assert [r["dur_s"] for r in rebuilt["seasons"][0]["episodes"]] == [2600, 2600]
    assert rebuilt["seasons"][0]["discs"] == []
    kept = json.loads((vid / new_show / VALIDATION_FILENAME)
                      .read_text(encoding="utf-8"))
    assert kept["seasons"][0]["discs"] == [{"volume_label": "Y", "run": "r2",
                                            "staged": [1]}]
    out = capsys.readouterr().out
    assert "rebuild rip/tv/Old (2011)" in out
    assert "keep   rip/tv/New (2012)" in out


def test_tv_backfill_season_fetch_failure_skips_that_season(tmp_path, monkeypatch):
    """A season whose TMDB fetch fails is skipped with a note; the rest of
    the show still builds — never guessed, never lost."""
    vid = tmp_path / "vid"
    show = "rip/tv/Show (2010)"
    _mk(vid, f"{show}/Show (2010)_S01E01.mkv")
    _mk(vid, f"{show}/Show (2010)_S02E01.mkv")

    def season(tv_id, n):
        if n == 2:
            raise RuntimeError("offline")
        return [(1, "a", 2580)]

    _install(monkeypatch,
             search={("tv", "Show"): TMDBPick("Show", "2010", "en", "tv", "456")},
             seasons=None)
    monkeypatch.setattr(bf, "fetch_season", season)
    monkeypatch.setattr(bf, "probe_duration", lambda p: 2600)

    bf.main(["--apply", "--root", str(vid)])

    doc = json.loads((vid / show / VALIDATION_FILENAME).read_text(encoding="utf-8"))
    assert [s["season"] for s in doc["seasons"]] == [1]
