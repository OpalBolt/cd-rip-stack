"""Tests for the validation.json document: shapes, round-trips, no-file fallback."""

from skive.tv_match import RATIO_GREEN as TV_RATIO_GREEN
from skive.validation import (
    RATIO_GREEN, MAIN_FLOOR_S, VALIDATION_FILENAME,
    MainArbitration, arbitrate_main, biggest_title, grade_main,
    main_candidates, merge_movie_document, movie_row, resolve_main,
    movie_document, tv_document, write_validation, load_validation,
    merge_tv_document, tv_row,
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
    """The TV document keeps the shared identity block plus the episode map."""
    doc = tv_document(
        tmdb_id="456", title="Show (2019)", season=2,
        volume_label="SHOW_S2", run="a1b2c3d4",
        episodes=[{"ep": 5, "file": "Show S02E05.mkv", "t": 12, "dur_s": 2611,
                   "runtime_s": 2580, "ratio": 0.99, "band": "green"}],
    )
    assert list(doc) == ["tmdb", "volume_label", "run", "episodes", "encodes"]
    assert doc["tmdb"] == {"id": "456", "type": "tv",
                           "title": "Show (2019)", "season": 2}
    assert doc["episodes"] == [{"ep": 5, "file": "Show S02E05.mkv", "t": 12,
                                "dur_s": 2611, "runtime_s": 2580,
                                "ratio": 0.99, "band": "green"}]
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
    """The full TV document — episode map with per-episode runtimes —
    survives write -> load unchanged."""
    doc = tv_document(
        tmdb_id="456", title="Show (2019)", season=2,
        volume_label="SHOW_S2", run="a1b2c3d4",
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
    ep already recorded is replaced by this run's row, not duplicated; run:
    latest wins; encodes start empty."""
    old = tv_document("456", "Show (2019)", 2, "OLD", "oldrun",
                      [{"ep": 5, "file": "Show (2019)_S02E05.mkv", "t": 12,
                        "dur_s": 2611, "runtime_s": 2580, "ratio": 1.01,
                        "band": "green"}])
    new = tv_document("456", "Show (2019)", 2, "NEW", "newrun",
                      [{"ep": 5, "file": "Show (2019)_S02E05.mkv", "t": None,
                        "dur_s": 2600, "runtime_s": 2580, "ratio": 1.01,
                        "band": "green"},
                       {"ep": 6, "file": "Show (2019)_S02E06.mkv", "t": 13,
                        "dur_s": 2602, "runtime_s": 2580, "ratio": 1.01,
                        "band": "green"}])

    merged = merge_tv_document(old, new)
    assert [r["ep"] for r in merged["episodes"]] == [5, 6]
    assert merged["episodes"][0]["dur_s"] == 2600  # this run's row replaced
    assert merged["run"] == "newrun"                # latest wins
    assert merged["volume_label"] == "NEW"
    assert merged["encodes"] == []


def test_merge_tv_document_tolerates_rowless_old():
    """An old document without a readable episodes list merges as empty."""
    new = tv_document("456", "Show (2019)", 2, "NEW", "newrun", [])
    merged = merge_tv_document({"tmdb": {"id": "456"}}, new)
    assert merged["episodes"] == []
