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

# libdiscid's base64 alphabet: '+'→'_', '/'→'.', padding '='→'-'.
_DISCID_ALPHABET = str.maketrans({"+": "_", "/": ".", "=": "-"})


def toc_from_text(text: str) -> list[int] | None:
    """The disc TOC as sector values (first track, last track, lead-out,
    per-track start offsets) from a ``toc=`` URL parameter in cyanrip's
    output. None when none printed."""
    m = _TOC_PARAM.search(text or "")
    return [int(v) for v in m.group(1).split("+")] if m else None


def discid_from_toc(toc: list[int]) -> str:
    """The MusicBrainz DiscID (libdiscid algorithm) from a full TOC: first
    track, last track, then every start offset with the lead-out last."""
    first, last, offsets = toc[0], toc[1], toc[2:]
    payload = f"{first:02d} {last:02d} "
    for offset in offsets:
        payload += f"{offset + 150:08X} "
    digest = hashlib.sha1(payload.encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii").translate(_DISCID_ALPHABET)


def discid_from_text(text: str) -> str | None:
    """The DiscID from cyanrip's pre-pass output: an ``id=`` URL parameter
    when the pre-pass printed it, else computed from the ``toc=`` TOC."""
    id_match = _ID_PARAM.search(text or "")
    if id_match:
        return id_match.group(1)
    toc = toc_from_text(text)
    if toc is not None and len(toc) >= 4:
        return discid_from_toc(toc)
    return None


def discid_info(discid: str, timeout: int = 10) -> dict | None:
    """The ws/2 discid lookup (with recordings, so release tracks carry
    durations) as parsed JSON. None on any failure — a failed resolve is
    the caller's blank audit column, never an error."""
    url = (f"https://musicbrainz.org/ws/2/discid/{discid}"
           "?fmt=json&inc=recordings")
    request = urllib.request.Request(url, headers={"User-Agent": "skive"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except Exception:  # urllib.error, JSON, OSError: resolve failed, not fatal
        return None


def release_track_lengths(info: dict) -> dict[int, int] | None:
    """Per-track seconds from the lookup's first release (its first medium's
    tracklist, recording length preferred). None when the release has no
    usable track durations — the stub shape."""
    releases = info.get("releases") if isinstance(info, dict) else None
    if not releases:
        return None
    mediums = releases[0].get("mediums") or []
    tracks = (mediums[0].get("tracks") or []) if mediums else []
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
    return release_track_lengths(discid_info(discid) or {})
