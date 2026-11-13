"""Pure cyanrip report parsing tests."""

from pathlib import Path

from skive.cyanrip_log import (disc_number_from_log, parse_cue_starts,
                               parse_cyanrip_log, parse_musicbrainz_miss,
                               total_time_s)


def test_disc_number_uses_header_then_cd_stem_then_one():
    assert disc_number_from_log("Disc number:    3\n", "Album CD1") == "3"
    assert disc_number_from_log("cyanrip report\n", "Album CD2") == "2"
    assert disc_number_from_log("cyanrip report\n", "Album") == "1"


def test_parse_upstream_accuraterip_report():
    report = """cyanrip 0.10
Offset:         + samples

  Track 1
  EAC CRC32:     ABCDEF01
  Duration:      04:29.240
  Accurip:       disc found in database (max confidence: 12)
    Accurip v1:  12345678 (accurately ripped, confidence 9)
    Accurip v2:  12345678 (accurately ripped, confidence 8)

  Track 2
  Accurip:       disc found in database (max confidence: 12)
    Accurip v1:  12345678 (not found, either a new pressing, or bad rip)
    Accurip v2:  12345678 (matches Accurip DB, confidence 10, track is partially accurately ripped)
"""
    parsed = parse_cyanrip_log(report.replace("+ samples", "+123 samples"))
    assert parsed == {
        "offset": 123,
        "release": None,
        "album": None,
        "album_artist": None,
        "tracks": [
            {"track": 1, "status": "accurate", "confidence": 9,
             "crc32": "ABCDEF01", "dur_s": 269},
            {"track": 2, "status": "partial", "confidence": 10,
             "crc32": None, "dur_s": None},
        ],
    }


def test_parse_real_report_fixture():
    text = (Path(__file__).parent / "fixtures" / "cyanrip"
            / "summertime-blues.log").read_text(encoding="utf-8")
    parsed = parse_cyanrip_log(text)
    assert parsed["release"] == "0ba5b04b-db78-46cd-95e9-9fcf1945fa23"
    assert parsed["album"] == "Summertime Blues"
    assert parsed["album_artist"] == "Various Artists"
    tracks = parsed["tracks"]
    assert len(tracks) == 19
    assert all(track["track"] == number
               for number, track in enumerate(tracks, 1))
    assert all(track["status"] == "accurate" and track["confidence"] == 3
               for track in tracks)
    assert all(track["crc32"] is not None and track["dur_s"] is not None
               for track in tracks)
    assert tracks[0]["crc32"] == "6C421A41"
    assert tracks[0]["dur_s"] == 269


def test_parse_missing_sections_is_empty_and_never_raises():
    assert parse_cyanrip_log("cyanrip changed its output\n") == {
        "offset": None, "release": None, "album": None,
        "album_artist": None, "tracks": []}
    track_only = parse_cyanrip_log(
        "Track 1 ripped and encoded successfully!\n"
        "  Accurip v1:  12345678 (accurately ripped, confidence 2)\n")
    assert track_only["tracks"] == [
        {"track": 1, "status": "accurate", "confidence": 2,
         "crc32": None, "dur_s": None}]


# Real transcript: run 83a8c814's raw log (/mnt/music/logs/raw/83a8c814.jsonl).
NOT_FOUND_83A8C814 = """Checking /dev/cdrom for cdrom...
\t\tCDROM sensed: hp       CDDVDW TS-L633R  0200 SCSI CD-ROM


Opening drive...
Unable to find release info for this CD, and metadata hasn't been manually added!
Please help improve the MusicBrainz DB by submitting the disc info via the following URL:
https://musicbrainz.org/cdtoc/attach?toc=1+25+328608+183+18725+31995+47463+60950+72288+84813+97270+112505+125005+138750+152115+164495+179008+192575+204948+217745+228955+240323+253030+265355+278188+291735+303715+316100&tracks=25&id=PUAwXKQBprUvd7PoJGuIZeTGyqs-
To continue add metadata via -a or -t, or ignore via -N!
"""


def test_musicbrainz_miss_not_found_from_real_transcript():
    miss = parse_musicbrainz_miss(NOT_FOUND_83A8C814)
    assert miss["case"] == "not_found"
    assert miss["discid"] == "PUAwXKQBprUvd7PoJGuIZeTGyqs-"
    assert miss["url"].startswith("https://musicbrainz.org/cdtoc/attach?toc=1+25+328608+")
    assert miss["url"].endswith("&tracks=25&id=PUAwXKQBprUvd7PoJGuIZeTGyqs-")
    assert miss["tracks"] == 25
    assert miss["length"] == "72:59"  # (328608 - 183) sectors / 75
    assert miss["candidates"] == []


def test_musicbrainz_miss_multiple_releases_synthetic():
    # cyanrip 0.9.3's format (from source; no real transcript yet)
    text = """Disc tracks: 12
Multiple releases found in database for DiscID aBcD-:
    1 (ID: 11111111-aaaa-bbbb-cccc-000000000001): Blue (GB) (1999-03-01)
    2 (ID: 11111111-aaaa-bbbb-cccc-000000000002): Blue (remaster) (US) (2 CDs) (2004)
    3 (ID: 11111111-aaaa-bbbb-cccc-000000000003): Blue
Please specify which release to use by adding the -R argument with an index or ID.
"""
    miss = parse_musicbrainz_miss(text)
    assert (miss["case"], miss["discid"], miss["tracks"]) == ("multiple", "aBcD-", 12)
    assert miss["url"] == "https://musicbrainz.org/cdtoc/aBcD-"
    assert miss["candidates"] == [
        {"index": 1, "mbid": "11111111-aaaa-bbbb-cccc-000000000001", "album": "Blue",
         "disambiguation": None, "country": "GB", "cds": None, "date": "1999-03-01"},
        {"index": 2, "mbid": "11111111-aaaa-bbbb-cccc-000000000002", "album": "Blue",
         "disambiguation": "remaster", "country": "US", "cds": 2, "date": "2004"},
        {"index": 3, "mbid": "11111111-aaaa-bbbb-cccc-000000000003", "album": "Blue",
         "disambiguation": None, "country": None, "cds": None, "date": None},
    ]


def test_musicbrainz_miss_stub_and_no_miss():
    stub = parse_musicbrainz_miss(
        "Found a CD stub for this disc!\n"
        "https://musicbrainz.org/cdtoc/attach?toc=1+2+9150+150+4650&tracks=2&id=XyZ-\n")
    assert (stub["case"], stub["discid"], stub["tracks"], stub["length"]) == (
        "stub", "XyZ-", 2, "2:00")
    assert parse_musicbrainz_miss("Read error\n") is None


# -- Stall guard: CUE / Total time parsing (ADR 0018) --------------------------

def test_parse_cue_starts_multi_track_fixture():
    """Track starts from a real cyanrip CUE shape: INDEX 01 at 75 fps,
    INDEX 00 pregap lines ignored; the last track has no end in the CUE."""
    text = (Path(__file__).parent / "fixtures" / "cyanrip" / "toc.cue").read_text(
        encoding="utf-8")
    starts = parse_cue_starts(text)
    assert starts == {1: 0.0, 2: 221 + 12 / 75, 3: 442 + 36 / 75}


def test_parse_cue_starts_empty_without_audio_tracks():
    assert parse_cue_starts("") == {}
    assert parse_cue_starts("TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n") == {}


def test_total_time_header_line():
    assert total_time_s("Total time:     01:18:47.468") == 4727.468
    assert total_time_s("Total time:     04:48.000") == 288.0
    assert total_time_s("Disc tracks:    19") is None
