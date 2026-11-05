"""skive transcode — batch re-encode rip/ staging into the Jellyfin library.

BC-250 slices 02+03, ported from the frozen windows/transcode.py: the movie
MAIN arm with its inline subtitle pass. The batch walks the videos share's
rip/ staging at both resting depths, skips junk labels, picks each movie's
main (the validation file's recorded main, biggest-bytes only as the legacy
fallback), encodes with HandBrake at native resolution (zero subtitle
tracks), and ships to library/movies/<Name>/<Name>.mkv with the validation
document travelling beside the encode and the encode result appended to it.
The inline subtitle pass (skive.subs, the graduated windows module) OCRs the
main's eng/dan tracks into sidecars beside the encode — forced tracks always
kept, per-language full tracks settled by OCR'd cue count into `.sdh`; a
failed pass ships the encode subless and parks the sources in the issues
folder with the run's evidence, failing the run. Nothing is deleted until
its sidecars are verified: the lossless source only moves after the ship
(-k parks it under lossless/<type>/<Name (Year)> instead of deleting it;
--retry re-runs quarantined items from the issues folder). The TV arm
(slice 04) encodes every staged episode — no main pick, no merge, a double
stays one file — into
Season NN (season 0 in Specials) with the same subtitle pass and the show's
validation document copied into each Season folder beside the encodes; TV
extras file under the show's extras/ with embedded subs kept, no pass.
A split main merges into one encode when every part's stream signature
matches (mkvmerge: the unsuffixed title first, then the .pN parts in order);
a signature mismatch quarantines the parts whole for a human, and an
mkvmerge failure leaves the sources in place — logged, batch continuing.
Movie extras — bonus _t titles and ` - Extra NN` disc labels alike — keep
every embedded track (no subtitle pass, no zero-subs trick) and file under
the movie's extras/, the number rolled past taken names.

Shared policy (run log, verbosity tiers, interrupt machinery, sanitize)
comes from skive.common; the validation document's reader/writer/name from
skive.validation. The Windows original's deliberate duplicates are not
repeated here. Each item's parts (copy, merge, subs, encode) log their own
timed rip.jsonl phase line carrying the part's throughput; the console gets
a per-part duration line, one throttled encode progress line a minute, and a
per-item summary at the end of the run. A phase that dies mid-run (FATAL,
interrupt) closes with FAIL/interrupted and its elapsed.
"""

import json
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from skive import common
from skive import subs
from skive import ui
from skive.validation import (
    VALIDATION_FILENAME,
    load_validation,
    read_extras_marker,
    write_validation,
)

# Staged names: <Movie (Year)>_tNN[.pN].mkv (titles), <Movie (Year)> - Extra NN[.pN].mkv
# (extras disc), <Show (Year)>_SNNENN[-ENN].mkv (TV, incl. doubles).
_RX_TITLE = re.compile(
    r"^(?P<movie>.+)_(?P<sfx>[te])(?P<num>\d+)(?P<part>\.p(?P<pn>\d+))?\.mkv$"
)
_RX_EXTRA = re.compile(
    r"^(?P<movie>.+) - Extra (?P<num>\d+)(?P<part>\.p(?P<pn>\d+))?\.mkv$"
)
_RX_EP = re.compile(r"^(?P<movie>.+)_S(?P<sn>\d{2})E(?P<en>\d{2})(?P<tail>-E\d{2})?\.mkv$")
_RX_MAIN = re.compile(r"^t\d+$")  # validation.json's recorded main: "tNN"
_RX_DDIR = re.compile(r"^d\d+$")  # rip/<type>/<Name (Year)>/d<disc> staging subdir
_TV_ROOTS = ("TV", "tv")  # rip/TV (legacy ps1 staging) and rip/tv (SPEC layout)
# one comma-joined argument: stays a single argv entry
# pcm has no HandBrake passthru: the Fedora build rejects the whole job with
# "Invalid audio codec in autopassthru copy mask (copy:pcm)" -- and still
# exits 0 (the Conan failure). LPCM audio takes the ac3 fallback instead.
_COPY_MASK = "aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac"
# encode-progress parse for the run view's status panel (TTY only)
_RX_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_RX_ETA = re.compile(r"ETA\s+(\S+)")
_POS = ""  # current "item n of m" status line while a run view is live


def _tool(name: str, line: str) -> None:
    """Destination of a gated tool line (the tier logic and hidden-replay
    buffers stay at the call sites): the run view tool-prefixes it into the
    log panel on a TTY, ``{name}  {line}`` on stderr otherwise."""
    v = ui.view()
    if v is None:
        print(line, flush=True)
    else:
        v.tool(name, line)


def _progress_line(name: str, stripped: str) -> None:
    """A surfaced encode-progress line (throttled or -vv): into the log panel
    plus, on a TTY, the status panel's progress/ETA; unparseable -> omitted."""
    _tool(name, stripped)
    v = ui.view()
    if v is not None:
        m, e = _RX_PCT.search(stripped), _RX_ETA.search(stripped)
        v.progress(float(m.group(1)) if m else None,
                   e.group(1) if e else None)


def _phase_status(part: str, name: str) -> None:
    """Run view status: session position + the current part."""
    v = ui.view()
    if v is not None and v.tty:
        v.status(*(([_POS] if _POS else []) + [f"{part}: {name}"]))


@dataclass
class Item:
    path: str
    name: str
    base: str
    movie: str
    sfx: str
    num: int
    num_str: str
    part: int
    size: int
    is_tv: bool
    sn: str | None = None
    en: str | None = None
    tail: str = ""


def _find_on_path(exe: str) -> str | None:
    """Everything resolves on PATH inside the distrobox (Linux tool names, no
    .exe, no %ProgramFiles% fallback)."""
    found = shutil.which(exe)
    if not found:
        ui.fail(f"{exe} not found on PATH -- see README (transcode box: Install)")
    return found


def _find_tool(exe: str) -> str | None:
    """/usr/bin first for the hardware path's tools: the host's mise shims
    bleed into the box through the shared home, and mise's ffmpeg/vainfo see
    a different (patent-stripped) VA-API driver than the container's RPM
    Fusion builds (bench.sh hit this as "no usable encoding entrypoint")."""
    if os.path.exists(os.path.join("/usr/bin", exe)):
        return os.path.join("/usr/bin", exe)
    return shutil.which(exe)


def _vcn_probe() -> dict | None:
    """The box's VCN HEVC encode capability: {"low_power": bool} when live,
    None otherwise. --display drm skips wayland/x11 probing, which can die
    inside containers after the driver loads; the entrypoints are matched
    end-anchored so classic EncSlice does not read as LP."""
    exe = _find_tool("vainfo")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--display", "drm"], capture_output=True,
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


def _stream_sig(src: str, ffprobe_exe: str) -> list[tuple[str, str, str]] | None:
    """(codec_type, codec_name, language-or-'und') per stream, in order; None if unreadable."""
    r = subprocess.run(
        [ffprobe_exe, "-v", "error", "-show_entries",
         "stream=codec_type,codec_name:stream_tags=language", "-of", "json", src],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    try:
        streams = json.loads(r.stdout).get("streams", [])
        return [(s.get("codec_type") or "", s.get("codec_name") or "",
                 (s.get("tags") or {}).get("language") or "und") for s in streams]
    except (ValueError, AttributeError):
        return None


def _split_mismatch(parts: list[Item], ffprobe_exe: str) -> str:
    """Why a split main must not merge: '' when every part's stream signature
    matches part 1, else a FAIL detail naming the part and stream position."""
    sigs = []
    for p in parts:
        sig = _stream_sig(p.path, ffprobe_exe)
        if sig is None:
            return f"{p.name}: ffprobe failed"
        sigs.append(sig)
    first = sigs[0]

    def fmt(s):
        return f"{s[0]}/{s[1]}/{s[2]}" if s else "-"

    for p, sig in zip(parts[1:], sigs[1:]):
        for k in range(max(len(sig), len(first))):
            a = sig[k] if k < len(sig) else None
            b = first[k] if k < len(first) else None
            if a != b:
                return (f"{p.name} stream {k} ({fmt(a)}) != "
                        f"{parts[0].name} stream {k} ({fmt(b)})")
    return ""


def _scan(input_dir: str, want: list[str]) -> tuple[list[Item], set[str]]:
    """Walk the input tree (rip/ staging; issues/ with --retry) for
    .mkv files; return parsed items + which prefixes hit.

    Type detection is positional: the first path segment under the input root
    is the type — `tv` (or legacy `TV`) is TV, anything else a movie. Titles
    rest at either depth — promoted at <type>/<Name (Year)>/<file>.mkv or
    fresh in the drive subdir <type>/<Name (Year)>/d{disc}/<file>.mkv — and
    one rglob finds both.
    """
    items: list[Item] = []
    want_hit: set[str] = set()
    root = Path(input_dir)
    for f in sorted(root.rglob("*.mkv")):
        if not f.is_file():
            continue
        rel = f.relative_to(root)
        is_tv = bool(rel.parts) and rel.parts[0] in _TV_ROOTS
        m = _RX_TITLE.match(f.name)
        sn = en = None
        tail = ""
        if m:
            movie, sfx = m.group("movie"), m.group("sfx")
            num_str = m.group("num")
            num = int(num_str)
            part = int(m.group("pn")) if m.group("part") else -1
        elif is_tv and (m := _RX_EP.match(f.name)):
            movie = m.group("movie")
            sfx = "S"
            num_str = m.group("en")
            num = int(m.group("en"))
            part = -1
            sn, en = m.group("sn"), m.group("en")
            tail = m.group("tail") or ""
        elif (m := _RX_EXTRA.match(f.name)):
            movie = m.group("movie")
            sfx = "e"
            num_str = m.group("num")
            num = int(num_str)
            part = int(m.group("pn")) if m.group("part") else -1
        else:
            ui.info(f"skip (junk label -- mid-rip or failed rename): {f}")
            continue
        # junk guard: no "(YYYY)" tail = rip in progress or failed rename
        if not re.search(r"\(\d{4}\)$", movie):
            ui.info(f"skip (junk label -- mid-rip or failed rename): {f}")
            continue
        if want:
            want_s = {common.sanitize(w): w for w in want}
            if not any(f.name.startswith(ws) for ws in want_s):
                continue
            for ws, w in want_s.items():
                if f.name.startswith(ws):
                    want_hit.add(w)
        items.append(Item(
            path=str(f), name=f.name, base=f.stem, movie=movie, sfx=sfx,
            num=num, num_str=num_str, part=part, size=f.stat().st_size,
            is_tv=is_tv, sn=sn, en=en, tail=tail,
        ))
    return items, want_hit


def _probe_height(src: str, ffprobe_exe: str) -> int:
    """Height of the first video stream; 0 if unreadable."""
    r = subprocess.run(
        [ffprobe_exe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=height", "-of", "csv=p=0", src],
        capture_output=True, text=True,
    )
    try:
        return int(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def _probe_duration(src: str, ffprobe_exe: str) -> int | None:
    """Whole-second duration of src (format=duration, the rip flow's probe
    shape); None when the probe can't read it."""
    r = subprocess.run(
        [ffprobe_exe, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", src],
        capture_output=True, text=True,
    )
    try:
        return int(float(r.stdout.strip()))
    except ValueError:
        return None


# Black-bar crop detection (ported verbatim from the committed trial,
# scripts/crop_detect_test.py, which stays the acceptance oracle): ~6
# cropdetect probes spread over 10-90% of the duration; per-side bar is the
# median across probes; at most one probe per side may dissent beyond
# +/-4 px (a lone dissent is a credits/dark-scene outlier); sides under
# 16 px crop as nothing; per-axis totals capped at 30%; fewer than 3
# readable probes = detection failed = no crop. A refusal never fails the
# item: the encode ships uncropped.
CROP_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
MIN_PX = 16        # one DVD macroblock; a bar under this is alignment noise
MAX_AXIS_PCT = 30  # per-axis crop total above this smells like detection error
TOLERANCE = 4      # a dissenting probe sits further than this from the median
MIN_READINGS = 3   # fewer good probes than this = detection failed
MAX_DISSENT = 1    # lone dissent per side absorbed (credits / dark scene)
_PROBES = 6        # sample points over 10-90% of the duration
_FRAMES = 30       # frames decoded per probe window


def _probe_frame(src: str, ffprobe_exe: str) -> tuple[int, int, str, float | None]:
    """(width, height, field_order, duration_s) of the first video stream, in
    the trial oracle's one-JSON-probe shape (the float duration feeds the
    probe stamps -- integer seconds shifts them onto different frames);
    None duration if unreadable."""
    r = subprocess.run(
        [ffprobe_exe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,field_order:format=duration",
         "-of", "json", src],
        capture_output=True, text=True,
    )
    try:
        doc = json.loads(r.stdout)
        st = doc["streams"][0]
        dur = doc.get("format", {}).get("duration")
        return (int(st["width"]), int(st["height"]),
                st.get("field_order") or "?",
                float(dur) if dur is not None else None)
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def _probe_bars(ff: str, path: str, at_s: float, frames: int,
                w: int, h: int) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) bar widths at one timestamp; None = no read.
    cropdetect limit=24 round=2, last hit wins (the settled read)."""
    p = subprocess.run(
        [ff, "-hide_banner", "-nostats",
         "-ss", f"{at_s:.1f}", "-i", path, "-an", "-sn",
         "-frames:v", str(frames),
         "-vf", "cropdetect=limit=24:round=2", "-f", "null", "-"],
        capture_output=True, text=True)
    hits = CROP_RE.findall(p.stderr)
    if not hits:
        return None
    cw, ch, x, y = map(int, hits[-1])
    return x, y, w - x - cw, h - y - ch


def _gate(readings: list[tuple[int, int, int, int]], w: int, h: int):
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


def _detect_crop(src: str, ffprobe_exe: str) -> tuple[tuple[int, int, int, int] | None, str]:
    """Detect baked-in black bars on the local copy. Returns (crop, note):
    the applied crop (l,t,r,b) or None, plus the note for the run log — the
    applied crop with its resulting geometry, or the refusal reason. NEVER
    fails: any probe failure/exception reads as no crop + a detection-failed
    note (the encode ships uncropped).
    Tools resolve through _find_tool (the mise-shim trap), not bare names."""
    try:
        frame = _probe_frame(src, ffprobe_exe)
        ff = _find_tool("ffmpeg")
        if frame is None or frame[3] is None or ff is None:
            return None, "no crop -- detection failed"
        w, h, _field, dur = frame
        stamps =[dur * (0.1 + 0.8 * i / (_PROBES - 1)) for i in range(_PROBES)]
        readings = [r for r in (_probe_bars(ff, src, t, _FRAMES, w, h)
                                for t in stamps) if r is not None]
        if len(readings) < MIN_READINGS:
            return None, (f"no crop -- detection failed "
                          f"({_PROBES - len(readings)} probe(s) gave no reading)")
        crop, reason, detail = _gate(readings, w, h)
        if crop is None:
            return None, f"no crop -- {reason} ({detail})"
        l, t, r_, b = crop
        return crop, (f"crop {l}/{t}/{r_}/{b} -> "
                      f"{w - l - r_}x{h - t - b} ({detail})")
    except Exception:
        return None, "no crop -- detection failed"


def _preset(height: int) -> str:
    """Largest standard preset that does NOT upscale the source."""
    if height <= 480:
        return "H.265 MKV 480p30"
    if height <= 576:
        return "H.265 MKV 576p25"
    if height <= 720:
        return "H.265 MKV 720p30"
    if height <= 1080:
        return "H.265 MKV 1080p30"
    return "H.265 MKV 2160p60 4K"


def _encode_vaapi(src: str, out: str, no_subs: bool = False,
                  log: common.RipLog | None = None,
                  crop: tuple[int, int, int, int] | None = None) -> bool:
    """Full-VCN re-encode via ffmpeg VAAPI (HandBrake has no VAAPI encoder on
    Linux): hevc_vaapi CQP 24, audio and subtitles stream-copied, bwdif
    deinterlaces only frames marked interlaced (progressive passes untouched
    -- the DVD detelecine/decomb equivalent), and the width is cropped to
    VCN's 64-pixel alignment because the hardware's own padding bleeds a
    stretched right edge (720-wide DVDs pad to 768); height padding rides
    the conformance window and is left alone. Process/log discipline mirrors
    _encode: every line raw-logged as "ffmpeg", frame= progress throttled to
    one a minute, hidden lines replayed on failure, rc==0 alone is not
    success (the output must exist)."""
    ff = _find_tool("ffmpeg")
    vcn = _vcn_probe()
    if not ff or not vcn:
        ui.fail("hardware path needs ffmpeg and a VCN with HEVC "
                f"encode (vainfo) -- {'ffmpeg' if not ff else 'vainfo'} is missing "
                "(scripts/setup-transcode.sh installs both)")
        return False
    vf = "bwdif=mode=send_frame:parity=auto:deint=interlaced,"
    if crop:  # the detected bar crop rides BEFORE the 64-px alignment crop
        l, t, r, b = crop
        vf += f"crop=iw-{l}-{r}:ih-{t}-{b}:{l}:{t},"
    vf += "crop=floor(iw/64)*64:ih,format=nv12|p010,hwupload"
    cmd = [ff, "-hide_banner", "-y",
           "-vaapi_device", "/dev/dri/renderD128",
           "-i", src,
           "-map", "0:v:0", "-map", "0:a?"]
    if not no_subs:
        cmd += ["-map", "0:s?"]
    cmd += ["-c:a", "copy", "-c:s", "copy", "-vf", vf,
            "-c:v", "hevc_vaapi", "-rc_mode", "CQP", "-global_quality", "24"]
    if vcn["low_power"]:
        cmd += ["-low_power", "1"]
    cmd.append(out)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    verbose = common.VERBOSITY
    hidden: list[str] = []  # console-suppressed lines, replayed on failure
    last_prog = 0.0  # monotonic ts of the last console progress line
    try:
        for line in proc.stdout:
            stripped = line.rstrip()
            if stripped.startswith("frame=") and "fps=" in stripped:
                # progress \r updates: throttle, never hidden-replay, raw-log
                # only what actually printed
                if verbose >= 2:
                    _progress_line("ffmpeg", stripped)
                    if log is not None:
                        log.raw("ffmpeg", stripped)
                elif time.monotonic() - last_prog >= 60:
                    _progress_line("ffmpeg", stripped)
                    last_prog = time.monotonic()
                    if log is not None:
                        log.raw("ffmpeg", stripped)
                continue
            if verbose >= 2 or (verbose >= 1 and "rror" in stripped):
                _tool("ffmpeg", stripped)
            elif stripped:
                hidden.append(stripped)
            if log is not None:
                log.raw("ffmpeg", stripped)
        rc = proc.wait()
    except KeyboardInterrupt:
        # CONTEXT.md Interrupt: stop the encode child; the partial temp
        # output is dropped by run_transcode (sources only move on success).
        common.kill_child_after_grace(proc)
        raise
    if rc != 0:
        for line in hidden:
            _tool("ffmpeg", line)
        return False
    if not os.path.exists(out):
        for line in hidden:
            _tool("ffmpeg", line)
        ui.fail(f"ffmpeg exited 0 but wrote no output: {out}")
        return False
    return True


def _encode(src: str, out: str, encoder: str, hb_exe: str, ffprobe_exe: str,
            no_subs: bool = False, log: common.RipLog | None = None,
            speed: str | None = None,
            crop: tuple[int, int, int, int] | None = None) -> bool:
    """HandBrake pass-through re-encode at native resolution (the x265 paths;
    the vaapi path dispatches to _encode_vaapi before any of this). no_subs
    selects zero subtitle tracks (features and TV
    episodes, the language-list trick); extras keep pass-through (embedded
    subs stay). Every HandBrake output line is
    written verbatim to the run's raw log (log.raw), the same tee discipline as
    skive/makemkv.py, while the console sees it gated by the verbosity tiers
    (common.VERBOSITY): quiet at 0, HandBrake error lines at -v, everything at -vv;
    anything the level hid is replayed verbatim when the encode fails.
    Progress \\r updates ("Encoding: ... %") are not evidence: the console sees
    one a minute (every line at -vv), and they never enter the failure replay
    or the raw log. rc==0 alone is not success -- the output file must exist,
    because a rejected job init (bad argv) exits 0 writing nothing (the
    Conan FATAL)."""
    if encoder == "vaapi":  # hardware path: ffmpeg, never HandBrakeCLI
        return _encode_vaapi(src, out, no_subs=no_subs, log=log, crop=crop)
    if encoder == "x265-fast":  # auto's CPU fallback; an item's own marker
        speed = speed or "fast"  # speed (extras superfast) still wins
    height = _probe_height(src, ffprobe_exe)
    cmd = [hb_exe, "-i", src, "-o", out]
    preset_h = height
    if height and crop:
        # preset judged on the CROPPED frame so the never-upscale rule holds
        preset_h = height - crop[1] - crop[3]
    cmd += ["--preset", _preset(preset_h) if height else "H.265 MKV 1080p30"]
    if crop:
        # gate returns (l,t,r,b); HandBrakeCLI wants t:b:l:r
        l, t, r_, b = crop
        cmd += ["--crop", f"{t}:{b}:{l}:{r_}"]
    if 0 < height <= 576:
        cmd += ["--detelecine", "--decomb"]  # DVD: kill telecine/interlace combing
    cmd += [
        "--all-audio", "--aencoder", "copy",
        "--audio-copy-mask", _COPY_MASK,
        "--audio-fallback", "ac3",
    ]
    if no_subs:
        # --all-subtitles respects the language list, so a language no track
        # matches selects zero subtitle tracks
        cmd += ["--subtitle-lang-list", "zzz"]
    cmd += ["--all-subtitles", "--subtitle-burned=none"]
    if speed is not None:
        # after --preset, so it overrides only the encoder preset (extras:
        # superfast; the container settings stay the preset's)
        cmd += ["--encoder-preset", speed]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    verbose = common.VERBOSITY
    hidden: list[str] = []  # console-suppressed lines, replayed on failure
    last_prog = 0.0  # monotonic ts of the last console progress line
    try:
        for line in proc.stdout:
            stripped = line.rstrip()
            if "Encoding:" in stripped and "%" in stripped:
                # progress \\r updates: throttle, never hidden-replay, raw-log
                # only what actually printed
                if verbose >= 2:
                    _progress_line("handbrake", stripped)
                    if log is not None:
                        log.raw("handbrake", stripped)
                elif time.monotonic() - last_prog >= 60:
                    _progress_line("handbrake", stripped)
                    last_prog = time.monotonic()
                    if log is not None:
                        log.raw("handbrake", stripped)
                continue
            if verbose >= 2 or (verbose >= 1 and stripped.startswith("ERROR")):
                _tool("handbrake", stripped)
            elif stripped:
                hidden.append(stripped)
            if log is not None:
                log.raw("handbrake", stripped)
        rc = proc.wait()
    except KeyboardInterrupt:
        # CONTEXT.md Interrupt: stop the encode child (POSIX arm: SIGINT, grace,
        # then kill); the partial temp output itself is dropped by run_transcode
        # (sources only move on success).
        common.kill_child_after_grace(proc)
        raise
    if rc != 0:
        for line in hidden:
            _tool("handbrake", line)
        return False
    if not os.path.exists(out):
        # rc==0 but no output: a rejected job init (bad argv, e.g. an invalid
        # copy-mask codec) exits 0 writing nothing. Replay the hidden lines
        # so the reason reaches the console instead of a FATAL two steps later.
        for line in hidden:
            _tool("handbrake", line)
        ui.fail(f"handbrake exited 0 but wrote no output: {out}")
        return False
    return True


def _merge(dst: str, parts: list[str], mkv_exe: str, log: common.RipLog | None = None) -> int:
    """Concatenate a split main's parts into dst with mkvmerge ("+"-joined).
    Every mkvmerge output line is written verbatim to the run's raw log (log.raw),
    the same tee discipline as _encode, while the console sees everything at -vv,
    "Error:" lines at -v, nothing at 0; anything the level hid is replayed
    verbatim when the merge fails (rc 2+ -- rc 1 is a warning, the merged file
    is still usable, so no replay)."""
    cmd = [mkv_exe, "-o", dst, parts[0]]
    for p in parts[1:]:
        cmd += ["+", p]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    hidden: list[str] = []  # console-suppressed lines, replayed on failure
    try:
        for line in proc.stdout:
            stripped = line.rstrip()
            if common.VERBOSITY >= 2 or (common.VERBOSITY >= 1 and stripped.startswith("Error")):
                _tool("mkvmerge", stripped)
            elif stripped:
                hidden.append(stripped)
            if log is not None:
                log.raw("mkvmerge", stripped)
        rc = proc.wait()
    except KeyboardInterrupt:
        # CONTEXT.md Interrupt: stop the merge child; the partial merged file
        # itself is dropped by run_transcode (sources only move on success).
        common.kill_child_after_grace(proc)
        raise
    if rc >= 2:
        for line in hidden:
            _tool("mkvmerge", line)
    return rc


def _units(items, main_num, rec_missing, extras_mark, limit):
    """The encode units in items order (slice 03 prefetch lookahead): a main
    feature's .pN sibling set is ONE unit (they merge), every extra and every
    episode is its own single-item unit. Mirrors the run loop's own grouping
    and skip rules (rec_missing mains, unmarked extras, session-cap deferrals
    -- the first `limit` mains in iteration order encode, the rest defer). If
    the pre-scan and the live loop ever disagree, the prefetched-but-unused
    files are dropped, never crash, never leak -- that degradation is the
    contract."""
    units: list[list[Item]] = []
    taken: set[str] = set()
    mains_started = 0
    for i in items:
        if i.path in taken:
            continue
        if i.is_tv:
            units.append([i])
            taken.add(i.path)
            continue
        if i.sfx == "t" and i.movie in rec_missing:
            taken.add(i.path)
            continue
        if i.sfx == "t" and i.movie in main_num and main_num[i.movie][0] == i.num:
            if limit and mains_started >= limit:
                taken.add(i.path)
                continue
            mains_started += 1
            sibs = sorted(
                (s for s in items if s.movie == i.movie and s.sfx == "t"
                 and s.num == i.num),
                key=lambda s: s.part,
            )
            units.append(sibs)
            taken.update(s.path for s in sibs)
            continue
        if not extras_mark.get(i.movie, {"include": False})["include"]:
            taken.add(i.path)
            continue
        units.append([i])
        taken.add(i.path)
    return units


def _rmf(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _hms(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def _copy_in(src: str, dst: str, log: common.RipLog, parts: list[str]) -> None:
    """Copy one file to local temp as the run's 'copy' phase. The throughput
    line (MB/s) goes to the console and the run log's status -- the slippage
    watch reads it (CONTEXT.md: Run log). The caller registers dst in
    inflight BEFORE calling (CONTEXT.md: Interrupt)."""
    ui.info(f"copying {os.path.basename(src)} to local temp...")
    _phase_status("copy", os.path.basename(src))
    log.phase("copy")
    t0 = time.monotonic()
    _copy2(src, dst)
    secs = max(int(time.monotonic() - t0), 1)
    mb = os.path.getsize(dst) // (1024 * 1024)
    detail = f"{mb} MB at {mb // secs} MB/s"
    ui.info(f"copied in {_hms(secs)} ({detail})")
    log.phase_end(detail)
    parts.append(f"copy {_hms(secs)}")


def _copy2(src: str, dst: str, cancel: threading.Event | None = None,
           bg: str | None = None) -> None:
    """copy2 (mode + mtime preserved) with a progress line every 10%.
    cancel, when set, aborts between chunks leaving a partial dst. bg, when
    set, routes the lines to the run view's background panel (prefetch);
    sequential copies keep the log-panel/fallback routing."""
    total = os.path.getsize(src)
    if total == 0:
        shutil.copy2(src, dst)
        return
    mb = 1024 * 1024
    done = 0
    last = -1
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            buf = fin.read(mb)
            if not buf:
                break
            if cancel is not None and cancel.is_set():
                return
            fout.write(buf)
            done += len(buf)
            pct = done * 100 // total
            if pct >= last + 10 or pct == 100:
                # a TTY view's log panel takes sequential copy lines (raw %
                # lines garble the Live region); prefetch lines go to the
                # background panel instead; fallback prints byte-identical
                v = ui.view()
                line = f"{pct}%  ({done // mb}/{total // mb} MB)"
                if bg is not None:
                    if v is not None and v.tty:
                        v.bg(bg, f"{os.path.basename(src)}  {line}")
                    else:
                        print(f"  {line}")
                elif v is not None and v.tty:
                    v.tool("copy", line)
                else:
                    print(f"  {line}")
                last = pct
    shutil.copystat(src, dst)


def _archive(src: str, item: Item, lossless_root: str, keep: bool) -> bool:
    """-Keep parks the lossless source under lossless/<type>/<Name (Year)>
    (CONTEXT.md: Lossless folder — never clobbering, never auto-deleted);
    else delete. keep comes from the -k/--keep flag. Returns True only when a
    source actually parked there (the run-end --keep banner prints only
    then)."""
    if not keep:
        _rmf(src)
        return False
    d = os.path.join(lossless_root, "tv" if item.is_tv else "movies", item.movie)
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, os.path.basename(src))
    if os.path.exists(dest):
        ui.warn(f"--keep: {dest} already exists -- source left in {os.path.dirname(src)}")
        return False
    shutil.move(src, dest)
    return True


def _quarantine(src: str, item: Item, issues_root: str,
                log: common.RipLog) -> None:
    """Sources that must not be lost wait in issues/<type>/<Name (Year)>
    (CONTEXT.md: Issues folder, never clobbering), together with this run's
    log evidence."""
    d = os.path.join(issues_root, "tv" if item.is_tv else "movies", item.movie)
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, os.path.basename(src))
    if os.path.exists(dest):
        ui.warn(f"quarantine: {dest} already exists -- source left in place")
    else:
        shutil.move(src, dest)
    _copy_run_evidence(log, d)


def _copy_run_evidence(log: common.RipLog, dest_dir: str) -> None:
    """Copy the run's evidence beside a quarantined source: the run's lines
    from logs/rip.jsonl (selected by the run field) are appended to the
    folder's rip.jsonl, and the run's raw log is copied in when it still
    exists (a clean exit deletes it; a failure keeps it). One copy per run
    and folder: a multi-part item quarantines part by part into the same
    folder, and a --retry re-quarantines into its existing folder — a
    folder already holding this run's evidence (its run id in rip.jsonl)
    is left alone, so lines are never appended twice and the same-named
    raw-log copy never clobbers."""
    rip_jsonl = os.path.join(dest_dir, "rip.jsonl")
    if os.path.exists(rip_jsonl):
        try:
            with open(rip_jsonl, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if obj.get("run") == log.run:
                        return  # this run's evidence already landed here
        except OSError:
            pass
    kept: list[str] = []
    try:
        with open(log._run_log, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get("run") == log.run:
                    kept.append(json.dumps(obj, ensure_ascii=False))
    except OSError:
        pass
    if kept:
        with open(rip_jsonl, "a", encoding="utf-8") as fh:
            fh.write("\n".join(kept) + "\n")
    if os.path.exists(log._raw_log):
        dst = os.path.join(dest_dir, os.path.basename(log._raw_log))
        if not os.path.exists(dst):
            shutil.copy2(log._raw_log, dst)


def _extra_name(dest_dir: str, safe: str, num: int, ext: str) -> str:
    """The extras dest name: `<movie> - Extra NN[.pN].mkv`, the number rolled
    forward past names already on disk (makemkv's staging pattern). Bonus _t
    titles keep their disc number when it is free; a staging label never
    reaches the library."""
    n = num
    while os.path.exists(os.path.join(
            dest_dir, f"{safe} - Extra {n:02d}{ext}.mkv")):
        n += 1
    return f"{safe} - Extra {n:02d}{ext}.mkv"


def _probe_size(src: str, ffprobe_exe: str) -> str | None:
    """'WxH' of the first video stream, for the validation encode record's
    size; None if unreadable."""
    frame = _probe_frame(src, ffprobe_exe)
    return f"{frame[0]}x{frame[1]}" if frame else None


def _record_encode(doc_path: str, dest: str, src_num: int, ffprobe_exe: str,
                   crop: tuple[int, int, int, int] | None = None) -> None:
    """Append the main encode's result to the title folder's validation.json
    and copy the doc into the library folder beside the encode. The encodes
    entry is {"file", "src", "dur_s", "crop", "size"} (skive/validation.py's
    schema): the dest file's name, the source title number as "tNN", the
    actual duration probed from the encode output, the applied bar crop as
    "l/t/r/b" (null when none), and the final encode geometry as "WxH". Append-once: an entry already
    naming this dest file — a re-run — is replaced by the fresh probe, never
    duplicated. A doc absent or unreadable here skips the append and the
    copy: the record never crashes the pipeline."""
    doc = load_validation(Path(doc_path))
    if doc is None:
        return
    entry = {"file": os.path.basename(dest), "src": f"t{src_num}",
             "dur_s": _probe_duration(dest, ffprobe_exe),
             "crop": "/".join(str(v) for v in crop) if crop else None,
             "size": _probe_size(dest, ffprobe_exe)}
    encodes = doc.get("encodes")
    if not isinstance(encodes, list):
        encodes = []
    # append-once: this dest file's old entry is replaced, never doubled
    doc["encodes"] = [e for e in encodes
                      if not (isinstance(e, dict) and e.get("file") == entry["file"])]
    doc["encodes"].append(entry)
    try:
        # skive.validation.write_validation: the one writer, UTF-8, 2-space
        # indent, trailing newline
        write_validation(Path(doc_path), doc)
    except OSError:
        return
    try:  # the doc's final copy lands beside the encode — simple overwrite
        shutil.copyfile(doc_path,
                        os.path.join(os.path.dirname(dest), VALIDATION_FILENAME))
    except OSError:
        pass


def _copy_show_doc(src_path: str, dest_dir: str) -> None:
    """Copy the show's validation.json from rip staging into the library
    Season folder beside the encodes -- the checker reads it there. The
    document holds every staged season, so each Season folder carries the
    whole show record; a plain overwrite per item keeps it fresh. The
    staging folder is the file's title folder, walking up past the d<disc>
    subdir a collision straggler still sits in. Absent doc or unreadable
    copy: skip silently -- the record never crashes the pipeline (the
    _record_encode discipline)."""
    folder = os.path.dirname(src_path)
    while _RX_DDIR.match(os.path.basename(folder)):
        folder = os.path.dirname(folder)
    src = os.path.join(folder, VALIDATION_FILENAME)
    if not os.path.isfile(src):
        return
    try:
        shutil.copyfile(src, os.path.join(dest_dir, VALIDATION_FILENAME))
    except OSError:
        pass


def _sub_pass(local_src: str, side_dir: str, base: str, seconv_exe: str,
              ffprobe_exe: str, log: common.RipLog | None = None):
    """Inline subtitle pass on the local copy; the caller creates the sidecar
    dir (and registers it as the interrupt path's temp) and sidecars only move
    beside the encode once it succeeds. Captured ffprobe/seconv output lines
    go to the run's raw log via log.raw (and pass through to the console at
    -vv). Returns (sidecar dir, PassResult)."""
    res = subs.run_pass(local_src, side_dir, base, seconv_exe, ffprobe_exe,
                        log=log, verbose=common.VERBOSITY)
    for n in res.notes:
        ui.info(n)
    return side_dir, res


def _subs_part(local_src: str, side_dir: str, base: str, seconv_exe: str,
               ffprobe_exe: str, log: common.RipLog, parts: list[str]):
    """The subtitle pass as the run's 'subs' phase; status carries the
    sidecar count and any barren drops (CONTEXT.md: Barren track)."""
    log.phase("subs")
    t0 = time.monotonic()
    _phase_status("subs", base)
    side_dir, res = _sub_pass(local_src, side_dir, base, seconv_exe, ffprobe_exe, log=log)
    secs = max(int(time.monotonic() - t0), 1)
    barren = sum(1 for n in res.notes if n.startswith("barren"))
    detail = f"{len(res.sidecars)} sidecar(s)" + (f", {barren} barren" if barren else "")
    log.phase_end(detail if res.ok else f"FAIL: {detail}")
    parts.append(f"subs {_hms(secs)}")
    return side_dir, res


def _detect_part(local_src: str, ffprobe_exe: str, log: common.RipLog,
                 parts: list[str]) -> tuple[tuple[int, int, int, int] | None, str]:
    """Black-bar detection as the run's 'detect' phase, on the local copy,
    right before the encode; the run log carries the applied crop or the
    refusal reason, timed like the other phases. A refusal never fails the
    item -- the encode ships uncropped."""
    log.phase("detect")
    t0 = time.monotonic()
    _phase_status("detect", os.path.basename(local_src))
    crop, note = _detect_crop(local_src, ffprobe_exe)
    secs = max(int(time.monotonic() - t0), 1)
    log.phase_end(note)
    if crop is None:
        ui.info(note)  # loud refusals on console; the phase line is the record
    parts.append(f"detect {_hms(secs)}")
    return crop, note


def _encode_part(local_src: str, local_out: str, encoder: str, hb_exe: str,
                 ffprobe_exe: str, no_subs: bool, log: common.RipLog,
                 parts: list[str], speed: str | None = None,
                 crop: tuple[int, int, int, int] | None = None) -> bool:
    """The encode as the run's 'encode' phase; status carries the output size."""
    log.phase("encode")
    t0 = time.monotonic()
    _phase_status("encode", os.path.basename(local_out))
    ok = _encode(local_src, local_out, encoder, hb_exe, ffprobe_exe,
                 no_subs=no_subs, log=log, speed=speed, crop=crop)
    secs = max(int(time.monotonic() - t0), 1)
    detail = (f"ok, {os.path.getsize(local_out) // (1024 * 1024)} MB"
              if ok else "FAIL")
    log.phase_end(detail)
    parts.append(f"encode {_hms(secs)}")
    return ok


def _merge_part(dst: str, local_parts: list[str], mkv_exe: str,
                log: common.RipLog, parts: list[str]) -> int:
    """mkvmerge as the run's 'merge' phase (0 ok, 1 warning, 2+ failure)."""
    log.phase("merge")
    t0 = time.monotonic()
    _phase_status("merge", os.path.basename(dst))
    mrc = _merge(dst, local_parts, mkv_exe, log=log)
    secs = max(int(time.monotonic() - t0), 1)
    log.phase_end("ok" if mrc <= 1 else f"FAIL({mrc})")
    parts.append(f"merge {_hms(secs)}")
    return mrc


def _ship_sidecars(side_dir: str, dest_dir: str, res) -> None:
    for name in res.sidecars:
        shutil.move(os.path.join(side_dir, name), os.path.join(dest_dir, name))
    shutil.rmtree(side_dir, ignore_errors=True)


def _fail_quarantine(log: common.RipLog, items: list[Item], issues_root: str,
                     label: str, notes: list[str]) -> None:
    """Pass failed but the encode shipped subless: sources wait in their
    issues/<type>/<Name (Year)> folder with the run's evidence."""
    fails = "; ".join(n for n in notes if n.startswith("FAIL"))
    log.note("subs", f"FAIL subtitle pass: {label} -- {fails}")
    folder = os.path.join(issues_root, "tv" if items[0].is_tv else "movies", items[0].movie)
    ui.fail(f"FAILED (subtitle pass): {label} -- encode shipped subless, "
            f"sources quarantined to {folder}")
    for item in items:
        _quarantine(item.path, item, issues_root, log)


def _title_dir_of(path: str) -> str:
    """The movie's title folder for a staged file: its own dir, or the parent
    when it sits in a d<disc> staging subdir (validation.json lives at the
    title-folder root, beside the dN dirs)."""
    d = os.path.dirname(path)
    if _RX_DDIR.match(os.path.basename(d)):
        d = os.path.dirname(d)
    return d


def run_transcode(args) -> int:
    common.set_verbosity(getattr(args, "verbose", 0))
    vid = os.environ.get("VIDEOS_MOUNT", "/mnt/videos")
    if not os.path.isdir(vid):
        ui.fail(
            f"{vid} is not reachable. The videos share isn't "
            f"mounted -- mount it at {vid} (see README: transcode box), or set VIDEOS_MOUNT.")
        return 1
    keep = args.keep
    retry = args.retry
    # --retry walks the issues folder instead of rip/ staging; the source
    # only leaves issues/ on success (delete, or -k park), a failure rests
    # in place with this run's evidence.
    input_dir = os.path.join(vid, "issues" if retry else "rip")
    lib_dir = os.path.join(vid, "library")
    lossless_root = os.path.join(vid, "lossless")
    issues_root = os.path.join(vid, "issues")  # sub-pass failures park sources here
    # disk, not $TMPDIR: /tmp is tmpfs (RAM) and cannot hold a staged source
    # plus its encode output (the Conan run died at 27% on "Disk quota exceeded")
    tmp_dir = args.workdir or os.path.expanduser("~/.cache/skive")
    os.makedirs(tmp_dir, exist_ok=True)

    hb_exe = _find_on_path("HandBrakeCLI")
    mkv_exe = _find_on_path("mkvmerge")
    ffprobe_exe = _find_on_path("ffprobe")
    seconv_exe = _find_on_path("seconv")
    tess_exe = _find_on_path("tesseract")  # seconv shells out to it
    if not hb_exe or not mkv_exe or not ffprobe_exe or not seconv_exe or not tess_exe:
        return 1

    log = common.RipLog("skive transcode", "handbrake", "-", root=vid)
    # encoder resolution happens once here (vainfo probing per file would be
    # noise); the three _encode_part call sites read args.encoder, so the
    # resolved value is written back onto args
    if args.encoder in ("auto", "vaapi"):
        vcn = _vcn_probe()
        if vcn:
            args.encoder = "vaapi"
            log.note("encode", "encoder: VCN hardware hevc_vaapi CQP 24"
                     + (" (low-power entrypoint)" if vcn["low_power"] else ""))
        elif args.encoder == "vaapi":
            ui.fail("-e vaapi but this box has no VA-API HEVC encode "
                    "entrypoint (vainfo) -- no VCN (BC-250) or the freeworld driver is "
                    "missing (scripts/setup-transcode.sh); use -e auto or -e x265")
            return 1
        else:
            args.encoder = "x265-fast"
            log.note("encode", "no VCN (vainfo) -- auto fell back to x265 (fast preset)")
    items, want_hit = _scan(input_dir, args.prefixes)
    for w in args.prefixes:
        if w not in want_hit:
            ui.warn(f"no staged MKVs match: {w}")
    if not items:
        if args.prefixes:
            ui.warn("no matching MKVs to transcode")
            return 1
        ui.info(f"no MKVs in {input_dir}")
        return 0

    # main-feature fallback per movie = the _tNN group with the most bytes (a
    # split main's .pN halves sum; tie -> lowest number). Extras are never
    # main. A readable validation.json in the title folder overrides below.
    grp: dict[tuple[str, int], int] = {}
    for i in items:
        if i.sfx != "t" or i.is_tv:
            continue
        key = (i.movie, i.num)
        grp[key] = grp.get(key, 0) + i.size
    main_num: dict[str, tuple[int, int]] = {}
    for (movie, num), sz in grp.items():
        best = main_num.get(movie)
        if best is None or sz > best[1] or (sz == best[1] and num < best[0]):
            main_num[movie] = (num, sz)

    # Recorded main (CONTEXT.md: Validation file, map.md decision 5): the doc
    # sits at the title-folder root beside the staged titles. main non-null
    # "tNN" -> that number IS the main; null/absent/corrupt -> bytes fallback
    # stands (legacy folders, shorts). A recorded main with no staged _tNN
    # (hand-edit typo, moved files) skips the movie's main encode loudly —
    # never a silent bytes fallback (that is the Mamma Mia failure mode).
    # The extras marker is read for EVERY movie folder (t-titled or not) so a
    # keep-marked extras-only folder encodes too.
    doc_path: dict[str, str] = {}      # movie -> its validation.json (append + copy)
    extras_mark: dict[str, dict] = {}   # movie -> extras marker (CONTEXT.md: Extras marker)
    rec_missing: dict[str, str] = {}   # movie -> recorded main with no staged title
    for movie in sorted({i.movie for i in items if not i.is_tv}):
        dpath = os.path.join(
            _title_dir_of(next(i.path for i in items if i.movie == movie)),
            VALIDATION_FILENAME)
        doc = load_validation(Path(dpath))
        if doc is None:
            continue
        extras_mark[movie] = read_extras_marker(doc)
        if not any(i.movie == movie and i.sfx == "t" for i in items):
            continue  # extras-only folder: no main arm, nothing more to read
        doc_path[movie] = dpath
        rec = doc.get("main")
        if rec is None:
            continue  # the record says no candidate main existed: bytes fallback
        if not isinstance(rec, str) or not _RX_MAIN.match(rec):
            ui.warn(f"validation: {dpath}: unreadable main {rec!r} -- biggest-bytes fallback")
            continue
        num = int(rec[1:])
        if any(i.num == num for i in items if i.movie == movie and i.sfx == "t"):
            main_num[movie] = (num, grp.get((movie, num), 0))  # the record wins
        else:
            rec_missing[movie] = rec

    # the very vague session estimate (CONTEXT.md: Session cap): ~15–20
    # min per main on the laptop's VCN is a DVD-to-Blu-ray blur, stated
    # once, never per title
    if args.limit:
        n_staged = len(main_num)
        n_run = min(args.limit, n_staged)
        ui.info(f"session: {n_run} main(s) of {n_staged} staged "
                f"(cap {args.limit}) -- expect roughly {n_run} × 15–20 min")

    failed = 0
    parked = 0  # sources actually parked in lossless/ (--keep banner honesty)
    mains_started = 0  # session cap counter (CONTEXT.md: Session cap)
    n_ok = 0
    n_failed = 0
    rows: list[tuple[str, list[str], bool]] = []  # summary-table accumulation
    handled: set[str] = set()
    missing_warned: set[str] = set()  # movies whose no-staged-main note already printed
    # The in-flight item's local temp files (CONTEXT.md: Interrupt): the
    # interrupt path deletes exactly these — the local copy and the encode
    # partial — while the SOURCE stays in staging (sources only move on
    # success). Reset per item; stale entries after a finished item are
    # harmless (the files are already gone and _rmf tolerates that).
    inflight: list[str] = []
    inflight_side: str | None = None  # this item's sidecar temp dir, once the pass starts
    def _item_done(label: str, parts: list[str], ok: bool) -> None:
        """Per-item console summary line; keeps the batch ok/failed counts
        and one row for the end-of-run summary table."""
        nonlocal n_ok, n_failed
        rows.append((label, list(parts), ok))
        if ok:
            n_ok += 1
            ui.info(f"{label}: {' + '.join(parts)} -- ok")
        else:
            n_failed += 1
            ui.fail(f"{label}: {' + '.join(parts)} -- FAILED")

    # Prefetch lookahead (slice 03): while one unit encodes, the next unit's
    # source files pull into the workdir in one background thread. The main
    # thread owns RipLog (no lock) — only it logs; pf_files registers every
    # prefetch artifact at thread START so the interrupt path drops partial
    # and complete-but-unconsumed copies alike (CONTEXT.md: Interrupt).
    units = _units(items, main_num, rec_missing, extras_mark, args.limit)
    unit_of = {x.path: n for n, u in enumerate(units) for x in u}
    pf_files: list[str] = []
    pf_ready: dict[str, str] = {}  # staged src -> local dst, filled on completion
    pf_started: set[int] = set()
    pf_thread: threading.Thread | None = None
    pf_cancel = threading.Event()  # set by the interrupt arm so _copy2 stops mid-file

    def _pf_join() -> None:
        nonlocal pf_thread
        if pf_thread is not None:
            pf_thread.join()
            pf_thread = None

    def _pf_take(src, log, parts):
        """Consume a prefetched local copy; join a still-running thread first
        (the encode finished early — the natural sequential degradation).
        Returns the dst, or None to copy sequentially as today."""
        _pf_join()
        dst = pf_ready.pop(src, None)
        if dst is None:
            return None
        log.phase("copy")
        log.phase_end("prefetched — overlapped with the previous encode")
        parts.append("copy prefetched")
        return dst

    def _pf_start(idx, log):
        """Start copying unit idx+1's sources into the workdir in ONE daemon
        thread, right before unit idx's encode — the note's timestamp inside
        the encode window is the overlap evidence. Space guard: no headroom
        for the next unit's bytes plus this unit's remaining needs (its own
        source bytes; encodes land smaller, so this overestimates safely)
        skips THIS prefetch with one loud note — the unit copies sequentially
        later."""
        nonlocal pf_thread
        nxt = idx + 1
        if nxt >= len(units) or nxt in pf_started:
            return
        pf_started.add(nxt)
        nxt_unit, cur = units[nxt], units[idx]
        names = " ".join(x.name for x in nxt_unit)
        free = shutil.disk_usage(tmp_dir).free
        need = sum(x.size for x in nxt_unit) + sum(x.size for x in cur)
        if free < need:
            msg = (f"workdir short {need - free} bytes of headroom -- no prefetch, "
                   f"{names} copies sequentially later")
            ui.warn(msg)
            log.note("prefetch", msg)
            return
        log.note("prefetch", f"copying {names} for {nxt_unit[0].movie} in the background")
        for x in nxt_unit:
            pf_files.append(os.path.join(tmp_dir, x.name))  # at thread START, before any bytes

        def work():
            try:
                for x in nxt_unit:
                    if pf_cancel.is_set():
                        return
                    dst = os.path.join(tmp_dir, x.name)
                    _copy2(x.path, dst, pf_cancel, bg="copy")
                    pf_ready[x.path] = dst
            finally:
                v = ui.view()
                if v is not None:
                    v.bg_clear("copy")

        pf_thread = threading.Thread(target=work, daemon=True)
        pf_thread.start()

    def _pf_drop(paths) -> None:
        """Drop prefetched-but-unconsumed copies (guard quarantine etc.)."""
        _pf_join()
        for p in paths:
            _rmf(p)

    view = ui.view() or ui.RunView()  # a test may pre-set a tty/live view
    ui.set_view(view)
    global _POS
    log.start()
    try:
        for i in items:
            if i.path in handled:
                continue
            u = unit_of.get(i.path)  # not every staged item is an encode unit
            if u is not None:
                _POS = f"item {u + 1} of {len(units)}"
            if view.tty:
                view.status(_POS, i.movie)
            inflight = []
            inflight_side = None
            log.name = i.movie
            safe = common.sanitize(i.movie)

            # -- TV: no main feature, no merge. Every staged episode encodes
            # -- (a double stays one file), episodes -> Season NN (00 =
            # -- Specials) with the inline subtitle pass and the show doc
            # -- copied beside the encodes, TV extras -> the show's extras/
            # -- with embedded subs kept and no pass.
            if i.is_tv:
                show_dir = os.path.join(lib_dir, "tv", safe)
                episode = i.sfx == "S"
                if episode:
                    season = "Specials" if int(i.sn) == 0 else f"Season {i.sn}"
                    dest_dir = os.path.join(show_dir, season)
                    dest = os.path.join(dest_dir, f"{safe} - S{i.sn}E{i.en}{i.tail}.mkv")
                else:
                    ext = f".p{i.part}" if i.part >= 0 else ""
                    dest_dir = os.path.join(show_dir, "extras")
                    dest = os.path.join(dest_dir, _extra_name(dest_dir, safe, i.num, ext))
                parts: list[str] = []
                os.makedirs(dest_dir, exist_ok=True)
                # the local copy is registered in `inflight` AS CREATED so an
                # interrupt during the copy window drops the partial too
                # (CONTEXT.md: Interrupt)
                cleanup: list[str] = []
                local_src = os.path.join(tmp_dir, i.name)
                pf = _pf_take(i.path, log, parts)
                inflight.append(local_src)  # BEFORE the copy (or the prefetched file's ownership move)
                if pf is None:
                    _copy_in(i.path, local_src, log, parts)
                cleanup.append(local_src)
                side_dir = res = None  # extras: no pass, embedded subs stay
                if episode:
                    # the sidecar dir is created up front and registered as
                    # this item's temp BEFORE the pass runs, so an interrupt
                    # mid-pass drops it too (CONTEXT.md: Interrupt); the
                    # sidecar base name carries the episode label, not the
                    # bare show name
                    side_dir = tempfile.mkdtemp(dir=tmp_dir, prefix="subs-")
                    inflight_side = side_dir
                    side_dir, res = _subs_part(local_src, side_dir,
                                               f"{safe} - S{i.sn}E{i.en}{i.tail}",
                                               seconv_exe, ffprobe_exe, log, parts)
                local_out = os.path.join(tmp_dir, i.base + ".h265.mkv")
                inflight = cleanup + [local_out]
                crop = _detect_part(local_src, ffprobe_exe, log, parts)[0]
                _pf_start(unit_of[i.path], log)
                if _encode_part(local_src, local_out, args.encoder, hb_exe,
                                ffprobe_exe, no_subs=episode, log=log, parts=parts,
                                crop=crop):
                    shutil.move(local_out, dest)
                    if side_dir is not None:
                        _ship_sidecars(side_dir, dest_dir, res)
                    if episode:
                        _copy_show_doc(i.path, dest_dir)  # the doc travels beside its encodes
                    for p in cleanup:
                        _rmf(p)
                    if res is None or res.ok:
                        if _archive(i.path, i, lossless_root, keep):
                            parked += 1
                    else:
                        _fail_quarantine(log, [i], issues_root, i.name, res.notes)
                        failed = 1
                    handled.add(i.path)
                    _item_done(i.name, parts, res is None or res.ok)
                else:
                    if side_dir is not None:
                        shutil.rmtree(side_dir, ignore_errors=True)
                    for p in cleanup + [local_out]:
                        _rmf(p)
                    log.note("encode", f"FAILED handbrake: {i.name} -- source left in place")
                    handled.add(i.path)
                    failed = 1
                    _item_done(i.name, parts, False)
                continue

            # -- recorded main with no staged title: loud skip, never a
            # silent bytes fallback. This movie's _t titles stay in staging
            # for a human (encoding the byte-winner would be the Mamma Mia
            # failure mode).
            if i.sfx == "t" and i.movie in rec_missing:
                if i.movie not in missing_warned:
                    missing_warned.add(i.movie)
                    msg = (f"{i.movie}: recorded main {rec_missing[i.movie]} has no "
                           f"staged title -- main encode skipped, staged titles "
                           f"left for a human")
                    log.note("validation", msg)
                continue

            # -- main feature: merge its .pN parts (if any), encode as one
            # -- movie.
            if i.sfx == "t" and i.movie in main_num and main_num[i.movie][0] == i.num:
                # session cap (CONTEXT.md: Session cap): a workday run takes a
                # predictable slice; the rest stays in staging for next run
                if args.limit and mains_started >= args.limit:
                    remaining = sum(1 for x in items
                                    if x.sfx == "t" and not x.is_tv and x.path not in handled
                                    and x.movie in main_num
                                    and main_num[x.movie][0] == x.num)
                    ui.info(f"deferred (session cap {args.limit}): {i.movie} -- "
                            f"{remaining} main(s) left for the next run")
                    continue
                mains_started += 1
                mark_main_include = extras_mark.get(
                    i.movie, {"include": False, "quality": "superfast"})["include"]
                # part order: no-suffix first (-1 -- rescues a forgotten
                # --part on disc 1), then .pN ascending.
                siblings = sorted(
                    (s for s in items if s.movie == i.movie and s.sfx == "t" and s.num == i.num),
                    key=lambda s: s.part,
                )
                dest_dir = os.path.join(lib_dir, "movies", safe)
                dest = os.path.join(dest_dir, safe + ".mkv")
                parts: list[str] = []
                # cleanup removes this item's temps AFTER the encode; every
                # entry is also registered in `inflight` AS CREATED so an
                # interrupt during the copy/merge window drops it too
                # (CONTEXT.md: Interrupt)
                cleanup: list[str] = []
                if len(siblings) > 1:
                    # track guard: a mismatched part set never merges or
                    # encodes -- all parts quarantine whole for a human
                    bad = _split_mismatch(siblings, ffprobe_exe)
                    if bad:
                        log.note("guard", f"FAIL tracks differ: {bad} -- all parts to issues/movies")
                        for s in siblings:
                            _quarantine(s.path, s, issues_root, log)
                            handled.add(s.path)
                        # its prefetched copies (if the lookahead got here
                        # first) are unconsumed now -- drop, never leak
                        _pf_drop(os.path.join(tmp_dir, s.name) for s in siblings)
                        failed = 1
                        _item_done(i.movie, parts, False)
                        continue
                    os.makedirs(dest_dir, exist_ok=True)
                    local_parts = []
                    for s in siblings:
                        lb = os.path.join(tmp_dir, s.name)
                        pf = _pf_take(s.path, log, parts)
                        inflight.append(lb)  # BEFORE the copy: mid-copy interrupt drops the partial
                        if pf is None:
                            _copy_in(s.path, lb, log, parts)
                        local_parts.append(lb)
                        cleanup.append(lb)
                    local_src = os.path.join(tmp_dir, f"{safe}_t{i.num_str}.merged.mkv")
                    inflight.append(local_src)  # BEFORE the merge: mid-merge interrupt drops the partial
                    mrc = _merge_part(local_src, local_parts, mkv_exe, log, parts)
                    if mrc <= 1:  # 0 ok, 1 warning (still usable)
                        cleanup.append(local_src)
                    else:  # 2+ failure: sources left in place, run continues
                        names = " ".join(s.name for s in siblings)
                        log.note("encode", f"FAILED mkvmerge ({mrc}): {names} -- sources left in place")
                        for p in cleanup:
                            _rmf(p)
                        for s in siblings:
                            handled.add(s.path)
                        failed = 1
                        _item_done(i.movie, parts, False)
                        continue
                else:
                    os.makedirs(dest_dir, exist_ok=True)
                    local_src = os.path.join(tmp_dir, i.name)
                    pf = _pf_take(i.path, log, parts)
                    inflight.append(local_src)  # BEFORE the copy (or the prefetched file's ownership move)
                    if pf is None:
                        _copy_in(i.path, local_src, log, parts)
                    cleanup.append(local_src)

                # the inline subtitle pass runs on the merged (or single) local
                # copy BEFORE the encode (the main encodes zero-track
                # meanwhile); the sidecar dir is this item's temp from
                # creation, so an interrupt mid-pass drops it too (CONTEXT.md:
                # Interrupt)
                side_dir = tempfile.mkdtemp(dir=tmp_dir, prefix="subs-")
                inflight_side = side_dir
                side_dir, res = _subs_part(local_src, side_dir, safe,
                                           seconv_exe, ffprobe_exe, log, parts)
                local_out = os.path.join(tmp_dir, f"{safe}_t{i.num_str}.h265.mkv")
                inflight = cleanup + [local_out]
                crop = _detect_part(local_src, ffprobe_exe, log, parts)[0]
                _pf_start(unit_of[i.path], log)
                if _encode_part(local_src, local_out, args.encoder, hb_exe,
                                ffprobe_exe, no_subs=True, log=log, parts=parts,
                                crop=crop):
                    shutil.move(local_out, dest)
                    _ship_sidecars(side_dir, dest_dir, res)
                    # the doc that picked this main gets the encode appended
                    # (append-once) and a library copy beside the encode
                    if i.movie in doc_path:
                        _record_encode(doc_path[i.movie], dest, i.num, ffprobe_exe,
                                       crop=crop)
                    for p in cleanup:
                        _rmf(p)
                    # nothing is deleted until its sidecars are verified: the
                    # sources only move after the ship — a failed pass leaves
                    # them parked in issues/ with the run's evidence (the
                    # encode stays shipped subless; the batch continues, the
                    # run fails)
                    names = " ".join(s.name for s in siblings)
                    if res.ok:
                        for s in siblings:
                            if _archive(s.path, s, lossless_root, keep):
                                parked += 1
                        # unmarked extras ride the main's disposal (CONTEXT.md:
                        # Extras marker): the marker that shipped with this
                        # movie decided they never encode
                        if not mark_main_include:
                            for x in items:
                                if (x.is_tv or x.movie != i.movie
                                        or (x.sfx == "t" and x.num == i.num)
                                        or not os.path.exists(x.path)):
                                    continue
                                if _archive(x.path, x, lossless_root, keep):
                                    parked += 1
                                handled.add(x.path)
                                ui.info(f"disposed unmarked extra with main: {x.name}")
                    else:
                        _fail_quarantine(log, siblings, issues_root, names, res.notes)
                    for s in siblings:
                        handled.add(s.path)
                    if not res.ok:
                        failed = 1
                    _item_done(names, parts, res.ok)
                else:
                    shutil.rmtree(side_dir, ignore_errors=True)
                    for p in cleanup + [local_out]:
                        _rmf(p)
                    log.note("encode", f"FAILED handbrake: {i.name} -- sources left in place")
                    if args.retry:
                        # unlike the sub-pass/guard arms this arm never moves
                        # the source — only a --retry leaves it resting in its
                        # issues folder, and then this run's evidence lands
                        # beside it like every other quarantine
                        _copy_run_evidence(
                            log, os.path.join(issues_root, "tv" if i.is_tv else "movies", i.movie))
                    for s in siblings:
                        handled.add(s.path)
                    failed = 1
                    _item_done(names if len(siblings) > 1 else i.name, parts, False)
                continue

            # -- extras (non-main _t title, or an extras-disc title): the
            # ` - Extra NN` form, number rolled forward past taken names —
            # no staging label ever reaches the library. Every embedded track
            # stays: no subtitle pass, no zero-subs trick. The extras marker
            # (CONTEXT.md: Extras marker) gates the whole branch: unmarked
            # extras never encode — they ride the main's disposal (delete, or
            # -k park) once it ships; a folder whose movie has no staged main
            # rides alone here. Marked extras encode at quality superfast
            # (default) or full (the main's preset).
            mark = extras_mark.get(i.movie, {"include": False, "quality": "superfast"})
            if not mark["include"]:
                handled.add(i.path)
                if i.movie in main_num or i.movie in rec_missing:
                    ui.info(f"skip (unmarked extra -- rides the main's disposal): {i.name}")
                else:
                    if _archive(i.path, i, lossless_root, keep):
                        parked += 1
                    ui.info(f"skip (unmarked extra -- no staged main, disposed): {i.name}")
                continue
            speed = None if mark["quality"] == "full" else "superfast"
            ext = f".p{i.part}" if i.part >= 0 else ""
            dest_dir = os.path.join(lib_dir, "movies", safe, "extras")
            dest = os.path.join(dest_dir, _extra_name(dest_dir, safe, i.num, ext))
            parts: list[str] = []
            os.makedirs(dest_dir, exist_ok=True)
            cleanup: list[str] = []
            local_src = os.path.join(tmp_dir, i.name)
            pf = _pf_take(i.path, log, parts)
            inflight.append(local_src)  # BEFORE the copy (or the prefetched file's ownership move)
            if pf is None:
                _copy_in(i.path, local_src, log, parts)
            cleanup.append(local_src)
            local_out = os.path.join(tmp_dir, i.base + ".h265.mkv")
            inflight = cleanup + [local_out]
            crop = _detect_part(local_src, ffprobe_exe, log, parts)[0]
            _pf_start(unit_of[i.path], log)
            if _encode_part(local_src, local_out, args.encoder, hb_exe,
                            ffprobe_exe, no_subs=False, log=log, parts=parts,
                            speed=speed, crop=crop):
                shutil.move(local_out, dest)
                for p in cleanup:
                    _rmf(p)
                if _archive(i.path, i, lossless_root, keep):
                    parked += 1
                handled.add(i.path)
                _item_done(i.name, parts, True)
            else:
                for p in cleanup + [local_out]:
                    _rmf(p)
                log.note("encode", f"FAILED handbrake: {i.name} -- source left in place")
                handled.add(i.path)
                failed = 1
                _item_done(i.name, parts, False)

        # leftover sweep (slice 03): prefetched files for units the live loop
        # never consumed (cap deferral, guard quarantine, normal end) drop
        # here -- never a leak, never a crash
        _pf_join()
        for p in pf_files:
            _rmf(p)
    except KeyboardInterrupt:
        # CONTEXT.md Interrupt: drop this item's partial temp output (the local
        # copy, the unshipped sidecar dir, and .h265 encode live in the work
        # dir), leave the SOURCE in staging — sources only move on success —
        # record status=interrupted, exit 130.
        log.fail_open_phase("interrupted")
        view.close()  # before any print, so the Live region never garbles them
        for p in inflight:
            _rmf(p)
        pf_cancel.set()
        _pf_join()
        for p in pf_files:  # prefetch partials and unconsumed copies drop too
            _rmf(p)
        if inflight_side is not None:
            shutil.rmtree(inflight_side, ignore_errors=True)
        log.interrupted()
        ui.fail("interrupted -- partial temp output deleted, source left in staging")
        return 130
    except Exception as e:
        failed = 1
        ui.fail(f"FATAL: {e}")
        log.note("run", f"FATAL: {e}")
        log.fail_open_phase("FAIL")
    finally:
        view.close()
        ui.clear_view()
        log.finish(failed)

    ui.run_summary(rows, n_ok, n_failed,
                   f"--keep: lossless sources moved to {lossless_root} -- verify, "
                   f"then clean by hand" if keep and parked else None,
                   replay=view.replay_lines())
    return failed
