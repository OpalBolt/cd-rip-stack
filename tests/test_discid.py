"""Tests for skive.discid — the pre-rip resolve's MusicBrainz lookup
(fixture JSON, urllib monkeypatched; no network)."""

import json
import re
import urllib.error
from pathlib import Path

from skive.discid import (
    discid_from_text,
    discid_from_toc,
    release_track_lengths,
    toc_from_text,
    track_lengths,
)

# A cyanrip pre-pass text: the attach URL carries the DiscID and the TOC
# (first track, last track, lead-out, per-track start offsets — sectors).
INFO_TEXT = (
    "Disc tracks: 2\n"
    "https://musicbrainz.org/cdtoc/attach?id=AbCdEf123456"
    "&tracks=2&toc=1+2+180000+150+90000\n"
)

FIXTURE_INFO = {
    "releases": [
        {
            "title": "Test Album",
            "mediums": [
                {
                    "tracks": [
                        {"recording": {"length": 200123}},
                        {"length": 300000},
                    ],
                }
            ],
        }
    ],
}


def test_toc_from_text_parses_the_url_parameter():
    assert toc_from_text(INFO_TEXT) == [1, 2, 180000, 150, 90000]
    assert toc_from_text("no url here") is None


def test_discid_from_toc_shape():
    """The computed DiscID is 28 characters over libdiscid's alphabet."""
    discid = discid_from_toc([1, 2, 180000, 150, 90000])
    assert len(discid) == 28
    assert re.fullmatch(r"[A-Za-z0-9_.\-]{28}", discid)


def test_discid_from_text_prefers_the_url_id():
    assert discid_from_text(INFO_TEXT) == "AbCdEf123456"
    assert discid_from_text("?toc=1+2+180000+150+90000") == discid_from_toc(
        [1, 2, 180000, 150, 90000]
    )
    assert discid_from_text("nothing useful") is None


def test_release_track_lengths_reads_the_first_release():
    assert release_track_lengths(FIXTURE_INFO) == {1: 200, 2: 300}
    assert release_track_lengths({"releases": []}) is None
    assert release_track_lengths({"releases": [{"mediums": [{"tracks": []}]}]}) is None


def test_track_lengths_uses_the_url_discid(monkeypatch):
    monkeypatch.setattr("skive.discid.discid_info", lambda d: dict(FIXTURE_INFO))
    assert track_lengths(INFO_TEXT) == {1: 200, 2: 300}


def test_discid_info_hits_the_ws2_endpoint(monkeypatch):
    """The lookup GETs ws/2/discid/<id>?fmt=json&inc=recordings and parses
    the JSON body; any failure resolves to None."""
    seen = {}

    class FakeResponse:
        def read(self):
            seen["body"] = True
            return json.dumps(FIXTURE_INFO).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        return FakeResponse()

    monkeypatch.setattr("skive.discid.urllib.request.urlopen", fake_urlopen)
    info = None
    from skive import discid as discid_module

    info = discid_module.discid_info("AbCdEf123456")
    assert info["releases"][0]["title"] == "Test Album"
    assert seen["url"] == (
        "https://musicbrainz.org/ws/2/discid/AbCdEf123456?fmt=json&inc=recordings"
    )

    def boom(request, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("skive.discid.urllib.request.urlopen", boom)
    assert discid_module.discid_info("AbCdEf123456") is None


_SUMMERTIME_FIXTURE = Path(__file__).parent / "fixtures" / "cyanrip" / "summertime-blues.log"

_SUMMERTIME_TOC = [
    1,
    19,
    354705,
    150,
    20335,
    45765,
    70122,
    85770,
    106592,
    125922,
    151877,
    168452,
    187122,
    209860,
    232147,
    250112,
    265927,
    284967,
    299585,
    312580,
    325957,
    339192,
]


def test_discid_from_toc_matches_the_fixture_disc():
    """The golden pair from the repo's own cyanrip fixture disc: the TOC in
    the miss URL must compute exactly the DiscID cyanrip printed for it."""
    assert discid_from_toc(_SUMMERTIME_TOC) == "bOpGMzWaoGKqV7OC6onCHgXytfE-"


def test_discid_from_text_reads_the_bare_discid_line():
    """A found release prints a bare ``DiscID:`` line and no attach URL —
    the real summertime-blues header text must still yield the DiscID."""
    assert discid_from_text(_SUMMERTIME_FIXTURE.read_text()) == "bOpGMzWaoGKqV7OC6onCHgXytfE-"


_MULTI_MEDIUM_INFO = {
    "releases": [
        {
            "title": "Two-fer",
            "mediums": [
                {
                    "position": 1,
                    "tracks": [
                        {"recording": {"length": 100000}},
                        {"recording": {"length": 110000}},
                    ],
                },
                {
                    "position": 2,
                    "tracks": [
                        {"recording": {"length": 200123}},
                        {"length": 300000},
                        {"recording": {"length": 400000}},
                    ],
                },
            ],
        }
    ],
}


def test_release_track_lengths_grades_the_matching_medium():
    """On a multi-disc release, the medium whose track count matches the
    pre-pass count grades the table — CD2 is not graded against CD1."""
    assert release_track_lengths(_MULTI_MEDIUM_INFO, 3) == {1: 200, 2: 300, 3: 400}
    assert release_track_lengths(_MULTI_MEDIUM_INFO, 2) == {1: 100, 2: 110}
    # no matching medium: falls back to the first one
    assert release_track_lengths(_MULTI_MEDIUM_INFO, 9) == {1: 100, 2: 110}
    assert release_track_lengths(_MULTI_MEDIUM_INFO) == {1: 100, 2: 110}


def test_track_lengths_passes_the_pre_pass_track_count(monkeypatch):
    monkeypatch.setattr("skive.discid.discid_info", lambda d: dict(_MULTI_MEDIUM_INFO))
    assert track_lengths(INFO_TEXT) == {1: 100, 2: 110}
    three = (
        "Disc tracks: 3\n"
        "https://musicbrainz.org/cdtoc/attach?id=AbCdEf123456"
        "&tracks=3&toc=1+3+180000+150+90000+120000\n"
    )
    assert track_lengths(three) == {1: 200, 2: 300, 3: 400}
