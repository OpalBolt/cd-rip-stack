#!/usr/bin/env python3
"""One-off: re-encode The Day After Tomorrow (2004) with its black bars
cropped (0/72/0/72 -> 704x432, verified twice by scripts/crop_detect_test.py).

Replicates skive's VAAPI encode exactly (ADR 0010: hevc_vaapi CQP 24,
audio stream-copied, bwdif on interlaced frames only, 64-px width
alignment — 704 already is), adds the bar crop, and ships the same stream
layout as the current library encode: video + 4 audio, zero subtitle
tracks (the existing .eng.srt/.dan.srt sidecars and validation.json stay
beside it untouched).

Run INSIDE the laptop transcode box (needs /dev/dri/renderD128 with HEVC
encode). Fully reversible until you clean up: the old encode is kept as
"<title>.pre-crop.mkv" beside the new one, the lossless source is never
touched, and nothing moves until the new file passes verification.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

TITLE = "The Day After Tomorrow (2004)"
SRC = f"/mnt/videos/lossless/movies/{TITLE}/{TITLE}_t00.mkv"
OLD = f"/mnt/videos/library/movies/{TITLE}/{TITLE}.mkv"
KEEP = f"/mnt/videos/library/movies/{TITLE}/{TITLE}.pre-crop.mkv"
CROP = "crop=704:432:0:72"  # the trial verdict; subsumes the 64-px align crop
DUR_S = 7116                # source duration the output must match (+/- 5 s)


def die(msg: str) -> None:
    print(f"abort: {msg}", file=sys.stderr)
    sys.exit(1)


def find_tool(exe: str) -> str:
    """/usr/bin first: the host's mise shims bleed into the box through the
    shared home, and mise's ffmpeg/vainfo see a mismatched (non-freeworld)
    VA-API driver -- device creation dies on an undefined symbol (skive's
    _find_tool and bench.sh's PATH pin exist for exactly this)."""
    if os.path.exists(f"/usr/bin/{exe}"):
        return f"/usr/bin/{exe}"
    found = shutil.which(exe)
    if not found:
        die(f"{exe} not found on PATH")
    return found


def vcn_probe(vainfo_exe: str) -> dict | None:
    """skive's _vcn_probe, verbatim contract: low_power only when the LP
    entrypoint exists and the classic one does not."""
    try:
        out = subprocess.run([vainfo_exe, "--display", "drm"], capture_output=True,
                             text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if "VAEntrypointEncSlice" not in out.stdout:
        return None
    classic = re.search(r"^ *VAProfileHEVC[^:\n]*:[ \t]*VAEntrypointEncSlice[ \t]*$",
                        out.stdout, re.M)
    lp = re.search(r"^ *VAProfileHEVC[^:\n]*:[ \t]*VAEntrypointEncSliceLP[ \t]*$",
                   out.stdout, re.M)
    return {"low_power": bool(lp and not classic)}


def probe(ffprobe_exe: str, path: str) -> dict:
    out = subprocess.run(
        [ffprobe_exe, "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height:format=duration",
         "-of", "json", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-w", default=os.path.expanduser("~/.cache/skive-redo"),
                    help="workdir (default ~/.cache/skive-redo)")
    ap.add_argument("--preflight", action="store_true",
                    help="run the checks only, encode nothing")
    args = ap.parse_args()

    ff = find_tool("ffmpeg")
    ffp = find_tool("ffprobe")
    vain = find_tool("vainfo")
    print(f"tools: {ff}")

    src_audio = None
    # --- preflight ---------------------------------------------------
    if not os.path.isfile(SRC):
        die(f"lossless source missing: {SRC}")
    if os.path.getsize(SRC) < 2**32:
        die(f"source looks wrong ({os.path.getsize(SRC)} bytes): {SRC}")
    if not os.path.isfile(OLD):
        die(f"library encode missing: {OLD}")
    for side in (OLD[:-4] + ".eng.srt", OLD[:-4] + ".dan.srt"):
        if not os.path.isfile(side):
            die(f"sidecar missing (would be orphaned): {side}")
    if not os.path.exists("/dev/dri/renderD128"):
        die("no /dev/dri/renderD128 -- run inside the laptop transcode box")
    vcn = vcn_probe(vain)
    if vcn is None:
        die("vainfo shows no HEVC encode -- run inside the laptop transcode box")
    doc = probe(ffp, SRC)
    src_audio = [s["codec_name"] for s in doc["streams"] if s["codec_type"] == "audio"]
    if len(src_audio) != 4:
        die(f"source audio tracks changed ({len(src_audio)}, expected 4)")
    print(f"preflight ok: source {os.path.getsize(SRC) / 2**30:.1f} GiB, "
          f"{len(src_audio)} audio tracks, low_power={vcn['low_power']}")
    if args.preflight:
        return 0

    # --- copy local, encode ------------------------------------------
    os.makedirs(args.w, exist_ok=True)
    local_src = os.path.join(args.w, os.path.basename(SRC))
    local_out = os.path.join(args.w, TITLE + ".crop.mkv")
    print(f"copying source to {local_src} ...")
    shutil.copy2(SRC, local_src)

    vf = ("bwdif=mode=send_frame:parity=auto:deint=interlaced,"
          f"{CROP},format=nv12|p010,hwupload")
    cmd = [ff, "-hide_banner", "-y", "-loglevel", "warning", "-stats",
           "-vaapi_device", "/dev/dri/renderD128", "-i", local_src,
           "-map", "0:v:0", "-map", "0:a?", "-c:a", "copy", "-vf", vf,
           "-c:v", "hevc_vaapi", "-rc_mode", "CQP", "-global_quality", "24"]
    if vcn["low_power"]:
        cmd += ["-low_power", "1"]
    cmd.append(local_out)
    print("encoding:", " ".join(cmd))
    try:
        rc = subprocess.run(cmd).returncode
    except KeyboardInterrupt:
        print("\ninterrupted -- library untouched, partial output in workdir")
        return 130
    if rc != 0 or not os.path.isfile(local_out):
        die(f"ffmpeg failed (rc={rc}) -- library untouched, workdir kept")

    # --- verify --------------------------------------------------------
    doc = probe(ffp, local_out)
    v = next(s for s in doc["streams"] if s["codec_type"] == "video")
    out_audio = [s["codec_name"] for s in doc["streams"] if s["codec_type"] == "audio"]
    problems = []
    if (v["width"], v["height"]) != (704, 432):
        problems.append(f"geometry {v['width']}x{v['height']}, wanted 704x432")
    if abs(float(doc["format"]["duration"]) - DUR_S) > 5:
        problems.append(f"duration {doc['format']['duration']:.0f}s, wanted {DUR_S}s +/- 5")
    if out_audio != src_audio:
        problems.append(f"audio {out_audio} != source {src_audio}")
    if any(s["codec_type"] == "subtitle" for s in doc["streams"]):
        problems.append("subtitle tracks present (sidecars already cover them)")
    gb = os.path.getsize(local_out) / 2**30
    if not 0.5 <= gb <= 4.0:
        problems.append(f"size {gb:.2f} GiB outside sanity window")
    if problems:
        die("verify failed: " + "; ".join(problems)
            + f" -- library untouched, output kept at {local_out}")

    # --- swap ----------------------------------------------------------
    os.rename(OLD, KEEP)
    try:
        shutil.move(local_out, OLD)
    except OSError:
        os.rename(KEEP, OLD)  # put it back; the verified file stays in workdir
        die(f"could not move the new encode into place -- old one restored, "
            f"verified output waiting at {local_out}")
    os.remove(local_src)

    print(f"done: {OLD} is now 704x432 ({gb:.2f} GiB), old encode kept at:\n  {KEEP}")
    print("eyeball it in Jellyfin (bars gone, AR right, subs load), then:\n"
          f"  rm '{KEEP}' && rm -rf '{args.w}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
