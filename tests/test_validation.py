"""Tests for the validation.json document: shapes, round-trips, no-file fallback."""

import json
from pathlib import Path
from types import SimpleNamespace

from skive.tmdb import TMDBPick
from skive.tv_match import RATIO_GREEN as TV_RATIO_GREEN
from skive.validation import (
    RATIO_GREEN, MAIN_FLOOR_S, VALIDATION_FILENAME,
    Finding, MainArbitration, arbitrate_main, band_delta, biggest_title,
    fix_staging_names, grade_main, is_issue, json_report, main_candidates,
    merge_movie_document, movie_row, print_issues, recommendations,
    resolve_main, ratio_severity, staging_name_findings,
    movie_document, tv_document, write_validation, load_validation,
    merge_tv_document, tv_row,
    probe_duration, validate_library, print_findings, run_validate,
)


# The rip-time shapes, key for key (transcode and the checker read these)
def test_movie_document_shape():
    """The movie document keeps the confirmed shape; encodes starts empty."""
    doc = movie_document(
        tmdb_id="123", title="Mamma Mia! (2008)", runtime_s=6480,
        volume_label="MAMMA_MIA", run="a1b2c3d4", main="t21",
        titles=[{"file": "Mamma Mia! (2008)_t21.mkv", "t": 21, "dur_s": 6252,
                 "ratio": 0.97, "band": "green"}],
    )
    assert list(doc) == ["tmdb", "volume_label", "run", "main", "titles", "encodes"]
    assert doc["tmdb"] == {"id": "123", "type": "movie",
                           "title": "Mamma Mia! (2008)", "runtime_s": 6480}
    assert doc["titles"] == [{"file": "Mamma Mia! (2008)_t21.mkv", "t": 21,
                              "dur_s": 6252, "ratio": 0.97, "band": "green"}]
    assert doc["encodes"] == []


def test_tv_document_shape():
    """The TV document keeps the shared identity block; the season entry
    carries the episode rows and the disc provenance index."""
    doc = tv_document(
        tmdb_id="456", title="Show (2019)", season=2,
        volume_label="SHOW_S2", run="a1b2c3d4", staged=[5, 6],
        episodes=[{"ep": 5, "file": "Show S02E05.mkv", "t": 12, "dur_s": 2611,
                   "runtime_s": 2580, "ratio": 0.99, "band": "green"}],
    )
    assert list(doc) == ["tmdb", "seasons", "encodes"]
    assert doc["tmdb"] == {"id": "456", "type": "tv",
                           "title": "Show (2019)"}
    assert doc["seasons"] == [{
        "season": 2,
        "episodes": [{"ep": 5, "file": "Show S02E05.mkv", "t": 12,
                      "dur_s": 2611, "runtime_s": 2580,
                      "ratio": 0.99, "band": "green"}],
        "discs": [{"volume_label": "SHOW_S2", "run": "a1b2c3d4",
                   "staged": [5, 6]}],
    }]
    assert doc["encodes"] == []


def test_movie_round_trip(tmp_path):
    """The full movie document — titles, recorded main, appended encodes —
    survives write -> load unchanged."""
    doc = movie_document(
        tmdb_id="123", title="Mamma Mia! (2008)", runtime_s=6480,
        volume_label="MAMMA_MIA", run="a1b2c3d4", main="t21",
        titles=[{"file": "Mamma Mia! (2008)_t21.mkv", "t": 21, "dur_s": 6252,
                 "ratio": 0.97, "band": "green"}],
    )
    doc["encodes"].append({"file": "Mamma Mia! (2008).mkv", "src": "t21",
                           "dur_s": 6249})
    path = tmp_path / "validation.json"
    write_validation(path, doc)
    assert load_validation(path) == doc


def test_tv_round_trip(tmp_path):
    """The full TV document — seasons with their episode rows and disc
    index — survives write -> load unchanged."""
    doc = tv_document(
        tmdb_id="456", title="Show (2019)", season=2,
        volume_label="SHOW_S2", run="a1b2c3d4", staged=[5, 6],
        episodes=[{"ep": 5, "file": "Show S02E05.mkv", "t": 12, "dur_s": 2611,
                   "runtime_s": 2580, "ratio": 0.99, "band": "green"},
                  {"ep": 6, "file": "Show S02E06.mkv", "t": 13, "dur_s": 2602,
                   "runtime_s": 2580, "ratio": 1.01, "band": "green"}],
    )
    path = tmp_path / "validation.json"
    write_validation(path, doc)
    assert load_validation(path) == doc


def test_round_trip_unicode_title(tmp_path):
    """A non-ascii title survives write -> load (utf-8 in, utf-8 out)."""
    doc = movie_document(
        tmdb_id="74", title="Amélie (2001)", runtime_s=7320,
        volume_label="AMELIE", run="a1b2c3d4", main="t1",
        titles=[{"file": "Amélie (2001)_t1.mkv", "t": 1, "dur_s": 7300,
                 "ratio": 1.0, "band": "green"}],
    )
    path = tmp_path / "validation.json"
    write_validation(path, doc)
    assert load_validation(path) == doc


# No validation file: callers fall back, nothing raises
def test_load_absent_file(tmp_path):
    """No file at the path: None — the no-validation-file result."""
    assert load_validation(tmp_path / "validation.json") is None


def test_load_unreadable_file(tmp_path):
    """Truncated JSON and non-object JSON both read as no validation file."""
    path = tmp_path / "validation.json"
    path.write_text('{"tmdb": {"id"', encoding="utf-8")
    assert load_validation(path) is None
    path.write_text('["not a document"]', encoding="utf-8")
    assert load_validation(path) is None


# One band constant in the codebase, imported — never copied
def test_band_is_the_tv_match_constant():
    """validation's RATIO_GREEN is tv_match's RATIO_GREEN, the same object."""
    assert RATIO_GREEN is TV_RATIO_GREEN
    assert RATIO_GREEN == (0.92, 1.04)


def test_validation_filename():
    """The document's one name in every folder it travels through."""
    assert VALIDATION_FILENAME == "validation.json"


# --- Candidate-main arbitration (issue 04, map.md decision 4) ---------------


def test_main_floor_boundary():
    """2400s is a candidate, 2399s is not: the floor is inclusive."""
    assert MAIN_FLOOR_S == 2400
    titles = [(1, 2399, 100), (2, 2400, 100)]
    assert main_candidates(titles) == [(2, 2400, 100)]


def test_grade_main_band_edges():
    """The shared band grades green at both edges, yellow just outside."""
    assert grade_main(2300, 2500) == (0.92, "green")
    assert grade_main(2600, 2500) == (1.04, "green")
    assert grade_main(2299, 2500) == (2299 / 2500, "yellow")
    assert grade_main(2601, 2500) == (2601 / 2500, "yellow")


def test_grade_main_unknown_is_never_yellow():
    """No runtime (or no duration): a blank cell — never a grade, never yellow."""
    assert grade_main(6000, None) is None
    assert grade_main(6000, 0) is None
    assert grade_main(0, 6000) is None


def test_arbitrate_sole_near_is_automatic():
    """Exactly one candidate inside the band: that one, no human needed."""
    arb = arbitrate_main([(3, 6400, 8_000_000_000)], 6480)
    assert isinstance(arb, MainArbitration)
    assert arb.kind == "sole"
    assert arb.pick == (3, 6400, 8_000_000_000)
    assert arb.near == []


def test_arbitrate_several_near_needs_human_and_carries_the_near_list():
    """Two candidates near the runtime (the Mamma Mia shape): human pick —
    near carries them in title order with exact ratios, default biggest."""
    arb = arbitrate_main([(21, 6252, 4_300_000_000), (12, 6662, 19_000_000_000)],
                         6480)
    assert arb.kind == "several"
    assert arb.near == [(12, 6662, 6662 / 6480), (21, 6252, 6252 / 6480)]
    assert arb.pick == (12, 6662, 19_000_000_000)


def test_arbitrate_several_default_is_biggest_among_the_near():
    """A yellow giant must not become the default: biggest of the NEAR ones."""
    arb = arbitrate_main([(3, 6300, 30_000_000_000),   # near, huge
                          (4, 6400, 5_000_000_000),    # near, small
                          (9, 5000, 40_000_000_000)],  # out of band, biggest overall
                         6480)
    assert arb.kind == "several"
    assert arb.near == [(3, 6300, 6300 / 6480), (4, 6400, 6400 / 6480)]
    assert arb.pick == (3, 6300, 30_000_000_000)


def test_arbitrate_band_edges_decide_near():
    """The near set grades with the shared band: at the edge is near."""
    arb = arbitrate_main([(1, 5962, 5), (2, 5961, 4)], 6480)  # 0.9201 vs 0.9199
    assert arb.kind == "sole"
    assert arb.pick == (1, 5962, 5)


def test_arbitrate_zero_near_falls_back_to_biggest_bytes_silently():
    """Candidates exist but none near the runtime: biggest-bytes fallback."""
    arb = arbitrate_main([(5, 3000, 1_000_000_000), (7, 2900, 9_000_000_000)], 6480)
    assert arb.kind == "zero"
    assert arb.pick == (7, 2900, 9_000_000_000)
    assert arb.near == []


def test_arbitrate_no_candidates_at_all_picks_none():
    """Shorts/bonus discs: nothing feature-length — pick None; the flow
    records main=None and transcode keeps its biggest-bytes fallback."""
    titles = [(1, 600, 100), (2, 900, 200)]
    arb = arbitrate_main(main_candidates(titles), 6480)
    assert arb.kind == "zero"
    assert arb.pick is None


def test_arbitrate_unknown_runtime_falls_back_without_grading():
    """No runtime: biggest-bytes fallback, near-ness never guessed."""
    arb = arbitrate_main([(5, 6000, 1_000_000_000), (7, 5000, 9_000_000_000)], None)
    assert arb.kind == "unknown"
    assert arb.pick == (7, 5000, 9_000_000_000)
    assert arb.near == []
    assert arbitrate_main([], None).pick is None


def test_biggest_title_longest_duration_when_sizes_are_zero():
    """Fast scan (every size 0): the longest duration is the fallback."""
    titles = [(2, 5400, 0), (1, 6900, 0), (3, 1200, 0)]
    assert biggest_title(titles) == (1, 6900, 0)
    assert arbitrate_main([(2, 5400, 0), (1, 6900, 0)], None).pick == (1, 6900, 0)


def test_biggest_title_prefers_bytes_over_duration():
    """Real sizes: biggest bytes wins even when a longer title exists."""
    titles = [(2, 6900, 1_000_000_000), (1, 6300, 9_000_000_000)]
    assert biggest_title(titles) == (1, 6300, 9_000_000_000)


def test_biggest_title_empty_reads_none():
    assert biggest_title([]) is None


# --- Post-rip record helpers (issue 05, map.md decision 5) ------------------


def test_movie_row_grades_the_probed_actual():
    """A probed duration at/above the floor: exact quotient banded, 2dp stored."""
    assert movie_row("Mamma Mia! (2008)_t21.mkv", 21, 6249, 6480) == {
        "file": "Mamma Mia! (2008)_t21.mkv", "t": 21, "dur_s": 6249,
        "ratio": 0.96, "band": "green"}


def test_movie_row_unknowns_and_sub_floor_stay_null():
    """Unknown duration or runtime — or a sub-floor short — keeps the grade
    blank (never yellow); dur_s itself is still recorded."""
    assert movie_row("f_t1.mkv", 1, 600, 6480) == {
        "file": "f_t1.mkv", "t": 1, "dur_s": 600, "ratio": None, "band": None}
    assert movie_row("f_t1.mkv", 1, None, 6480) == {
        "file": "f_t1.mkv", "t": 1, "dur_s": None, "ratio": None, "band": None}
    assert movie_row("f_t1.mkv", 1, 6249, None) == {
        "file": "f_t1.mkv", "t": 1, "dur_s": 6249, "ratio": None, "band": None}


def test_movie_row_band_grades_exact_quotient_ratio_stores_2dp():
    """The band grades the exact quotient, the record stores the 2dp value:
    2601/2500 reads 1.04 rounded but bands yellow on the exact 1.0404."""
    row = movie_row("f_t1.mkv", 1, 2601, 2500)
    assert row["ratio"] == 1.04
    assert row["band"] == "yellow"


def test_resolve_main_full_scan_exact_number():
    """Full scan: the file whose final _tNN equals the table number wins."""
    staged = [{"file": "M_t21.mkv", "t": 21, "dur_s": 6249, "ratio": None, "band": None}]
    graded = [{"t": 21, "dur_s": 6252, "ratio": None, "band": None}]
    assert resolve_main("t21", graded, staged, fast_scan=False) == "t21"


def test_resolve_main_full_scan_collision_roll_joins_by_duration():
    """The table number never staged (collision rolled t21 forward to t22):
    the fallback joins the table main's scanned duration ±1s."""
    staged = [{"file": "M_t22.mkv", "t": 22, "dur_s": 6251, "ratio": None, "band": None}]
    graded = [{"t": 21, "dur_s": 6252, "ratio": None, "band": None}]
    assert resolve_main("t21", graded, staged, fast_scan=False) == "t22"


def test_resolve_main_no_match_is_null():
    """Skipped decoy, no duration twin staged: main stays null."""
    staged = [{"file": "M_t02.mkv", "t": 2, "dur_s": 600, "ratio": None, "band": None}]
    graded = [{"t": 21, "dur_s": 6252, "ratio": None, "band": None}]
    assert resolve_main("t21", graded, staged, fast_scan=False) is None


def test_resolve_main_ambiguous_join_is_null():
    """Two staged files within ±1s of the scanned duration: never guess."""
    staged = [{"file": "M_t04.mkv", "t": 4, "dur_s": 7020, "ratio": None, "band": None},
              {"file": "M_t05.mkv", "t": 5, "dur_s": 7021, "ratio": None, "band": None}]
    graded = [{"t": 9, "dur_s": 7021, "ratio": None, "band": None}]
    assert resolve_main("t9", graded, staged, fast_scan=False) is None


def test_resolve_main_fast_scan_joins_by_duration_never_the_index():
    """Fast scan: the table number is a scanner index — even a staged _t0
    with the wrong duration never matches; the duration twin does."""
    staged = [{"file": "M_t0.mkv", "t": 0, "dur_s": 100, "ratio": None, "band": None},
              {"file": "M_t5.mkv", "t": 5, "dur_s": 7020, "ratio": None, "band": None}]
    graded = [{"t": 0, "dur_s": 7021, "ratio": None, "band": None}]
    assert resolve_main("t0", graded, staged, fast_scan=True) == "t5"


def test_resolve_main_fast_scan_ambiguous_or_unknown_is_null():
    """Fast scan with no duration twin — or none chosen at the table: null."""
    staged = [{"file": "M_t3.mkv", "t": 3, "dur_s": 599, "ratio": None, "band": None}]
    graded = [{"t": 0, "dur_s": 7021, "ratio": None, "band": None}]
    assert resolve_main("t0", graded, staged, fast_scan=True) is None
    assert resolve_main(None, graded, staged, fast_scan=False) is None


def test_merge_movie_document_appends_rows_and_applies_main_run_rules():
    """Same-film re-rip: old rows kept, this run's appended; this run's main
    resolution wins when present, else the old one stands; run: latest."""
    old = movie_document("157", "Mamma Mia! (2008)", 6480, "OLD", "oldrun", "t12",
                         [{"file": "M_t12.mkv", "t": 12, "dur_s": 6662,
                           "ratio": 1.03, "band": "green"}])
    new = movie_document("157", "Mamma Mia! (2008)", 6480, "NEW", "newrun", None,
                         [{"file": "M_t21.mkv", "t": 21, "dur_s": 6252,
                           "ratio": 0.96, "band": "green"}])

    merged = merge_movie_document(old, new)
    assert merged["titles"] == old["titles"] + new["titles"]
    assert merged["main"] == "t12"    # this run resolved nothing — old stands
    assert merged["run"] == "newrun"  # latest wins
    assert merged["encodes"] == []

    assert merge_movie_document(old, dict(new, main="t21"))["main"] == "t21"


def test_merge_movie_document_tolerates_rowless_old():
    """An old document without a readable titles list merges as empty."""
    new = movie_document("157", "Mamma Mia! (2008)", 6480, "NEW", "newrun", "t21",
                         [{"file": "M_t21.mkv", "t": 21, "dur_s": 6252,
                           "ratio": 0.96, "band": "green"}])
    merged = merge_movie_document({"tmdb": {"id": "157"}}, new)
    assert merged["titles"] == new["titles"]


# --- TV post-rip record helpers (issue 06, map.md decision 6) ---------------


def test_tv_row_grades_the_probed_actual():
    """A probed duration with a known runtime: exact quotient banded, 2dp
    stored, the episode's TMDB runtime recorded beside it."""
    assert tv_row(5, "Show (2019)_S02E05.mkv", 12, 2611, 2580) == {
        "ep": 5, "file": "Show (2019)_S02E05.mkv", "t": 12, "dur_s": 2611,
        "runtime_s": 2580, "ratio": 1.01, "band": "green"}


def test_tv_row_unknowns_stay_null_and_never_yellow():
    """Unknown duration or runtime keeps the grade blank (never yellow); the
    fetch's 0 reads as unknown — recorded null, never graded."""
    assert tv_row(5, "f.mkv", None, 2611, 0) == {
        "ep": 5, "file": "f.mkv", "t": None, "dur_s": 2611,
        "runtime_s": None, "ratio": None, "band": None}
    assert tv_row(5, "f.mkv", None, None, 2580) == {
        "ep": 5, "file": "f.mkv", "t": None, "dur_s": None,
        "runtime_s": 2580, "ratio": None, "band": None}


def test_tv_row_band_grades_exact_quotient_ratio_stores_2dp():
    """The band grades the exact quotient, the record stores the 2dp value:
    2601/2500 reads 1.04 rounded but bands yellow on the exact 1.0404."""
    row = tv_row(1, "f.mkv", 0, 2601, 2500)
    assert row["ratio"] == 1.04
    assert row["band"] == "yellow"


def test_merge_tv_document_appends_rows_and_replaces_by_ep():
    """Same-show, same-season re-rip: old rows kept, this run's appended; an
    ep already recorded is replaced by this run's row, not duplicated; the
    run's disc entry appends to the season's index; encodes start empty."""
    old = tv_document("456", "Show (2019)", 2, "OLD", "oldrun", [5],
                      [{"ep": 5, "file": "Show (2019)_S02E05.mkv", "t": 12,
                        "dur_s": 2611, "runtime_s": 2580, "ratio": 1.01,
                        "band": "green"}])
    new = tv_document("456", "Show (2019)", 2, "NEW", "newrun", [5, 6],
                      [{"ep": 5, "file": "Show (2019)_S02E05.mkv", "t": None,
                        "dur_s": 2600, "runtime_s": 2580, "ratio": 1.01,
                        "band": "green"},
                       {"ep": 6, "file": "Show (2019)_S02E06.mkv", "t": 13,
                        "dur_s": 2602, "runtime_s": 2580, "ratio": 1.01,
                        "band": "green"}])

    merged = merge_tv_document(old, new)
    season = merged["seasons"][0]
    assert [r["ep"] for r in season["episodes"]] == [5, 6]
    assert season["episodes"][0]["dur_s"] == 2600  # this run's row replaced
    assert season["discs"] == [
        {"volume_label": "OLD", "run": "oldrun", "staged": [5]},
        {"volume_label": "NEW", "run": "newrun", "staged": [5, 6]},
    ]
    assert merged["encodes"] == []


def test_merge_tv_document_second_season_merges_by_season():
    """A second season's rip merges into the same document: each season
    keeps its own rows and discs, the list sorts by season number whatever
    order the discs ripped in."""
    s1 = tv_document("456", "Show (2019)", 1, "S1", "run1", [1],
                     [tv_row(1, "Show (2019)_S01E01.mkv", 0, 2600, 2580)])
    s2 = tv_document("456", "Show (2019)", 2, "S2", "run2", [1],
                     [tv_row(1, "Show (2019)_S02E01.mkv", 3, 2600, 2580)])

    merged = merge_tv_document(s2, s1)  # season 1 ripped after season 2

    assert [s["season"] for s in merged["seasons"]] == [1, 2]
    assert merged["seasons"][1]["episodes"][0]["file"] == "Show (2019)_S02E01.mkv"
    assert merged["seasons"][1]["discs"] == [
        {"volume_label": "S2", "run": "run2", "staged": [1]}]


def test_merge_tv_document_migrates_one_season_shape():
    """An old one-season document (tmdb.season + episodes, no seasons key —
    the first schema) migrates on the merge: its rows survive as a seasons
    entry with no disc provenance, this run's shape throughout."""
    old = {"tmdb": {"id": "456", "type": "tv", "title": "Show (2019)",
                    "season": 3},
           "volume_label": "OLD", "run": "oldrun",
           "episodes": [tv_row(1, "Show (2019)_S03E01.mkv", 0, 2600, 2580)],
           "encodes": []}
    new = tv_document("456", "Show (2019)", 1, "S1", "newrun", [1],
                      [tv_row(1, "Show (2019)_S01E01.mkv", 0, 2600, 2580)])

    merged = merge_tv_document(old, new)

    assert [s["season"] for s in merged["seasons"]] == [1, 3]
    assert merged["seasons"][1]["episodes"][0]["file"] == "Show (2019)_S03E01.mkv"
    assert merged["seasons"][1]["discs"] == []  # the old shape had no index
    assert "season" not in merged["tmdb"]


def test_merge_tv_document_remerge_same_run_replaces_disc_entry():
    """Idempotent re-merge: the same run's disc entry replaces its own old
    one, never duplicates; episode rows replace per ep as always."""
    doc = tv_document("456", "Show (2019)", 2, "S2", "run1", [5],
                      [tv_row(5, "Show (2019)_S02E05.mkv", 0, 2600, 2580)])

    merged = merge_tv_document(doc, doc)

    assert merged["seasons"][0]["discs"] == doc["seasons"][0]["discs"]
    assert [r["ep"] for r in merged["seasons"][0]["episodes"]] == [5]


def test_merge_tv_document_tolerates_rowless_old():
    """An old document without a readable episodes list merges as empty."""
    new = tv_document("456", "Show (2019)", 2, "NEW", "newrun", [], [])
    merged = merge_tv_document({"tmdb": {"id": "456"}}, new)
    assert merged["seasons"][0]["episodes"] == []


# --- Library checker (skive validate) ----------------------------------------
# Fake mounts only: every fixture lives in tmp_path, the probes are injected
# run() fakes, and TMDB is a fake — no test reads or writes outside tmp_path.

MAMMA = "Mamma Mia! (2008)"


def _fake_probe(durations: dict[str, int]):
    """An injectable run(): ffprobe replies keyed by the probed file's name;
    any other file fails to probe (nonzero rc, empty stdout)."""
    def run(cmd, capture_output, text):
        name = Path(cmd[-1]).name
        if name in durations:
            return SimpleNamespace(returncode=0, stdout=f"{durations[name]}.0\n",
                                   stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="probe failed")
    return run


def _no_search(kind, query):
    raise AssertionError(f"TMDB search must not run for {kind} {query!r}")


def _no_season_list(tv_id, season):
    raise AssertionError(f"TMDB season fetch must not run for tv {tv_id} S{season:02d}")


def _library(tmp_path):
    """An empty fake share: library/movies + library/tv."""
    root = tmp_path / "library"
    (root / "movies").mkdir(parents=True)
    (root / "tv").mkdir(parents=True)
    return root


def _mamma_doc():
    return movie_document(tmdb_id="471", title=MAMMA, runtime_s=6480,
                          volume_label="MAMMA_MIA", run="a1b2c3d4", main="t21",
                          titles=[])


def _mamma_search():
    def search(kind, query):
        assert (kind, query) == ("movie", "Mamma Mia!")
        return TMDBPick("Mamma Mia!", "2008", "en", "movie", "471")
    return search


def _details(runtime_s):
    def fetch_details(type_, id_):
        return TMDBPick("Mamma Mia!", "2008", "en", "movie", id_,
                        runtime_secs=runtime_s)
    return fetch_details


# probe_duration — the rename_tv_fast probe shape

def test_probe_duration_reads_ffprobe_csv():
    """format=duration csv output parses to whole seconds; the command shape
    matches the rename_tv_fast probe."""
    calls = []

    def run(cmd, capture_output, text):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="6251.0\n", stderr="")

    assert probe_duration(Path("/x/M.mkv"), run=run) == 6251
    assert calls[0][:5] == ["ffprobe", "-v", "error", "-show_entries",
                            "format=duration"]
    assert calls[0][5:7] == ["-of", "csv=p=0"]
    assert calls[0][-1] == "/x/M.mkv"


def test_probe_duration_unreadable_is_none_never_a_crash():
    """Nonzero rc, garbage stdout, a missing ffprobe: all None — the caller
    reports the file unreadable instead of crashing the walk."""
    def bad_rc(cmd, capture_output, text):
        return SimpleNamespace(returncode=1, stdout="", stderr="no")
    def garbage(cmd, capture_output, text):
        return SimpleNamespace(returncode=0, stdout="N/A\n", stderr="")
    def no_tool(cmd, capture_output, text):
        raise FileNotFoundError("ffprobe")

    assert probe_duration(Path("/x/M.mkv"), run=bad_rc) is None
    assert probe_duration(Path("/x/M.mkv"), run=garbage) is None
    assert probe_duration(Path("/x/M.mkv"), run=no_tool) is None


# Movies: identity from the doc, else name+year search (a LEAD)

def test_validate_movie_doc_folder_grades_main_encode(tmp_path):
    """A doc folder needs no TMDB: the `<folder>.mkv` main encode probes and
    grades against the doc's recorded runtime."""
    root = _library(tmp_path)
    folder = root / "movies" / MAMMA
    folder.mkdir()
    (folder / f"{MAMMA}.mkv").touch()
    write_validation(folder / VALIDATION_FILENAME, _mamma_doc())

    findings = validate_library(root, run=_fake_probe({f"{MAMMA}.mkv": 6252}),
                                search=_no_search, season_list=_no_season_list)

    assert findings == [Finding("movies/" + MAMMA, "ratio", f"{MAMMA}.mkv  0.96",
                                "green", f"{MAMMA}.mkv", 0.96, 0.04)]


def test_validate_movie_far_off_band_ratio_reads_red(tmp_path):
    """0.77 — beyond the band by more than the PAL-vs-NTSC speedup: the red
    alarm tier, not the yellow plausible-cut zone."""
    root = _library(tmp_path)
    folder = root / "movies" / MAMMA
    folder.mkdir()
    (folder / f"{MAMMA}.mkv").touch()
    write_validation(folder / VALIDATION_FILENAME, _mamma_doc())

    findings = validate_library(root, run=_fake_probe({f"{MAMMA}.mkv": 5000}),
                                search=_no_search, season_list=_no_season_list)

    assert findings == [Finding("movies/" + MAMMA, "ratio", f"{MAMMA}.mkv  0.77",
                                "red", f"{MAMMA}.mkv", 0.77, -0.15)]


def test_validate_movie_unknown_runtime_is_blank_never_yellow(tmp_path):
    """The doc names the id but carries no runtime: a blank cell — never a
    grade, never yellow (grade_main's no-false-yellow rule)."""
    root = _library(tmp_path)
    folder = root / "movies" / MAMMA
    folder.mkdir()
    (folder / f"{MAMMA}.mkv").touch()
    doc = {"tmdb": {"id": "471", "type": "movie", "title": MAMMA,
                    "runtime_s": None}, "main": None}
    write_validation(folder / VALIDATION_FILENAME, doc)

    findings = validate_library(root, run=_fake_probe({f"{MAMMA}.mkv": 6662}),
                                search=_no_search, season_list=_no_season_list)

    assert findings == [Finding("movies/" + MAMMA, "ratio", f"{MAMMA}.mkv",
                                path=f"{MAMMA}.mkv")]


def test_validate_movie_legacy_resolves_via_name_year_search(tmp_path):
    """No doc: the trailing (YYYY) splits off, the first search hit names the
    id (a lead), the details fetch carries the runtime."""
    root = _library(tmp_path)
    folder = root / "movies" / MAMMA
    folder.mkdir()
    (folder / f"{MAMMA}.mkv").touch()

    findings = validate_library(root, run=_fake_probe({f"{MAMMA}.mkv": 6662}),
                                search=_mamma_search(),
                                fetch_details=_details(6480))

    assert findings == [Finding("movies/" + MAMMA, "ratio", f"{MAMMA}.mkv  1.03",
                                "green", f"{MAMMA}.mkv", 1.03, 0.01)]


def test_validate_mamma_mia_fixture_two_candidates_and_staging_names(tmp_path):
    """The forensic: a legacy folder whose main encode and full-length extra
    both grade near the film runtime (1.03 and 0.96 — ratio alone reads green,
    the map says so) get one ratio row per graded file, the `_tNN` extras get
    staging-name rows, and the 300s short is never graded. Nothing is
    written: the tree reads back exactly as built."""
    root = _library(tmp_path)
    folder = root / "movies" / MAMMA
    (folder / "extras").mkdir(parents=True)
    (folder / f"{MAMMA}.mkv").touch()
    (folder / "extras" / f"{MAMMA}_t21.mkv").touch()
    (folder / "extras" / f"{MAMMA}_t05.mkv").touch()

    findings = validate_library(
        root,
        run=_fake_probe({f"{MAMMA}.mkv": 6662, f"{MAMMA}_t21.mkv": 6251,
                         f"{MAMMA}_t05.mkv": 300}),
        search=_mamma_search(), fetch_details=_details(6480))

    assert [f for f in findings if f.check == "ratio"] == [
        Finding("movies/" + MAMMA, "ratio", f"{MAMMA}.mkv  1.03", "green",
                f"{MAMMA}.mkv", 1.03, 0.01),
        Finding("movies/" + MAMMA, "ratio", f"extras/{MAMMA}_t21.mkv  0.96",
                "green", f"extras/{MAMMA}_t21.mkv", 0.96, 0.04),
    ]
    assert sorted(f.detail for f in findings if f.check == "staging name") == [
        f"extras/{MAMMA}_t05.mkv", f"extras/{MAMMA}_t21.mkv"]
    assert not [f for f in findings if f.check in ("unidentified", "duplicate id")]
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*")) == [
        "movies", f"movies/{MAMMA}", f"movies/{MAMMA}/{MAMMA}.mkv",
        f"movies/{MAMMA}/extras", f"movies/{MAMMA}/extras/{MAMMA}_t05.mkv",
        f"movies/{MAMMA}/extras/{MAMMA}_t21.mkv", "tv"]


def test_validate_movie_no_doc_main_is_biggest_root_mkv(tmp_path):
    """Legacy folder without a `<folder>.mkv`: the biggest .mkv in the folder
    root is the main encode; root shorts below the main floor stay ungraded."""
    root = _library(tmp_path)
    folder = root / "movies" / "Two Files (2001)"
    folder.mkdir()
    (folder / "aaa.mkv").write_bytes(b"x" * 10)
    (folder / "bbb.mkv").write_bytes(b"x" * 1000)

    findings = validate_library(root,
                                run=_fake_probe({"aaa.mkv": 100, "bbb.mkv": 6300}),
                                search=lambda k, q: TMDBPick("Two Files", "2001",
                                                             "en", "movie", "5"),
                                fetch_details=_details(6000))

    assert findings == [Finding("movies/Two Files (2001)", "ratio", "bbb.mkv  1.05",
                                "yellow", "bbb.mkv", 1.05, 0.01)]


# Movies: unresolvable identity is a row, never an error

def test_validate_movie_no_year_is_unidentified_row(tmp_path):
    """No validation.json and no (Year): the search has nothing to run — an
    unidentified row, walk goes on."""
    root = _library(tmp_path)
    (root / "movies" / "Det fulle DK").mkdir()

    findings = validate_library(root, run=_fake_probe({}), search=_no_search)

    assert findings == [Finding("movies/Det fulle DK", "unidentified",
                                "no validation.json and no (Year) in the folder name")]


def test_validate_movie_search_no_match_is_unidentified_row(tmp_path):
    root = _library(tmp_path)
    (root / "movies" / MAMMA).mkdir()

    findings = validate_library(
        root, run=_fake_probe({}),
        search=lambda k, q: None)

    assert findings == [Finding("movies/" + MAMMA, "unidentified",
                                'TMDB search: no match for "Mamma Mia!"')]


def test_validate_movie_search_failure_warns_once_and_unidentifies(tmp_path, capsys):
    """TMDB down: one stderr warning, an unidentified row, no crash — the
    walk degrades per-folder."""
    root = _library(tmp_path)
    (root / "movies" / MAMMA).mkdir()

    def down(kind, query):
        raise RuntimeError("TMDB request failed: HTTP 500")

    findings = validate_library(root, run=_fake_probe({}), search=down)

    assert findings == [Finding("movies/" + MAMMA, "unidentified",
                                'TMDB search failed for "Mamma Mia!"')]
    assert "warn  TMDB search failed" in capsys.readouterr().err


def test_validate_movie_details_fetch_failure_unidentifies(tmp_path, capsys):
    """Search resolved but the runtime fetch died: unidentified row plus one
    warning — a legacy folder without a runtime cannot be graded."""
    root = _library(tmp_path)
    (root / "movies" / MAMMA).mkdir()

    def down(type_, id_):
        raise RuntimeError("TMDB request failed: timeout")

    findings = validate_library(root, run=_fake_probe({}),
                                search=_mamma_search(), fetch_details=down)

    assert findings == [Finding("movies/" + MAMMA, "unidentified",
                                "TMDB details fetch failed for id 471")]
    assert "TMDB details fetch failed" in capsys.readouterr().err


def test_validate_probe_failure_is_unreadable_row_not_a_crash(tmp_path):
    """The main encode that won't probe: an unreadable row, the folder's other
    checks keep running."""
    root = _library(tmp_path)
    folder = root / "movies" / MAMMA
    folder.mkdir()
    (folder / f"{MAMMA}.mkv").touch()
    (folder / "extras").mkdir()
    (folder / "extras" / f"{MAMMA}_t09.mkv").touch()
    write_validation(folder / VALIDATION_FILENAME, _mamma_doc())

    findings = validate_library(root,
                                run=_fake_probe({f"{MAMMA}_t09.mkv": 300}),
                                search=_no_search)

    assert findings == [
        Finding("movies/" + MAMMA, "unreadable", f"{MAMMA}.mkv: duration unreadable",
                path=f"{MAMMA}.mkv"),
        Finding("movies/" + MAMMA, "staging name", f"extras/{MAMMA}_t09.mkv",
                path=f"extras/{MAMMA}_t09.mkv"),
    ]


# TV: doc-based and season-list-based checks

def _show_tree(tmp_path):
    show = tmp_path / "library" / "tv" / "Show (2019)"
    season = show / "Season 02"
    season.mkdir(parents=True)
    return show, season


def _show_doc():
    return tv_document("456", "Show (2019)", 2, "SHOW_S2", "a1b2c3d4", [1, 2],
                       [tv_row(1, "Show (2019) - S02E01.mkv", 3, 2600, 2580),
                        tv_row(2, "Show (2019) - S02E02.mkv", 4, 2601, 2580)])


def test_validate_tv_doc_fallback_flags_missing_episode(tmp_path, capsys):
    """Doc-based missing episodes: the season fetch fails (one warning), the
    doc's episode rows stand in as the expected set, and the present episode
    grades against its doc runtime."""
    show, season = _show_tree(tmp_path)
    (season / "Show (2019) - S02E01.mkv").touch()
    write_validation(season / VALIDATION_FILENAME, _show_doc())

    def down(tv_id, n):
        raise RuntimeError("TMDB request failed: down")

    findings = validate_library(
        tmp_path / "library",
        run=_fake_probe({"Show (2019) - S02E01.mkv": 2600}),
        search=_no_search, season_list=down)

    assert findings == [
        Finding("tv/Show (2019)", "ratio",
                "Season 02/Show (2019) - S02E01.mkv  1.01", "green",
                "Season 02/Show (2019) - S02E01.mkv", 1.01, 0.03),
        Finding("tv/Show (2019)", "missing episodes",
                "Season 02 missing E02 (validation.json)"),
    ]
    assert "TMDB season fetch failed for Show (2019) Season 02" in capsys.readouterr().err


def test_validate_tv_season_list_preferred_for_missing(tmp_path):
    """When the season list fetches, missing episodes grade against it — a
    doc folder that only ever held part of the season still reports the gap,
    with runs collapsed (E03, E05-E06)."""
    show, season = _show_tree(tmp_path)
    for ep in (1, 2, 4):
        (season / f"Show (2019) - S02E{ep:02d}.mkv").touch()
    write_validation(season / VALIDATION_FILENAME, _show_doc())

    def season_list(tv_id, n):
        assert (tv_id, n) == ("456", 2)
        return [(e, f"Ep {e}", 2580) for e in range(1, 7)]

    findings = validate_library(
        tmp_path / "library",
        run=_fake_probe({f"Show (2019) - S02E{e:02d}.mkv": 2600 for e in (1, 2, 4)}),
        search=_no_search, season_list=season_list)

    missing = [f for f in findings if f.check == "missing episodes"]
    assert missing == [Finding("tv/Show (2019)", "missing episodes",
                               "Season 02 missing E03, E05-E06 (TMDB season list)")]
    assert [f.detail for f in findings if f.check == "ratio"] == [
        f"Season 02/Show (2019) - S02E0{e}.mkv  1.01" for e in (1, 2, 4)]


def test_validate_tv_specials_folder_is_season_0(tmp_path):
    """The `Specials/` folder the pipeline writes for season 0 walks as a
    season: its episodes get ratio rows and the missing-episode check grades
    against TMDB season 0."""
    show = tmp_path / "library" / "tv" / "Show (2010)"
    specials = show / "Specials"
    specials.mkdir(parents=True)
    (specials / "Show (2010) - S00E01.mkv").touch()
    write_validation(specials / VALIDATION_FILENAME,
                     tv_document("456", "Show (2010)", 0, "SHOW_S0", "a1b2c3d4", [1],
                                 [tv_row(1, "Show (2010) - S00E01.mkv", 3,
                                         2580, 2580)]))

    def season_list(tv_id, n):
        assert (tv_id, n) == ("456", 0)
        return [(1, "Special 1", 2580), (2, "Special 2", 2580)]

    findings = validate_library(
        tmp_path / "library",
        run=_fake_probe({"Show (2010) - S00E01.mkv": 2580}),
        search=_no_search, season_list=season_list)

    assert findings == [
        Finding("tv/Show (2010)", "ratio",
                "Specials/Show (2010) - S00E01.mkv  1.00", "green",
                "Specials/Show (2010) - S00E01.mkv", 1.00, 0.04),
        Finding("tv/Show (2010)", "missing episodes",
                "Season 00 missing E02 (TMDB season list)"),
    ]


def test_validate_tv_double_episode_grades_sum_and_is_never_missing(tmp_path):
    """A double (`S02E05-E06.mkv`, one file, never split) is one ratio row
    graded against the SUM of the range's runtimes, and covers E06 in the
    missing check — no false E06-missing, no 2.0 false yellow."""
    show, season = _show_tree(tmp_path)
    (season / "Show (2019) - S02E05-E06.mkv").touch()
    write_validation(season / VALIDATION_FILENAME,
                     tv_document("456", "Show (2019)", 2, "SHOW_S2", "a1b2c3d4", [5],
                                 [tv_row(5, "Show (2019) - S02E05-E06.mkv", 9,
                                         5160, 2580)]))

    def season_list(tv_id, n):
        assert (tv_id, n) == ("456", 2)
        return [(5, "Ep 5", 2580), (6, "Ep 6", 2580)]

    findings = validate_library(
        tmp_path / "library",
        run=_fake_probe({"Show (2019) - S02E05-E06.mkv": 5160}),
        search=_no_search, season_list=season_list)

    assert findings == [
        Finding("tv/Show (2019)", "ratio",
                "Season 02/Show (2019) - S02E05-E06.mkv  E05-E06  1.00",
                "green", "Season 02/Show (2019) - S02E05-E06.mkv", 1.00, 0.04),
    ]


def test_validate_tv_whole_show_doc_reads_only_its_season(tmp_path):
    """The whole-show document the rip flow now writes (every season, one
    file) copied into each Season folder: a folder's rows come from its own
    season's entry only — the other seasons' rows never leak in."""
    show = tmp_path / "library" / "tv" / "Show (2019)"
    s1, s2 = show / "Season 01", show / "Season 02"
    s1.mkdir(parents=True)
    s2.mkdir()
    (s1 / "Show (2019) - S01E01.mkv").touch()
    (s2 / "Show (2019) - S02E01.mkv").touch()
    doc = merge_tv_document(
        tv_document("456", "Show (2019)", 1, "S1", "run1", [1],
                    [tv_row(1, "Show (2019) - S01E01.mkv", 0, 1800, 1800)]),
        tv_document("456", "Show (2019)", 2, "S2", "run2", [1],
                    [tv_row(1, "Show (2019) - S02E01.mkv", 0, 2600, 2580)]))
    write_validation(s1 / VALIDATION_FILENAME, doc)
    write_validation(s2 / VALIDATION_FILENAME, doc)

    findings = validate_library(
        tmp_path / "library",
        run=_fake_probe({"Show (2019) - S01E01.mkv": 1800,
                         "Show (2019) - S02E01.mkv": 2600}),
        search=_no_search, season_list=lambda tv_id, n: [])

    assert findings == [
        Finding("tv/Show (2019)", "ratio",
                "Season 01/Show (2019) - S01E01.mkv  1.00",
                "green", "Season 01/Show (2019) - S01E01.mkv", 1.00, 0.04),
        Finding("tv/Show (2019)", "ratio",
                "Season 02/Show (2019) - S02E01.mkv  1.01",
                "green", "Season 02/Show (2019) - S02E01.mkv", 1.01, 0.03),
    ]


def test_validate_tv_legacy_uses_search_and_season_list(tmp_path):
    """A legacy show: name+year search names the id, the season list carries
    both the expected episode set and the per-episode runtimes."""
    show = tmp_path / "library" / "tv" / "Show (2016)"
    season = show / "Season 01"
    season.mkdir(parents=True)
    (season / "Show (2016) - S01E01.mkv").touch()
    (season / "Show (2016) - S01E02.mkv").touch()

    def search(kind, query):
        assert (kind, query) == ("tv", "Show")
        return TMDBPick("Show", "2016", "en", "tv", "9")

    def season_list(tv_id, n):
        assert (tv_id, n) == ("9", 1)
        return [(1, "a", 1800), (2, "b", 1800), (3, "c", 1800)]

    findings = validate_library(
        tmp_path / "library",
        run=_fake_probe({"Show (2016) - S01E01.mkv": 1800,
                         "Show (2016) - S01E02.mkv": 1900}),
        search=search, season_list=season_list)

    assert findings == [
        Finding("tv/Show (2016)", "ratio", "Season 01/Show (2016) - S01E01.mkv  1.00",
                "green", "Season 01/Show (2016) - S01E01.mkv", 1.00, 0.04),
        Finding("tv/Show (2016)", "ratio", "Season 01/Show (2016) - S01E02.mkv  1.06",
                "yellow", "Season 01/Show (2016) - S01E02.mkv", 1.06, 0.02),
        Finding("tv/Show (2016)", "missing episodes",
                "Season 01 missing E03 (TMDB season list)"),
    ]


def test_validate_tv_unresolved_identity_skips_missing_and_ratio(tmp_path):
    """Unresolvable show: the unidentified row plus the no-identity checks
    (staging names); missing episodes skip rather than guess."""
    show = tmp_path / "library" / "tv" / "Show (2016)"
    (show / "Season 01").mkdir(parents=True)
    (show / "extras").mkdir()
    (show / "Season 01" / "Show (2016) - S01E01.mkv").touch()
    (show / "extras" / "Show (2016)_t03.mkv").touch()

    findings = validate_library(tmp_path / "library", run=_fake_probe({}),
                                search=lambda k, q: None,
                                season_list=_no_season_list)

    assert findings == [
        Finding("tv/Show (2016)", "unidentified", 'TMDB search: no match for "Show"'),
        Finding("tv/Show (2016)", "staging name", "extras/Show (2016)_t03.mkv",
                path="extras/Show (2016)_t03.mkv"),
    ]


def test_validate_tv_episode_without_runtime_is_blank(tmp_path):
    """An episode the sources give no runtime for: a blank ratio cell — never
    yellow (TMDB lists it with none; the doc records null)."""
    show, season = _show_tree(tmp_path)
    (season / "Show (2019) - S02E05.mkv").touch()
    doc = tv_document("456", "Show (2019)", 2, "SHOW_S2", "a1b2c3d4", [5],
                      [tv_row(5, "Show (2019) - S02E05.mkv", None, 2600, None)])
    write_validation(season / VALIDATION_FILENAME, doc)

    findings = validate_library(
        tmp_path / "library",
        run=_fake_probe({"Show (2019) - S02E05.mkv": 2600}),
        search=_no_search, season_list=lambda tv_id, n: [])

    assert findings == [Finding("tv/Show (2019)", "ratio",
                                "Season 02/Show (2019) - S02E05.mkv",
                                path="Season 02/Show (2019) - S02E05.mkv")]


def test_validate_tv_unreadable_episode_is_a_row_not_a_crash(tmp_path):
    """An episode file that won't probe: an unreadable row, the season's
    missing-episode check keeps running."""
    show, season = _show_tree(tmp_path)
    (season / "Show (2019) - S02E01.mkv").touch()
    (season / "Show (2019) - S02E02.mkv").touch()
    doc = tv_document("456", "Show (2019)", 2, "SHOW_S2", "a1b2c3d4", [1],
                      [tv_row(1, "Show (2019) - S02E01.mkv", 3, 2600, 2580)])
    write_validation(season / VALIDATION_FILENAME, doc)

    findings = validate_library(
        tmp_path / "library",
        run=_fake_probe({"Show (2019) - S02E01.mkv": 2600}),
        search=_no_search, season_list=lambda tv_id, n: [])

    assert findings == [
        Finding("tv/Show (2019)", "ratio",
                "Season 02/Show (2019) - S02E01.mkv  1.01", "green",
                "Season 02/Show (2019) - S02E01.mkv", 1.01, 0.03),
        Finding("tv/Show (2019)", "unreadable",
                "Season 02/Show (2019) - S02E02.mkv: duration unreadable",
                path="Season 02/Show (2019) - S02E02.mkv"),
    ]


# Duplicate ids across folders

def test_validate_duplicate_id_across_folders_flagged_once(tmp_path):
    """One film under two folders — a doc folder and a legacy folder whose
    name+year search lands on the same id — is one finding row."""
    root = _library(tmp_path)
    documented = root / "movies" / MAMMA
    documented.mkdir()
    write_validation(documented / VALIDATION_FILENAME, _mamma_doc())
    legacy = root / "movies" / "Mamma Mia 2 (2008)"
    legacy.mkdir()
    (legacy / "Mamma Mia 2 (2008).mkv").touch()

    def search(kind, query):
        # the second folder's NAME lands on the same film — the Mamma shape
        assert (kind, query) == ("movie", "Mamma Mia 2")
        return TMDBPick("Mamma Mia!", "2008", "en", "movie", "471")

    findings = validate_library(root, run=_fake_probe({"Mamma Mia 2 (2008).mkv": 6252}),
                                search=search, fetch_details=_details(6480))

    duplicates = [f for f in findings if f.check == "duplicate id"]
    assert duplicates == [Finding("movies/Mamma Mia 2 (2008)", "duplicate id",
                                  "tmdb movie 471 also under movies/" + MAMMA)]
    # one legacy ratio row (the re-encode under the second folder) + the dup row
    assert [f.detail for f in findings if f.check == "ratio"] == [
        "Mamma Mia 2 (2008).mkv  0.96"]


def test_validate_single_id_never_duplicates(tmp_path):
    root = _library(tmp_path)
    folder = root / "movies" / MAMMA
    folder.mkdir()
    write_validation(folder / VALIDATION_FILENAME, _mamma_doc())

    assert validate_library(root, run=_fake_probe({}), search=_no_search) == []


# The walk, the table, and the entry

def test_validate_empty_library_is_no_findings(tmp_path):
    root = _library(tmp_path)
    assert validate_library(root, run=_fake_probe({}), search=_no_search) == []


def test_validate_missing_library_roots_read_no_findings(tmp_path):
    """Neither library/movies nor library/tv under the root: nothing walks,
    nothing raises."""
    assert validate_library(tmp_path / "empty", run=_fake_probe({}),
                            search=_no_search) == []


def test_print_findings_renders_the_one_table(capsys):
    """Findings render as rows with the Δ column; an empty walk prints the
    clean summary. Short names: the table must also read at 80 columns."""
    findings = [Finding("movies/Up (2009)", "ratio", "Up (2009).mkv  0.77", "red",
                        "Up (2009).mkv", 0.77, -0.15),
                Finding("movies/Up (2009)", "staging name", "extras/Up (2009)_t21.mkv",
                        path="extras/Up (2009)_t21.mkv")]
    print_findings(findings, Path("/mnt/videos"))
    out = capsys.readouterr().out
    assert "Library validation" in out
    assert "ratio" in out and "staging name" in out
    assert "-0.15" in out
    assert "Up (2009)" in out

    print_findings([], Path("/mnt/videos"))
    assert "no findings" in capsys.readouterr().out


# The severity tiers, the issues view, the action plan, and JSON

def test_ratio_severity_tiers_and_band_delta():
    """green inside the band; yellow just outside; red once the overshoot
    exceeds the PAL-vs-NTSC speedup — and delta carries the 0.xx amount."""
    assert ratio_severity(0.95) == "green"
    assert ratio_severity(0.92) == "green" and ratio_severity(1.04) == "green"
    assert ratio_severity(0.90) == "yellow" and ratio_severity(1.05) == "yellow"
    assert ratio_severity(0.87) == "red" and ratio_severity(1.09) == "red"
    assert band_delta(1.00) == 0.04      # margin inside the norm
    assert band_delta(0.95) == 0.03
    assert band_delta(0.90) == -0.02     # short of the norm
    assert band_delta(1.17) == 0.13      # over the norm


def test_validate_movie_just_off_band_ratio_reads_yellow(tmp_path):
    """0.91 — outside the band but within the PAL-vs-NTSC window of it:
    yellow, the plausible-cut zone, not the red alarm."""
    root = _library(tmp_path)
    folder = root / "movies" / MAMMA
    folder.mkdir()
    (folder / f"{MAMMA}.mkv").touch()
    write_validation(folder / VALIDATION_FILENAME, _mamma_doc())

    findings = validate_library(root, run=_fake_probe({f"{MAMMA}.mkv": 5900}),
                                search=_no_search, season_list=_no_season_list)

    assert findings == [Finding("movies/" + MAMMA, "ratio", f"{MAMMA}.mkv  0.91",
                                "yellow", f"{MAMMA}.mkv", 0.91, -0.01)]


def test_is_issue_off_band_extras_and_every_other_check():
    """Green mains are not issues; graded extras are (the two-candidates
    lead); off-band rows and every non-ratio check are."""
    green = Finding("movies/A (2000)", "ratio", "A (2000).mkv  0.96", "green",
                    "A (2000).mkv", 0.96, 0.04)
    green_extra = Finding("movies/A (2000)", "ratio",
                          "extras/A (2000)_t01.mkv  0.96", "green",
                          "extras/A (2000)_t01.mkv", 0.96, 0.04)
    red = Finding("movies/A (2000)", "ratio", "A (2000).mkv  0.16", "red",
                  "A (2000).mkv", 0.16, -0.76)
    tv_green = Finding("tv/S (2019)", "ratio", "Season 01/S - S01E01.mkv  1.00",
                       "green", "Season 01/S - S01E01.mkv", 1.00, 0.04)
    staging = Finding("movies/A (2000)", "staging name", "extras/A (2000)_t00.mkv",
                      path="extras/A (2000)_t00.mkv")
    assert not is_issue(green) and not is_issue(tv_green)
    assert is_issue(green_extra) and is_issue(red) and is_issue(staging)


def test_recommendations_worst_first_with_action_wording():
    """Red folders lead, staging-only folders trail; the lines name the
    file, the reading, and what to do."""
    findings = [
        Finding("movies/Ok (2001)", "ratio", "Ok (2001).mkv  0.95", "green",
                "Ok (2001).mkv", 0.95, 0.03),
        Finding("movies/Da Vinci (2006)", "staging name", "extras/x_t00.mkv",
                path="extras/x_t00.mkv"),
        Finding("movies/Da Vinci (2006)", "staging name", "extras/x_t01.mkv",
                path="extras/x_t01.mkv"),
        Finding("movies/Bad (2009)", "ratio", "Bad (2009).mkv  0.16", "red",
                "Bad (2009).mkv", 0.16, -0.76),
    ]
    plan = recommendations(findings)
    assert [e["folder"] for e in plan] == ["movies/Bad (2009)",
                                           "movies/Da Vinci (2006)"]
    assert plan[0]["priority"] == "red"
    assert "76% short" in plan[0]["actions"][0]
    assert "PAL-vs-NTSC" in plan[0]["actions"][0]
    assert plan[1]["priority"] == "note"
    assert plan[1]["actions"] == ["2 file(s) still carry `_tNN` staging "
                                  "labels — run `skive validate --fix` to "
                                  "rename them"]


def test_print_issues_collapses_staging_and_prints_the_plan(capsys):
    """The issues view drops green mains, collapses staging names to one row
    per folder, and ends in the action plan."""
    findings = [
        Finding("movies/A (2000)", "ratio", "A (2000).mkv  0.95", "green",
                "A (2000).mkv", 0.95, 0.03),
        Finding("movies/A (2000)", "ratio", "extras/A (2000)_t01.mkv  0.96",
                "green", "extras/A (2000)_t01.mkv", 0.96, 0.04),
        Finding("movies/A (2000)", "staging name", "extras/A (2000)_t01.mkv",
                path="extras/A (2000)_t01.mkv"),
        Finding("movies/A (2000)", "staging name", "extras/A (2000)_t02.mkv",
                path="extras/A (2000)_t02.mkv"),
    ]
    print_issues(findings, Path("/mnt/videos"))
    out = capsys.readouterr().out
    assert "issues only" in out
    assert "2 files" in out and "_tNN names" in out
    assert "A (2000).mkv  0.95" not in out        # the green main stays out
    assert "action plan" in out
    assert "two-candidates" in out

    print_issues([], Path("/mnt/videos"))
    assert "no issues" in capsys.readouterr().out


def test_json_report_shape_counts_and_issues_filter():
    """The JSON report carries the band definitions, parsed ratio/delta per
    row, summary counts, and the plan; issues=True drops green mains."""
    findings = [
        Finding("movies/A (2000)", "ratio", "A (2000).mkv  0.77", "red",
                "A (2000).mkv", 0.77, -0.15),
        Finding("tv/S (2019)", "ratio", "Season 01/S (2019) - S01E01.mkv  1.00",
                "green", "Season 01/S (2019) - S01E01.mkv", 1.00, 0.04),
        Finding("movies/A (2000)", "staging name", "extras/A (2000)_t02.mkv",
                path="extras/A (2000)_t02.mkv"),
    ]
    report = json_report(findings, Path("/mnt/videos/library"))
    assert list(report)[-1] == "recommendations"  # after findings, not buried
    assert report["ratio_band"] == {"norm": [0.92, 1.04],
                                    "red_beyond": [0.8783, 1.0817]}
    assert report["summary"]["ratio"] == {"green": 1, "yellow": 0, "red": 1,
                                          "blank": 0}
    assert report["summary"]["other"] == {"staging name": 1}
    first = report["findings"][0]
    assert (first["folder"], first["check"], first["band"],
            first["ratio"], first["delta"]) == ("movies/A (2000)", "ratio",
                                                "red", 0.77, -0.15)
    assert "PAL-vs-NTSC" in first["recommendation"]  # per-row, self-contained
    assert report["findings"][1]["recommendation"] is None  # green TV main
    staging_row = report["findings"][2]
    assert "--fix" in staging_row["recommendation"]
    assert report["recommendations"][0]["priority"] == "red"

    issues = json_report(findings, Path("/mnt/videos/library"), issues=True)
    assert [f["check"] for f in issues["findings"]] == ["ratio", "staging name"]


def test_staging_name_catches_the_old_e_labels_too(tmp_path):
    """_eNN is the older extras staging label; it pollutes exactly like
    _tNN and reads as a finding."""
    folder = tmp_path / "M (2000)"
    (folder / "extras").mkdir(parents=True)
    (folder / "extras" / "M (2000)_t01.mkv").touch()
    (folder / "extras" / "M (2000)_e07.mkv").touch()

    rows = staging_name_findings(folder, "movies/M (2000)")

    assert sorted(r.path for r in rows) == [
        "extras/M (2000)_e07.mkv", "extras/M (2000)_t01.mkv"]


def test_fix_staging_names_renames_extras_labels(tmp_path):
    """--fix: the label's number becomes ` - Extra NN`, .pN parts survive,
    taken numbers roll forward, files outside extras/ are never touched,
    and a second run finds nothing left to rename."""
    root = tmp_path / "library"
    ex = root / "movies" / "Mamma Mia! (2008)" / "extras"
    ex.mkdir(parents=True)
    (ex / "Mamma Mia! (2008)_t05.p2.mkv").write_text("b")
    (ex / "Mamma Mia! (2008)_t21.mkv").write_text("a")
    (ex / "Mamma Mia! (2008) - Extra 05.p2.mkv").write_text("taken")
    tv_ex = root / "tv" / "Show (2019)" / "extras"
    tv_ex.mkdir(parents=True)
    (tv_ex / "Show (2019)_t03.mkv").write_text("c")
    stray = root / "movies" / "Mamma Mia! (2008)" / "Mamma Mia! (2008)_t09.mkv"
    stray.write_text("root stray — left alone")

    renames = fix_staging_names(root)

    assert [(o.name, n.name) for o, n in renames] == [
        # 05.p2 taken -> rolls to 06.p2, the part suffix survives
        ("Mamma Mia! (2008)_t05.p2.mkv", "Mamma Mia! (2008) - Extra 06.p2.mkv"),
        ("Mamma Mia! (2008)_t21.mkv", "Mamma Mia! (2008) - Extra 21.mkv"),
        ("Show (2019)_t03.mkv", "Show (2019) - Extra 03.mkv"),
    ]
    assert (ex / "Mamma Mia! (2008) - Extra 21.mkv").read_text() == "a"
    assert (ex / "Mamma Mia! (2008) - Extra 05.p2.mkv").read_text() == "taken"
    assert stray.read_text() == "root stray — left alone"  # outside extras/
    assert fix_staging_names(root) == []  # idempotent


def test_run_validate_fix_applies_then_reports_clean(tmp_path, capsys, monkeypatch):
    """--fix applies before the walk: the renamed file vanishes from the
    findings and the JSON carries the fixed list."""
    monkeypatch.setattr("skive.validation.search_first", lambda kind, query: None)
    ex = tmp_path / "library" / "movies" / "Up (2009)" / "extras"
    ex.mkdir(parents=True)
    (ex / "Up (2009)_t01.mkv").write_text("x")

    run_validate(tmp_path, run=_fake_probe({"Up (2009) - Extra 01.mkv": 300}),
                 json_=True, fix=True)

    report = json.loads(capsys.readouterr().out)
    assert report["fixed"] == [
        {"from": "movies/Up (2009)/extras/Up (2009)_t01.mkv",
         "to": "movies/Up (2009)/extras/Up (2009) - Extra 01.mkv"}]
    assert report["summary"]["other"].get("staging name") is None
    assert (ex / "Up (2009) - Extra 01.mkv").exists()
