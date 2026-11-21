"""MusicBrainz DiscID lookup for the CD pre-rip resolve — stdlib only.

The resolve's track durations come from the MusicBrainz release the disc's
TOC is attached to: the DiscID (printed by cyanrip's TOC pre-pass, or
computed from its ``toc=`` URL parameter — first track, last track, lead-out
and per-track start offsets in 1/75 s sectors, SHA-1, base64 with libdiscid's
``+_/=`` → ``_.-`` alphabet) looked up over ws/2. Any failure resolves to
None — the caller shows the table with a blank audit column, never blocks.
"""

import base64
import hashlib
import json
import re
import urllib.request

_TOC_PARAM = re.compile(r"[?&]toc=(\d+(?:\+\d+)+)")
_ID_PARAM = re.compile(r"[?&]id=([^&\s]+)")
_BARE_DISCID = re.compile(r"^DiscID:\s*(\S+)$", re.MULTILINE)
_TRACKS_COUNT = re.compile(r"[?&]tracks=(\d+)|^Disc tracks:\s*(\d+)", re.MULTILINE)

# libdiscid's base64 alphabet: '+'→'_', '/'→'.', padding '='→'-'.
_DISCID_ALPHABET = str.maketrans({"+": "_", "/": ".", "=": "-"})


def toc_from_text(text: str) -> list[int] | None:
    """The disc TOC as sector values (first track, last track, lead-out,
    per-track start offsets) from a ``toc=`` URL parameter in cyanrip's
    output. None when none printed."""
    m = _TOC_PARAM.search(text or "")
    return [int(v) for v in m.group(1).split("+")] if m else None


def discid_from_toc(toc: list[int]) -> str:
    """The MusicBrainz DiscID (cyanrip's port of libdiscid's
    ``create_disc_id()``) from a full TOC: first track, last track, lead-out,
    per-track start offsets — uppercase hex, no separators, all 100 TOC slots
    hashed (entry 0 the lead-out, tracks 1..n, the rest zeroed). Offsets must
    already carry the +150 frame pregap, as cyanrip's ``toc=`` values do."""
    first, last = toc[0], toc[1]
    offsets = [0] * 100
    for position, offset in enumerate(toc[3:], start=1):
        offsets[position] = offset
    offsets[0] = toc[2]  # lead-out first, hashed as entry 0
    payload = f"{first:02X}{last:02X}" + "".join(f"{o:08X}" for o in offsets)
    digest = hashlib.sha1(payload.encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii").translate(_DISCID_ALPHABET)


def discid_from_text(text: str) -> str | None:
    """The DiscID from cyanrip's pre-pass output: an ``id=`` URL parameter
    when the pre-pass printed it, else the bare ``DiscID:`` line a found
    release prints, else computed from the ``toc=`` TOC."""
    id_match = _ID_PARAM.search(text or "")
    if id_match:
        return id_match.group(1)
    bare = _BARE_DISCID.search(text or "")
    if bare:
        return bare.group(1)
    toc = toc_from_text(text)
    if toc is not None and len(toc) >= 4:
        return discid_from_toc(toc)
    return None


def discid_info(discid: str, timeout: int = 10) -> dict | None:
    """The ws/2 discid lookup (with recordings, so release tracks carry
    durations) as parsed JSON. None on any failure — a failed resolve is
    the caller's blank audit column, never an error."""
    url = f"https://musicbrainz.org/ws/2/discid/{discid}?fmt=json&inc=recordings"
    request = urllib.request.Request(url, headers={"User-Agent": "skive"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except Exception:  # noqa: BLE001 — urllib/JSON/OS failure resolves to None, never fatal
        return None


def release_track_lengths(info: dict, track_count: int | None = None) -> dict[int, int] | None:
    """Per-track seconds from the lookup's first release (recording length
    preferred). On a multi-disc release the medium whose track count matches
    ``track_count`` (the pre-pass ``tracks=``) grades the table; without a
    match, the first medium. None when the release has no usable track
    durations — the stub shape."""
    releases = info.get("releases") if isinstance(info, dict) else None
    if not releases:
        return None
    mediums = releases[0].get("mediums") or []
    medium = mediums[0] if mediums else None
    if track_count is not None and len(mediums) > 1:
        for candidate in mediums:
            if len(candidate.get("tracks") or []) == track_count:
                medium = candidate
                break
    tracks = (medium.get("tracks") or []) if medium else []
    lengths: dict[int, int] = {}
    for position, track in enumerate(tracks, start=1):
        ms = track.get("length") or (track.get("recording") or {}).get("length")
        if ms:
            lengths[position] = int(ms) // 1000
    return lengths or None


def track_lengths(text: str) -> dict[int, int] | None:
    """The pre-rip resolve: MusicBrainz track durations (seconds, by track
    number) for the disc cyanrip's pre-pass describes. None when the text
    yields no DiscID or the lookup fails — the caller's blank audit."""
    discid = discid_from_text(text)
    if not discid:
        return None
    count = _TRACKS_COUNT.search(text or "")
    track_count = int(count.group(1) or count.group(2)) if count else None
    return release_track_lengths(discid_info(discid) or {}, track_count)
