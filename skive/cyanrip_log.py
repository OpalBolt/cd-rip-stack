"""Tolerant parser for cyanrip's human-readable report."""

from __future__ import annotations

import re

_OFFSET = re.compile(r"^\s*Offset:\s*([+-]?\d+)\s+samples?\b", re.IGNORECASE | re.MULTILINE)
# Header lines sit at column 0; per-track metadata lines are indented, so
# anchoring these at the line start keeps a track's ``album:`` tag from matching.
_RELEASE_ID = re.compile(r"^Release ID:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_ALBUM = re.compile(r"^Album:\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)
_ALBUM_ARTIST = re.compile(r"^Album artist:\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)
_DISC = re.compile(r"^\s*Disc\s+number\s*:\s*(\d+)\b", re.IGNORECASE | re.MULTILINE)
_DISC_STEM = re.compile(r"\s+CD(\d+)$", re.IGNORECASE)
_TRACK = re.compile(r"^\s*Track\s+(\d+)\b", re.IGNORECASE)
_AR = re.compile(r"^\s*Accurip\s+(v1|v2|450):\s*(.*)$", re.IGNORECASE)
# ``Duration:`` carries an optional hour field (``hh:mm:ss.mmm`` or ``mm:ss.mmm``).
_DURATION = re.compile(r"^\s*Duration:\s*(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)\s*$",
                       re.IGNORECASE)
_CRC32 = re.compile(r"^\s*EAC CRC32:\s*([0-9A-F]{8})\s*$", re.IGNORECASE)
_DETAIL = re.compile(r"\((.*?)\)")
_CONFIDENCE = re.compile(r"confidence\s+(\d+)", re.IGNORECASE)


def disc_number_from_log(text: str = "", stem: str = "") -> str:
    """Return a cyanrip disc number, using the filename only as fallback.

    Current cyanrip reports the disc number in a ``Disc number:`` header.
    Older or altered reports may omit it, while cyanrip's multi-disc output
    names the corresponding log ``... CD<n>.log``.  The fallback is kept
    here, alongside report parsing, so callers do not need to duplicate the
    naming convention.
    """
    header = _DISC.search(text or "")
    if header:
        return header.group(1)
    filename = _DISC_STEM.search(stem or "")
    return filename.group(1) if filename else "1"


def album_header_field(line: str) -> tuple[str, str] | None:
    """Classify one cyanrip output line as the album header: ('album', value)
    or ('album_artist', value) when the line is one of the column-0 header
    lines (same patterns as parse_cyanrip_log), else None. Used by the rip's
    line stream, where the header prints mid-rip before any track result."""
    album_match = _ALBUM.match(line)
    if album_match:
        return "album", album_match.group(1)
    artist_match = _ALBUM_ARTIST.match(line)
    if artist_match:
        return "album_artist", artist_match.group(1)
    return None


def album_header_name(header: dict) -> str | None:
    """Run name from a streamed album header dict: 'Album artist - Album'
    when the pair is present, or the album alone without an artist line;
    None when no album header streamed."""
    album = header.get("album")
    artist = header.get("album_artist")
    if album and artist:
        return f"{artist} - {album}"
    return album or None


def parse_cyanrip_log(text: str) -> dict:
    """Parse header facts and per-track results from a cyanrip log.

    cyanrip's report is intended for people, not as a stable API.  This parser
    therefore ignores unfamiliar lines and returns an empty result for a
    missing or changed section rather than raising.  A track is considered
    accurate when either v1 or v2 says so, partial when the v1-450 result says
    it partially matches, and not_found when cyanrip explicitly reports that
    neither database checksum matched.
    """
    offset_match = _OFFSET.search(text or "")
    release_match = _RELEASE_ID.search(text or "")
    album_match = _ALBUM.search(text or "")
    artist_match = _ALBUM_ARTIST.search(text or "")
    result = {"offset": int(offset_match.group(1)) if offset_match else None,
              "release": release_match.group(1) if release_match else None,
              "album": album_match.group(1) if album_match else None,
              "album_artist": artist_match.group(1) if artist_match else None,
              "tracks": []}
    current: dict[int, dict] = {}
    parsed: set[int] = set()
    for line in (text or "").splitlines():
        track_match = _TRACK.match(line)
        if track_match:
            number = int(track_match.group(1))
            current.setdefault(number, {"track": number, "status": "unknown",
                                        "confidence": None, "crc32": None,
                                        "dur_s": None})
            continue
        duration_match = _DURATION.match(line)
        if duration_match and current:
            hours, minutes, seconds = duration_match.groups()
            current[next(reversed(current))]["dur_s"] = (
                int(hours or 0) * 3600 + int(minutes) * 60
                + int(seconds.split(".", 1)[0]))
            continue
        crc_match = _CRC32.match(line)
        if crc_match and current:
            current[next(reversed(current))]["crc32"] = crc_match.group(1)
            continue
        ar_match = _AR.match(line)
        if not ar_match or not current:
            continue
        kind, rest = ar_match.groups()
        number = next(reversed(current))
        entry = current[number]
        parsed.add(number)
        detail_match = _DETAIL.search(rest)
        detail = detail_match.group(1) if detail_match else rest
        confidence = _CONFIDENCE.search(detail)
        if confidence:
            value = int(confidence.group(1))
            previous = entry.get("confidence")
            entry["confidence"] = value if previous is None else max(previous, value)
        lower = detail.lower()
        if "accurately ripped" in lower and "partially" not in lower:
            entry["status"] = "accurate"
        elif "partially accurately ripped" in lower or "matches accurip db" in lower:
            entry["status"] = "partial"
        elif "not found" in lower:
            # Keep an accurate v1/v2 result if a later variant says so.
            if entry["status"] not in {"accurate", "partial"}:
                entry["status"] = "not_found"
    result["tracks"] = [entry for number, entry in current.items()
                         if number in parsed]
    return result
