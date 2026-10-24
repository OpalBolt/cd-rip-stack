"""Tests for the validation.json document: shapes, round-trips, no-file fallback."""

from skive.tv_match import RATIO_GREEN as TV_RATIO_GREEN
from skive.validation import (
    RATIO_GREEN, VALIDATION_FILENAME,
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
