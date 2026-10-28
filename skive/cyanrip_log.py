"""Tolerant parser for cyanrip's human-readable report."""

from __future__ import annotations

import re

_OFFSET = re.compile(r"^\s*Offset:\s*([+-]?\d+)\s+samples?\b", re.IGNORECASE | re.MULTILINE)
_TRACK = re.compile(r"^\s*Track\s+(\d+)\b", re.IGNORECASE)
_AR = re.compile(r"^\s*Accurip\s+(v1|v2|450):\s*(.*)$", re.IGNORECASE)
_DETAIL = re.compile(r"\((.*?)\)")
_CONFIDENCE = re.compile(r"confidence\s+(\d+)", re.IGNORECASE)


def parse_cyanrip_log(text: str) -> dict:
    """Parse offset and per-track AccurateRip results from a cyanrip log.

    cyanrip's report is intended for people, not as a stable API.  This parser
    therefore ignores unfamiliar lines and returns an empty result for a
    missing or changed section rather than raising.  A track is considered
    accurate when either v1 or v2 says so, partial when the v1-450 result says
    it partially matches, and not_found when cyanrip explicitly reports that
    neither database checksum matched.
    """
    offset_match = _OFFSET.search(text or "")
    result = {"offset": int(offset_match.group(1)) if offset_match else None,
              "tracks": []}
    current: dict[int, dict] = {}
    parsed: set[int] = set()
    for line in (text or "").splitlines():
        track_match = _TRACK.match(line)
        if track_match:
            number = int(track_match.group(1))
            current.setdefault(number, {"track": number, "status": "unknown",
                                        "confidence": None})
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
