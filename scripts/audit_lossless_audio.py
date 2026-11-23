#!/usr/bin/env python3
"""Audit the video library for lossless audio tracks; --fix remuxes in place.

Enforces the lossy-only audio rule on the existing library: dry-run by
default, it walks the given library root and prints file, audio track, and
codec for every lossless track (classification comes from skive's transcode
module -- imported, never duplicated), closing with a summary; it changes
nothing. With --fix, each offending file is remuxed audio-only through a
temp beside it: video and subtitles stream-copied, lossy audio copied,
lossless transcoded to ac3. The temp is verified (no lossless audio
remains) before it replaces the original; any failure -- ffmpeg, probe,
verify -- deletes the temp and leaves the original untouched. Compliant
files run no ffmpeg at all.

The music library never enters: its FLAC is by design, so never point this
at the music tree. Extras trees are never touched (ADR 0003's discipline).

Usage:  python scripts/audit_lossless_audio.py [--fix] [LIBRARY]
        (default /mnt/videos/library, or $VIDEOS/library when VIDEOS is set)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from skive.transcode import _is_lossless  # in-repo operator script: shared lossless set, one source

_TMP_SUFFIX = ".audiofix.tmp.mkv"


def _rmf(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def walk(root: str):
    """All *.mkv under root, extras trees pruned (ADR 0003: untouched) and
    our own remux temps ignored, sorted for a stable run order."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d.lower() != "extras")
        for name in sorted(filenames):
            if name.lower().endswith(".mkv") and not name.endswith(_TMP_SUFFIX):
                yield os.path.join(dirpath, name)


def _audio_tracks(path: str, ffprobe_exe: str) -> list[tuple[str, str]] | None:
    """(codec_name, profile) per audio stream in order, or None if
    unreadable -- argv mirrors _probe_audio in skive/transcode.py."""
    try:
        r = subprocess.run(
            [
                ffprobe_exe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,profile",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # unresolvable exe: fail closed, never guess the audio
        return None
    if r.returncode != 0:
        return None
    try:
        streams = json.loads(r.stdout).get("streams", [])
    except ValueError:
        return None
    return [
        (s.get("codec_name") or "", s.get("profile") or "")
        for s in streams
        if s.get("codec_type") == "audio"
    ]


def process(path: str, ffprobe_exe: str, fix: bool, ffmpeg_exe: str) -> str:
    """One file: probe, report every lossless track, and (fix mode) remux
    audio-only through a verified temp. Returns compliant|fixed|failed."""
    name = os.path.basename(path)
    tracks = _audio_tracks(path, ffprobe_exe)
    if tracks is None:
        print(f"FAIL {name} (ffprobe unreadable) -- original kept")
        return "failed"
    for i, (codec, profile) in enumerate(tracks):
        if _is_lossless(codec, profile):
            print(f"{name}: audio track {i} {codec}" + (f" ({profile})" if profile else ""))
    if not any(_is_lossless(c, p) for c, p in tracks):
        print(f"ok {name} (no lossless audio)")
        return "compliant"
    if not fix:
        return "fixed"  # would-fix; the summary wording marks dry-run

    tmp = os.path.join(os.path.dirname(path), Path(path).stem + _TMP_SUFFIX)
    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        path,
        "-map",
        "0:v",
        "-c:v",
        "copy",
        "-map",
        "0:s?",
        "-c:s",
        "copy",
    ]
    for i, (codec, profile) in enumerate(tracks):
        cmd += ["-map", f"0:a:{i}", f"-c:a:{i}", "ac3" if _is_lossless(codec, profile) else "copy"]
    cmd.append(tmp)
    rc = subprocess.run(cmd, capture_output=True, text=True, check=False).returncode
    if rc != 0:
        _rmf(tmp)
        print(f"FAIL {name} (ffmpeg rc={rc}) -- original kept")
        return "failed"
    after = _audio_tracks(tmp, ffprobe_exe)
    if after is None:
        why = "verify failed: ffprobe unreadable"
    elif any(_is_lossless(c, p) for c, p in after):
        why = "verify failed: lossless audio remains"
    else:
        os.replace(tmp, path)
        print(f"fixed {name}")
        return "fixed"
    _rmf(tmp)
    print(f"FAIL {name} ({why}) -- original kept")
    return "failed"


def _safe_process(path: str, ffprobe_exe: str, fix: bool, ffmpeg_exe: str) -> str:
    """One unexpected exception must not abort the whole library run."""
    try:
        return process(path, ffprobe_exe, fix, ffmpeg_exe)
    except Exception as e:  # noqa: BLE001 -- the whole point is to survive anything
        print(
            f"FAIL {os.path.basename(path)} (unexpected {type(e).__name__}: {e}) -- original kept"
        )
        return "failed"


def run_audit(library: str, fix: bool = False) -> int:
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe or (fix and not ffmpeg):
        need = "ffprobe and ffmpeg" if fix else "ffprobe"
        print(f"audit: {need} not on PATH", file=sys.stderr)
        return 1
    if not os.path.isdir(library):
        print(f"audit: {library} is not a directory", file=sys.stderr)
        return 1

    counts = {"compliant": 0, "fixed": 0, "failed": 0}
    for path in walk(library):
        counts[_safe_process(path, ffprobe, fix, ffmpeg or "")] += 1

    what = "fixed" if fix else "with lossless audio"
    print(
        f"{sum(counts.values())} file(s): {counts['compliant']} compliant, "
        f"{counts['fixed']} {what}, {counts['failed']} failed"
    )
    return 1 if counts["failed"] else 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="audit_lossless_audio.py",
        description="Report every lossless audio track in the video library; --fix remuxes "
        "audio-only to ac3 in place (video/subtitles copied, verified before replace).",
    )
    p.add_argument(
        "--fix",
        action="store_true",
        help="remux non-compliant files in place (default: dry-run report)",
    )
    p.add_argument(
        "library",
        nargs="?",
        metavar="LIBRARY",
        default=os.path.join(os.environ.get("VIDEOS") or "/mnt/videos", "library"),
        help="library root (default: $VIDEOS/library or /mnt/videos/library)",
    )
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    sys.exit(run_audit(args.library, args.fix))


if __name__ == "__main__":
    main()
