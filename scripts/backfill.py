#!/usr/bin/env python3
"""One-off backfill: convert the existing library in place on Linux.

Runs the shared subtitle pass (skive/subs.py) per encode, ships the SRT
sidecars beside the video, and strips the embedded bitmap tracks only after
the sidecars verify (ADR 0006). Idempotent and resumable from library state
alone -- re-runs skip finished files and skip OCR when the expected sidecars
already exist but the strip never happened. Extras trees are never touched
(ADR 0003). A failed file keeps its tracks and lands on the end-of-run retry
report (backfill-report.txt in the library root).

Usage:  python scripts/backfill.py [LIBRARY]
        (default /mnt/videos/library, or $VIDEOS/library when VIDEOS is set)
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from skive import subs


def _rmf(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def probe_tools() -> dict[str, str]:
    """shutil.which for the four tools; tesseract --list-langs must include
    eng and dan. Prints exactly what to install and returns {} when anything
    is missing -- callers must exit before touching the library."""
    paths: dict[str, str] = {}
    missing: list[str] = []
    for tool in ("seconv", "tesseract", "mkvmerge", "ffprobe"):
        found = shutil.which(tool)
        if found:
            paths[tool] = found
        else:
            missing.append(f"  {tool}: not on PATH")
    if "tesseract" in paths:
        r = subprocess.run([paths["tesseract"], "--list-langs"],
                           capture_output=True, text=True)
        langs = set(r.stdout.split())
        lack = [lang for lang in ("eng", "dan") if lang not in langs]
        if r.returncode != 0:
            missing.append("  tesseract --list-langs failed")
        for lang in lack:
            missing.append(f"  tesseract --list-langs missing {lang} (tesseract-data-{lang})")
    if missing:
        print('missing tools -- see README ("Non-Python prerequisites"):',
              file=sys.stderr)
        print("\n".join(missing), file=sys.stderr)
        return {}
    return paths


def walk(root: str):
    """All *.mkv under root, extras trees pruned (ADR 0003: untouched,
    un-OCR'd, un-stripped) and our own strip temps ignored, sorted for a
    stable run order."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d.lower() != "extras")
        for name in sorted(filenames):
            if name.lower().endswith(".mkv") and not name.endswith(".tmp.mkv"):
                yield os.path.join(dirpath, name)


def _nonempty(path: str) -> bool:
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def _sidecars_beside(video_dir: str, stem: str) -> bool:
    """Any <stem>.*.srt beside the video. A stripped file has no subtitle
    streams left to recompute its expected set from, so this marks it done."""
    return any(f.startswith(stem + ".") and f.endswith(".srt")
               for f in os.listdir(video_dir))


def _type_counts(path: str, ffprobe_exe: str) -> dict[str, int] | None:
    """Stream count per codec_type, or None if unreadable."""
    r = subprocess.run(
        [ffprobe_exe, "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    counts: dict[str, int] = {}
    for line in r.stdout.splitlines():
        t = line.strip()
        if t:
            counts[t] = counts.get(t, 0) + 1
    return counts


def strip(mkv: str, mkv_exe: str, ffprobe_exe: str) -> tuple[bool, str]:
    """In-place --no-subtitles remux: temp beside the original (same
    filesystem), then verify zero subtitle streams and unchanged audio+video
    counts before the atomic replace. (False, why) leaves the original
    intact."""
    tmp = os.path.join(os.path.dirname(mkv), Path(mkv).stem + ".stripped.tmp.mkv")
    before = _type_counts(mkv, ffprobe_exe)
    rc = subprocess.run([mkv_exe, "-o", tmp, "--no-subtitles", mkv]).returncode
    if rc > 1:  # 0 ok, 1 warnings (still usable) -- like transcode's merge
        _rmf(tmp)
        return False, f"mkvmerge failed (rc={rc})"
    after = _type_counts(tmp, ffprobe_exe)
    av = {k: v for k, v in (before or {}).items() if k != "subtitle"}
    av_after = {k: v for k, v in (after or {}).items() if k != "subtitle"}
    if before is None or after is None:
        why = "strip verify failed: ffprobe error"
    elif after.get("subtitle", 0):
        why = f"strip verify failed: {after['subtitle']} subtitle stream(s) left"
    elif av_after != av:
        why = f"strip verify failed: audio+video counts changed {av} -> {av_after}"
    else:
        os.replace(tmp, mkv)
        return True, ""
    _rmf(tmp)
    return False, why


def process(path: str, tools: dict[str, str], report: list[str]) -> str:
    """One file: inventory -> policy -> (pass) -> strip. Prints the file's one
    progress line and returns done|skip|fail; FAIL and straggler lines also
    land in the end-of-run report."""
    name = os.path.basename(path)
    stem = Path(path).stem
    video_dir = os.path.dirname(path)

    tracks = subs.inventory(path, tools["ffprobe"])
    if not tracks:
        reason = "already done" if _sidecars_beside(video_dir, stem) else "no subtitle streams"
        print(f"skip {name} ({reason})")
        return "skip"

    keeps, _notes = subs.decide(tracks)
    if not keeps:
        langs = ",".join(sorted({t.lang for t in tracks}))
        line = f"skip {name} (straggler-only: {langs} -- no eng/dan, never stripped)"
        print(line)
        report.append(line)
        return "skip"

    # minimal expected set: one name per forced keep, one per language with a
    # full track. All present and non-empty = the pass already ran; only the
    # strip is left (re-run after an interruption -- no redundant OCR).
    minimal = {subs.sidecar_name(stem, k.track.lang, "forced" if k.track.forced else "")
               for k in keeps}
    n_side = 0
    if not all(_nonempty(os.path.join(video_dir, n)) for n in minimal):
        side_dir = tempfile.mkdtemp(prefix="backfill-side-")
        try:
            res = subs.run_pass(path, side_dir, stem, tools["seconv"], tools["ffprobe"])
            for n in res.notes:
                print(n)
            for s in res.sidecars:
                shutil.move(os.path.join(side_dir, s), os.path.join(video_dir, s))
        finally:
            shutil.rmtree(side_dir, ignore_errors=True)
        if not res.ok:
            fails = "; ".join(n.removeprefix("FAIL ") for n in res.notes if n.startswith("FAIL"))
            line = f"FAIL {name} ({fails}) -- embedded tracks kept"
            print(line)
            report.append(line)
            return "fail"
        if not res.sidecars:  # unreachable after the straggler guard, but never strip
            line = f"skip {name} (pass produced no sidecars -- never stripped)"
            print(line)
            report.append(line)
            return "skip"
        n_side = len(res.sidecars)

    ok, why = strip(path, tools["mkvmerge"], tools["ffprobe"])
    if not ok:
        line = f"FAIL {name} ({why}) -- sidecars kept"
        print(line)
        report.append(line)
        return "fail"
    print(f"done {name} (+{n_side} sidecars)")
    return "done"


def run_backfill(library: str) -> int:
    tools = probe_tools()
    if not tools:
        return 1
    if not os.path.isdir(library):
        print(f"backfill: {library} is not a directory", file=sys.stderr)
        return 1

    counts = {"done": 0, "skip": 0, "fail": 0}
    report: list[str] = []
    for path in walk(library):
        counts[process(path, tools, report)] += 1

    lines = [
        f"backfill report -- {library}",
        f"{sum(counts.values())} file(s): {counts['done']} done, "
        f"{counts['skip']} skipped, {counts['fail']} failed",
        *report,
    ]
    print("\n".join(lines))
    with open(os.path.join(library, "backfill-report.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return 1 if counts["fail"] else 0  # skips are not failures


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="backfill.py",
        description="One-off in-place library backfill: OCR embedded subtitles to SRT "
                    "sidecars, then strip the embedded tracks (after verify).",
    )
    p.add_argument("library", nargs="?", metavar="LIBRARY",
                   default=os.path.join(os.environ.get("VIDEOS") or "/mnt/videos", "library"),
                   help="library root (default: $VIDEOS/library or /mnt/videos/library)")
    return p.parse_args(argv)


def main(argv=None) -> None:
    sys.exit(run_backfill(parse_args(argv).library))


if __name__ == "__main__":
    main()
