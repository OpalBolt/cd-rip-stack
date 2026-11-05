#!/usr/bin/env python3
"""Black-bar crop detector trial (grill session, pre-spec — report-only).

Runs the agreed detector against movie files and prints the gated verdict
per file. Writes nothing, encodes nothing — this exists to prove the gate
on real library titles before the spec and tickets.

The gate (settled in .scratch/transcode-overlap-crop/map.md):
  ~6 probes spread over 10%-90% of the file, cropdetect on a short window
  each; per-side bar = median across probes; at most one probe per side
  may dissent beyond +/-4 px of that median (a lone dissent is a credits
  or dark-scene outlier); sides under 16 px crop as 0 (alignment noise);
  left+right and top+bottom each under 30% of the axis. More dissent or
  over-cap = no crop, reason printed.

Usage:
  mise exec -- python3 scripts/crop_detect_test.py 'Movie (Year).mkv' [more ...]
  python3 scripts/crop_detect_test.py --self-check   # gate logic only, no ffmpeg

Needs ffmpeg + ffprobe on PATH (rip box: prefix with `mise exec --`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys

CROP_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")

MIN_PX = 16        # one DVD macroblock; a bar under this is alignment noise
MAX_AXIS_PCT = 30  # per-axis crop total above this smells like detection error
TOLERANCE = 4      # a dissenting probe sits further than this from the median
MIN_READINGS = 3   # fewer good probes than this = detection failed
MAX_DISSENT = 1    # lone dissent per side absorbed (credits / dark scene)


def probe_stream(path: str) -> tuple[int, int, float, str]:
    """(width, height, duration_s, field_order) of the first video stream."""
    doc = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,field_order:format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True).stdout)
    st = doc["streams"][0]
    return (int(st["width"]), int(st["height"]),
            float(doc["format"]["duration"]), st.get("field_order") or "?")


def probe_bars(path: str, at_s: float, frames: int,
               w: int, h: int) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) bar widths at one timestamp; None = no read."""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats",
         "-ss", f"{at_s:.1f}", "-i", path, "-an", "-sn",
         "-frames:v", str(frames),
         "-vf", "cropdetect=limit=24:round=2", "-f", "null", "-"],
        capture_output=True, text=True)
    hits = CROP_RE.findall(p.stderr)
    if not hits:
        return None
    cw, ch, x, y = map(int, hits[-1])  # the last frame is the settled read
    return x, y, w - x - cw, h - y - ch


def gate(readings: list[tuple[int, int, int, int]], w: int, h: int):
    """Apply the agreed gate. Returns (crop(l,t,r,b) or None, reason, detail)."""
    med = [statistics.median(r[i] for r in readings) for i in range(4)]
    detail = "median l/t/r/b = " + "/".join(str(int(m)) for m in med)
    for name, i in zip(("left", "top", "right", "bottom"), range(4)):
        n_off = sum(1 for r in readings if abs(r[i] - med[i]) > TOLERANCE)
        if n_off > MAX_DISSENT:
            return None, (f"unstable {name} ({n_off} of {len(readings)} "
                          "probes dissent)"), detail
    crop = [m if m >= MIN_PX else 0 for m in med]
    if not any(crop):
        return None, f"no bar >= {MIN_PX} px", detail
    for axis, (ia, ib), size in (("width", (0, 2), w), ("height", (1, 3), h)):
        total = crop[ia] + crop[ib]
        if total > size * MAX_AXIS_PCT / 100:
            return None, (f"{axis} crop {int(total)} px = "
                          f"{100 * total / size:.0f}% (cap {MAX_AXIS_PCT}%)"), detail
    crop = [int(c // 2 * 2) for c in crop]  # even, like cropdetect's round=2
    return tuple(crop), None, detail  # type: ignore[return-value]


def _clean_path(raw: str) -> str:
    # Forgive pasted shell quoting: one matched quote layer, then
    # backslash escapes. shortcut: trial-only leniency, skive stays strict
    p = raw
    if len(p) > 1 and p[0] == p[-1] and p[0] in "'\"":
        p = p[1:-1]
    if not os.path.exists(p) and "\\" in p:
        p = re.sub(r"\\(.)", r"\1", p)
    return p


def trial(path: str, probes: int, frames: int) -> int:
    path = _clean_path(path)
    if not os.path.isfile(path):
        print(f"{path}\n  not found -- check the path (quote it, no backslashes)")
        return 1
    try:
        w, h, dur, field = probe_stream(path)
    except (subprocess.CalledProcessError, KeyError, IndexError, ValueError) as e:
        print(f"{path}\n  ffprobe failed: {e}")
        return 1
    stamps = [dur * (0.1 + 0.8 * i / (probes - 1)) for i in range(probes)]
    readings = [r for r in (probe_bars(path, t, frames, w, h) for t in stamps)
                if r is not None]
    print(f"{path}  --  {w}x{h} {field} {dur:.0f}s  "
          f"probes ok={len(readings)}/{probes}")
    if len(readings) < MIN_READINGS:
        print(f"  VERDICT: no crop -- detection failed "
              f"({probes - len(readings)} probe(s) gave no reading)")
        return 1
    print("  bars l/t/r/b per probe: " +
          "  ".join("/".join(str(v) for v in r) for r in readings))
    crop, reason, detail = gate(readings, w, h)
    print(f"  {detail}")
    if crop is None:
        print(f"  VERDICT: no crop -- {reason}")
        if field not in ("progressive", "?"):
            print("  note: interlaced source; the encode deinterlaces first, "
                  "a raw-frame detector can misread it")
        return 0
    nw, nh = w - crop[0] - crop[2], h - crop[1] - crop[3]
    dropped = 100 * (1 - nw * nh / (w * h))
    print(f"  VERDICT: crop l/t/r/b = {'/'.join(map(str, crop))} "
          f"-> encode {nw}x{nh} ({dropped:.1f}% of pixels dropped)")
    return 0


def self_check() -> int:
    cases = [  # (readings, width, height, expected crop or None)
        ([(0, 0, 0, 0)] * 6, 1024, 576, None),              # clean frame
        ([(0, 70, 0, 70)] * 6, 1024, 576, (0, 70, 0, 70)),  # plain letterbox
        ([(8, 0, 8, 0)] * 6, 1024, 576, None),              # under 16 px = noise
        ([(0, 200, 0, 200)] * 6, 1024, 576, None),          # 69% of height = error
        ([(70, 0, 70, 0), (70, 0, 70, 0), (0, 0, 0, 0),
          (70, 0, 70, 0), (70, 0, 70, 0), (70, 0, 70, 0)],
         1024, 576, (70, 0, 70, 0)),                      # lone dissent absorbed
        ([(70, 0, 70, 0)] * 3 + [(0, 0, 0, 0)] * 3,
         1024, 576, None),                                # half dissent = mixed AR
        ([(20, 0, 40, 0)] * 6, 1024, 576, (20, 0, 40, 0)),  # asymmetric pillarbox
    ]
    for readings, w, h, want in cases:
        crop, reason, _ = gate(readings, w, h)
        assert crop == want, (f"{readings[0]} x{len(readings)} on {w}x{h} -> "
                              f"{crop} ({reason}), wanted {want}")
        print(f"  ok: {want}  <-  {readings[0]} x{len(readings)} on {w}x{h}")
    print("self-check passed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", help="movie files to trial the gate on")
    ap.add_argument("--probes", type=int, default=6, help="sample points (default 6)")
    ap.add_argument("--frames", type=int, default=30,
                    help="frames decoded per probe (default 30)")
    ap.add_argument("--self-check", action="store_true",
                    help="run gate-logic asserts, no ffmpeg needed")
    args = ap.parse_args()
    if args.self_check:
        return self_check()
    if not args.files:
        ap.error("give file(s), or --self-check")
    return max(trial(f, args.probes, args.frames) for f in args.files)


if __name__ == "__main__":
    sys.exit(main())
