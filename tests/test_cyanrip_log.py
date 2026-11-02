"""Pure cyanrip report parsing tests."""

from pathlib import Path

from skive.cyanrip_log import disc_number_from_log, parse_cyanrip_log


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
