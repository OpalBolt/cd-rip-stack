"""Pure cyanrip report parsing tests."""

from skive.cyanrip_log import parse_cyanrip_log


def test_parse_upstream_accuraterip_report():
    report = """cyanrip 0.10
Offset:         + samples

  Track 1
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
        "tracks": [
            {"track": 1, "status": "accurate", "confidence": 9},
            {"track": 2, "status": "partial", "confidence": 10},
        ],
    }


def test_parse_missing_sections_is_empty_and_never_raises():
    assert parse_cyanrip_log("cyanrip changed its output\n") == {
        "offset": None, "tracks": []}
