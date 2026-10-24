"""Tests for the validation.json document: shapes, round-trips, no-file fallback."""

from skive.tv_match import RATIO_GREEN as TV_RATIO_GREEN
from skive.validation import (
    RATIO_GREEN, MAIN_FLOOR_S, VALIDATION_FILENAME,
    MainArbitration, arbitrate_main, biggest_title, grade_main,
    main_candidates,
    movie_document, tv_document, write_validation, load_validation,
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
