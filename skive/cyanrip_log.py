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


_MB_URL = re.compile(r"https://musicbrainz\.org/\S+")
_URL_DISCID = re.compile(r"[?&]id=([^&\s]+)")
_URL_TRACKS = re.compile(r"[?&]tracks=(\d+)")
_URL_TOC = re.compile(r"[?&]toc=(\d+)\+(\d+)\+(\d+)\+(\d+)")
_DISC_TRACKS = re.compile(r"^Disc tracks:\s*(\d+)", re.MULTILINE)
_MULTIPLE = re.compile(r"^Multiple releases found in database for DiscID (\S+?):?\s*$",
                       re.MULTILINE)
# cyanrip 0.9.3: "    <n> (ID: <mbid>): <album> (<disambiguation>) (<country>) (<n> CDs) (<date>)",
# each parenthesised field optional.
# shortcut: an album title ending in "(…)" reads as disambiguation; upgrade if a real transcript shows it.
_CANDIDATE = re.compile(r"^\s+(\d+) \(ID: ([^)\s]+)\): (.*?)((?: \([^()]*\))*)\s*$")
_FIELD = re.compile(r"\(([^()]*)\)")


def parse_musicbrainz_miss(text: str) -> dict | None:
    """The MusicBrainz miss (CONTEXT.md) in cyanrip's output, or None.

    Returns {case, discid, url, tracks, length, candidates}: case is
    not_found, stub or multiple; url is the attach (or stub) URL cyanrip
    printed, or the disc's cdtoc page for multiple; tracks and length
    (m:ss) come from a ``Disc tracks:`` line or the attach URL's TOC, None
    when neither printed; candidates (multiple only) are dicts of index,
    mbid, album, disambiguation, country, cds, date.
    """
    text = text or ""
    multiple = _MULTIPLE.search(text)
    if multiple:
        case = "multiple"
    elif "Unable to find release info" in text:
        case = "not_found"
    # shortcut: stub wording unverified (no real transcript); upgrade on the first real one.
    elif re.search(r"\bstub\b", text, re.IGNORECASE) and _MB_URL.search(text):
        case = "stub"
    else:
        return None
    url_match = _MB_URL.search(text)
    url = url_match.group(0) if url_match else None
    discid_match = _URL_DISCID.search(url or "")
    discid = multiple.group(1) if multiple else (
        discid_match.group(1) if discid_match else None)
    if multiple:
        url = f"https://musicbrainz.org/cdtoc/{discid}"
    tracks_match = _DISC_TRACKS.search(text) or _URL_TRACKS.search(url or "")
    toc = _URL_TOC.search(url or "")
    length = None
    if toc:  # toc=first+last+leadout+offset1…, in 1/75 s sectors
        secs = (int(toc.group(3)) - int(toc.group(4))) // 75
        length = f"{secs // 60}:{secs % 60:02d}"
    candidates = []
    if multiple:
        for line in text[multiple.end():].splitlines():
            candidate = _CANDIDATE.match(line)
            if not candidate:
                continue
            entry = {"index": int(candidate.group(1)), "mbid": candidate.group(2),
                     "album": candidate.group(3), "disambiguation": None,
                     "country": None, "cds": None, "date": None}
            for field in _FIELD.findall(candidate.group(4)):
                if re.fullmatch(r"\d+ CDs?", field):
                    entry["cds"] = int(field.split()[0])
                elif re.fullmatch(r"\d{4}(?:-\d{2}){0,2}", field):
                    entry["date"] = field
                elif re.fullmatch(r"[A-Z]{2}", field):
                    entry["country"] = field
                elif entry["disambiguation"] is None:
                    entry["disambiguation"] = field
            candidates.append(entry)
    return {"case": case, "discid": discid, "url": url,
            "tracks": int(tracks_match.group(1)) if tracks_match else None,
            "length": length, "candidates": candidates}


def parse_track_durations(text: str) -> dict[int, float]:
    """Per-track audio seconds from ``cyanrip -I`` output (the stall-guard
    pre-pass): each ``Track n info:`` block carries a ``Duration:`` line,
    from the TOC. Data tracks have none and are absent; empty when nothing
    parses."""
    durations: dict[int, float] = {}
    number = None
    for line in (text or "").splitlines():
        track_match = _TRACK.match(line)
        if track_match:
            number = int(track_match.group(1))
            continue
        duration_match = _DURATION.match(line)
        if duration_match and number is not None:
            hours, minutes, seconds = duration_match.groups()
            durations[number] = int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds)
            number = None
    return durations


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
