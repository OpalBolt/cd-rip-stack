#!/usr/bin/env python3
"""Standalone Windows transcode — batch re-encode rip\\ staging into the Jellyfin library.

Port of windows/transcode-video.ps1 to stdlib-only Python (3.10+; runs on a
clean `python.exe`, no rich, no skive imports). The whole Windows install spec
is: winget Python 3, HandBrake (CLI reachable), MKVToolNix, ffmpeg (ffprobe),
and the share mapped (default `Z:\\`, `$env:VIDEOS` overrides).

The staging subdirectory decides the type: anything under `rip\\{TV,tv}` is TV
(no main-feature pick, no merging); everything else is a movie. Video is
re-encoded, audio copied. Main features and TV episodes ship with ZERO subtitle
tracks (the inline subtitle pass extracts them to SRT sidecars beside the
encode); extras keep every track embedded.

Usage:  python transcode.py [-k] [-e x265|nvenc|qsv|vce] [-w DIR] [-v|-vv] [prefix...]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import subs  # beside this script; a script's dir is on sys.path when run directly

# Staged names: <Movie (Year)>_tNN[.pN].mkv (titles), <Movie (Year)> - Extra NN[.pN].mkv
# (extras disc), <Show (Year)>_SNNENN[-ENN].mkv (TV, incl. doubles).
_RX_TITLE = re.compile(
    r"^(?P<movie>.+)_(?P<sfx>[te])(?P<num>\d+)(?P<part>\.p(?P<pn>\d+))?\.mkv$"
)
_RX_EXTRA = re.compile(
    r"^(?P<movie>.+) - Extra (?P<num>\d+)(?P<part>\.p(?P<pn>\d+))?\.mkv$"
)
_RX_EP = re.compile(
    r"^(?P<movie>.+)_S(?P<sn>\d{2})E(?P<en>\d{2})(?P<tail>-E\d{2})?\.mkv$"
)
_TV_ROOTS = ("TV", "tv")  # rip\TV (legacy ps1 staging) and rip\tv (SPEC layout)
_HB = "HandBrakeCLI.exe"
_MKV = "mkvmerge.exe"
_FFPROBE = "ffprobe.exe"
# one comma-joined argument: stays a single argv entry
_COPY_MASK = "aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac,pcm"


# sanitize/RipLog duplicate skive/common.py on purpose: standalone Windows file, no skive import.
def sanitize(s: str) -> str:
    """Strip filesystem-hostile chars; keeps ( ) for "Title (Year)"."""
    return re.sub(r'[/:*?"<>|]', "", s)


# Console verbosity: 0 = transcode's own lines only, 1 = -v adds HandBrake
# error lines, 2 = -vv raw passthrough. Duplicate of skive/common.py's
# VERBOSITY on purpose (standalone file); main()/run_transcode set it once.
# The raw log always captures everything regardless of level.
VERBOSITY = 0


def set_verbosity(level: int) -> None:
    """Store the console verbosity, clamped to 0/1/2 (absent/-v/-vv)."""
    global VERBOSITY
    VERBOSITY = max(0, min(int(level or 0), 2))


def _append_jsonl(path: str, obj: dict) -> None:
    """Append one object as a JSONL line. Open-append-close per line — the same
    append/flush discipline as the Linux side — so lines survive concurrent writers."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


class RipLog:
    """The two JSONL logs, same shapes as skive/common.py, written via the share
    root (VIDEOS, default Z:\\).

    Run log  <root>\\logs\\rip.jsonl — one
    {ts, run, script, software, name, phase, status, elapsed_s} object per line.
    Raw log  <root>\\logs\\raw\\<run>.jsonl — one {ts, run, tool, line} object
    per tool output line; finish(0) deletes it, failure or interrupt keeps it.
    """

    SCRIPT = "transcode.py"
    SOFT = "handbrake"

    def __init__(self, root: str | None = None, now=None):
        self._now = now or datetime.now
        # short UUID minted per invocation; names this run's raw log
        self.run = uuid.uuid4().hex[:8]
        vid = root if root is not None else (os.environ.get("VIDEOS") or "Z:\\")
        self._run_log = os.path.join(vid, "logs", "rip.jsonl")
        self._raw_log = os.path.join(vid, "logs", "raw", self.run + ".jsonl")
        self.name = "-"
        self._phases = 0

    def _emit(self, now, phase: str, status: str, elapsed_s: int) -> None:
        _append_jsonl(self._run_log, {
            "ts": now.isoformat(timespec="seconds"),
            "run": self.run,
            "script": self.SCRIPT,
            "software": self.SOFT,
            "name": self.name,
            "phase": phase,
            "status": status,
            "elapsed_s": elapsed_s,
        })

    def raw(self, tool: str, line: str) -> None:
        """One verbatim tool output line into this run's raw log."""
        _append_jsonl(self._raw_log, {
            "ts": self._now().isoformat(timespec="seconds"),
            "run": self.run,
            "tool": tool,
            "line": line,
        })

    def start(self) -> None:
        now = self._now()
        self._t0 = now
        self._emit(now, "-", "started", 0)

    def phase(self, name: str) -> None:
        self._phase_name = name
        self._phase_t0 = self._now()

    def phase_end(self) -> None:
        now = self._now()
        self._phases += 1
        self._emit(now, self._phase_name, "ok",
                   int((now - self._phase_t0).total_seconds()))

    def note(self, phase: str, status: str) -> None:
        """Untimed detail line (elapsed_s 0); mirrors Linux RipLog.note."""
        self._emit(self._now(), phase, status, 0)

    def finish(self, rc: int) -> None:
        # always write the total so a successful run ends with a clear summary
        # line (the README contract is "a total on exit").
        now = self._now()
        status = "ok" if rc == 0 else f"FAIL({rc})"
        self._emit(now, "total", status, int((now - self._t0).total_seconds()))
        # raw log is deleted on a clean exit; kept on failure or interrupt
        if rc == 0:
            try:
                os.remove(self._raw_log)
            except OSError:
                pass


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


def _find_tool(exe: str, fallback_rel: str) -> str | None:
    """HandBrakeCLI.exe / mkvmerge.exe: PATH first, then %ProgramFiles%."""
    found = shutil.which(exe)
    if found:
        return found
    pf = os.environ.get("ProgramFiles")
    if pf and os.path.isfile(os.path.join(pf, fallback_rel)):
        return os.path.join(pf, fallback_rel)
    print(f"{exe} not found on PATH nor at %ProgramFiles%\\{fallback_rel} -- see README (Installing the tools).",
          file=sys.stderr)
    return None


def _find_on_path(exe: str) -> str | None:
    """seconv/tesseract: PATH only (README install spec puts them there)."""
    found = shutil.which(exe)
    if not found:
        print(f"{exe} not found on PATH -- see README (Windows transcode box: Install)",
              file=sys.stderr)
    return found


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


def _scan(rip_dir: str, want: list[str]) -> tuple[list[Item], set[str]]:
    """Walk rip\\ for .mkv files; return parsed items + which prefixes hit."""
    items: list[Item] = []
    want_hit: set[str] = set()
    root = Path(rip_dir)
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
            print(f"skip (junk label -- mid-rip or failed rename): {f}", file=sys.stderr)
            continue
        # junk guard: no "(YYYY)" tail = rip in progress or failed rename
        if not re.search(r"\(\d{4}\)$", movie):
            print(f"skip (junk label -- mid-rip or failed rename): {f}", file=sys.stderr)
            continue
        if want:
            want_s = {sanitize(w): w for w in want}
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


def _encode(src: str, out: str, encoder: str, hb_exe: str, ffprobe_exe: str,
            no_subs: bool = False, log: RipLog | None = None) -> bool:
    """HandBrake pass-through re-encode at native resolution. GPU encoders override the encoder.
    no_subs selects zero subtitle tracks (features/TV); extras keep today's pass-through.
    Every HandBrake output line is written verbatim to the run's raw log (log.raw),
    the same tee discipline as skive/makemkv.py, while the console sees it gated
    by the verbosity tiers: quiet at 0, HandBrake error lines at -v, everything
    at -vv; anything the level hid is replayed verbatim when the encode fails.
    HandBrakeCLI's exact line shapes were never observed on this box -- errors
    are gated by its "ERROR" line prefix; verify on the first real transcode."""
    height = _probe_height(src, ffprobe_exe)
    cmd = [hb_exe, "-i", src, "-o", out]
    if encoder != "x265":
        cmd += ["--encoder", f"{encoder}_h265", "--quality", "28"]
    cmd += ["--preset", _preset(height) if height else "H.265 MKV 1080p30"]
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
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    hidden: list[str] = []  # console-suppressed lines, replayed on failure
    for line in proc.stdout:
        stripped = line.rstrip()
        if VERBOSITY >= 2 or (VERBOSITY >= 1 and stripped.startswith("ERROR")):
            print(line, end="", flush=True)
        elif stripped:
            hidden.append(stripped)
        if log is not None:
            log.raw("handbrake", stripped)
    rc = proc.wait()
    if rc != 0:
        for line in hidden:
            print(line, flush=True)
    return rc == 0


def _rmf(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _copy2(src: str, dst: str) -> None:
    """copy2 (mode + mtime preserved) with a progress line every 10%."""
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
            fout.write(buf)
            done += len(buf)
            pct = done * 100 // total
            if pct >= last + 10 or pct == 100:
                print(f"  {pct}%  ({done // mb}/{total // mb} MB)")
                last = pct
    shutil.copystat(src, dst)


def _archive(src: str, done_dir: str, rip_dir: str, keep: bool) -> None:
    """-Keep parks the lossless source in rip-DONE (never clobbering); else delete."""
    if not keep:
        _rmf(src)
        return
    d = os.path.join(done_dir, os.path.basename(src))
    if os.path.exists(d):
        print(f"--keep: {d} already exists -- source left in {rip_dir}", file=sys.stderr)
    else:
        shutil.move(src, d)


def _quarantine(src: str, issues_dir: str) -> None:
    """Sources that must not be lost wait in rip-ISSUES (never clobbering)."""
    os.makedirs(issues_dir, exist_ok=True)
    d = os.path.join(issues_dir, os.path.basename(src))
    if os.path.exists(d):
        print(f"quarantine: {d} already exists -- source left in place", file=sys.stderr)
    else:
        shutil.move(src, d)


def _sub_pass(local_src: str, work_dir: str, base: str, seconv_exe: str,
              ffprobe_exe: str, log: RipLog | None = None):
    """Inline subtitle pass on the local copy/merge; sidecars land in a fresh
    dir under the work dir and only move beside the encode once it succeeds.
    Captured ffprobe/seconv output lines go to the run's raw log via log.raw
    (and pass through to the console at -vv). Returns (sidecar dir, PassResult)."""
    side_dir = tempfile.mkdtemp(dir=work_dir, prefix="subs-")
    res = subs.run_pass(local_src, side_dir, base, seconv_exe, ffprobe_exe,
                        log=log, verbose=VERBOSITY)
    for n in res.notes:
        print(n)
    return side_dir, res


def _ship_sidecars(side_dir: str, dest_dir: str, res) -> None:
    for name in res.sidecars:
        shutil.move(os.path.join(side_dir, name), os.path.join(dest_dir, name))
    shutil.rmtree(side_dir, ignore_errors=True)


def _fail_quarantine(log: RipLog, paths: list[str], issues_dir: str,
                     label: str, notes: list[str]) -> None:
    """Pass failed but the encode shipped subless: sources wait in rip-ISSUES."""
    fails = "; ".join(n for n in notes if n.startswith("FAIL"))
    log.note("subs", f"FAIL subtitle pass: {label} -- {fails}")
    print(f"FAILED (subtitle pass): {label} -- encode shipped subless, "
          f"sources quarantined to {issues_dir}", file=sys.stderr)
    for p in paths:
        _quarantine(p, issues_dir)


def run_transcode(args) -> int:
    set_verbosity(getattr(args, "verbose", 0))
    vid = os.environ.get("VIDEOS") or "Z:\\"
    if not os.path.isdir(vid):
        print(
            f"transcode.py: {vid} is not reachable. An SSH session doesn't inherit desktop "
            f"drive mappings -- run 'net use Z: \\\\nas\\\\videos' first, or set $VIDEOS to the UNC path.",
            file=sys.stderr,
        )
        return 1
    rip_dir = os.path.join(vid, "rip")
    lib_dir = os.path.join(vid, "library")
    done_dir = os.path.join(vid, "rip-DONE")
    issues_dir = os.path.join(vid, "rip-ISSUES")
    tmp_dir = args.workdir or os.environ.get("VIDEO_WORK_DIR") or os.path.join(tempfile.gettempdir(), "video-work")
    os.makedirs(tmp_dir, exist_ok=True)

    hb_exe = _find_tool(_HB, os.path.join("HandBrake", _HB))
    mkv_exe = _find_tool(_MKV, os.path.join("MKVToolNix", _MKV))
    ffprobe_exe = _find_tool(_FFPROBE, os.path.join("ffmpeg", "bin", _FFPROBE))
    seconv_exe = _find_on_path("seconv.exe")
    tess_exe = _find_on_path("tesseract.exe")  # seconv shells out to it
    if not hb_exe or not mkv_exe or not ffprobe_exe or not seconv_exe or not tess_exe:
        return 1
    if args.keep:
        os.makedirs(done_dir, exist_ok=True)

    log = RipLog()
    items, want_hit = _scan(rip_dir, args.prefixes)
    for w in args.prefixes:
        if w not in want_hit:
            print(f"no staged MKVs match: {w}", file=sys.stderr)
    if not items:
        if args.prefixes:
            print("no matching MKVs to transcode", file=sys.stderr)
            return 1
        print(f"no MKVs in {rip_dir}")
        return 0

    # main feature per movie = the _tNN group with the most bytes (a split
    # main's .pN halves sum; tie -> lowest number). Extras are never main.
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

    failed = 0
    handled: set[str] = set()
    log.start()
    try:
        for i in items:
            if i.path in handled:
                continue
            log.name = i.movie
            safe = sanitize(i.movie)

            # -- TV: no main feature, no merge. episodes -> Season NN (00 =
            # -- Specials), extras -> the show's extras.
            if i.is_tv:
                show_dir = os.path.join(lib_dir, "TV", safe)
                episode = i.sfx == "S"
                if episode:
                    season = "Specials" if int(i.sn) == 0 else f"Season {i.sn}"
                    dest_dir = os.path.join(show_dir, season)
                    dest = os.path.join(dest_dir, f"{safe} - S{i.sn}E{i.en}{i.tail}.mkv")
                else:
                    ext = f".p{i.part}" if i.part >= 0 else ""
                    dest_dir = os.path.join(show_dir, "extras")
                    name = (f"{safe} - Extra {i.num_str}{ext}.mkv"
                            if i.sfx == "e" else f"{safe}_{i.sfx}{i.num_str}{ext}.mkv")
                    dest = os.path.join(dest_dir, name)
                os.makedirs(dest_dir, exist_ok=True)
                local_src = os.path.join(tmp_dir, i.name)
                local_out = os.path.join(tmp_dir, i.base + ".h265.mkv")
                log.phase("encode")
                print(f"copying {i.name} to local temp...")
                _copy2(i.path, local_src)
                side_dir = res = None  # extras: no pass, embedded subs stay
                if episode:
                    side_dir, res = _sub_pass(
                        local_src, tmp_dir, f"{safe} - S{i.sn}E{i.en}{i.tail}",
                        seconv_exe, ffprobe_exe, log=log)
                if _encode(local_src, local_out, args.encoder, hb_exe, ffprobe_exe,
                           no_subs=episode, log=log):
                    shutil.move(local_out, dest)
                    if side_dir is not None:
                        _ship_sidecars(side_dir, dest_dir, res)
                    _rmf(local_src)
                    if res is None or res.ok:
                        _archive(i.path, done_dir, rip_dir, args.keep)
                    else:
                        _fail_quarantine(log, [i.path], os.path.join(issues_dir, "tv"),
                                         i.name, res.notes)
                        failed = 1
                    log.phase_end()
                else:
                    if side_dir is not None:
                        shutil.rmtree(side_dir, ignore_errors=True)
                    _rmf(local_src)
                    _rmf(local_out)
                    log.note("encode", f"FAILED handbrake: {i.name} -- source left in place")
                    print(f"FAILED (handbrake): {i.name} -- source left in place", file=sys.stderr)
                    failed = 1
                    log.phase_end()
                continue

            # -- main feature: merge its .pN parts (if any), encode as one movie.
            if i.sfx == "t" and i.movie in main_num and main_num[i.movie][0] == i.num:
                # part order: no-suffix first (-1 -- rescues a forgotten --part
                # on disc 1), then .pN ascending.
                siblings = sorted(
                    (s for s in items if s.movie == i.movie and s.sfx == "t" and s.num == i.num),
                    key=lambda s: s.part,
                )
                dest_dir = os.path.join(lib_dir, safe)
                dest = os.path.join(dest_dir, safe + ".mkv")
                log.phase("encode")
                cleanup: list[str] = []
                if len(siblings) > 1:
                    # track guard: a mismatched part set never merges or encodes
                    bad = _split_mismatch(siblings, ffprobe_exe)
                    if bad:
                        log.note("guard", f"FAIL tracks differ: {bad} -- all parts to rip-ISSUES/movies")
                        print(f"FAILED (track guard): {bad} -- parts quarantined", file=sys.stderr)
                        for s in siblings:
                            _quarantine(s.path, os.path.join(issues_dir, "movies"))
                            handled.add(s.path)
                        failed = 1
                        log.phase_end()
                        continue
                    os.makedirs(dest_dir, exist_ok=True)
                    local_parts = []
                    for s in siblings:
                        lb = os.path.join(tmp_dir, s.name)
                        print(f"copying {s.name} to local temp...")
                        _copy2(s.path, lb)
                        local_parts.append(lb)
                        cleanup.append(lb)
                    local_src = os.path.join(tmp_dir, f"{safe}_t{i.num_str}.merged.mkv")
                    margs = [local_parts[0]]
                    for j in range(1, len(local_parts)):
                        margs += ["+", local_parts[j]]
                    mrc = subprocess.run([mkv_exe, "-o", local_src] + margs).returncode
                    if mrc <= 1:  # 0 ok, 1 warning (still usable)
                        cleanup.append(local_src)
                    else:  # 2+ failure: sources left, run continues
                        names = " ".join(s.name for s in siblings)
                        log.note("encode", f"FAILED mkvmerge ({mrc}): {names} -- sources left in place")
                        print(f"FAILED mkvmerge ({mrc}): {names} -- sources left in place "
                              f"(see mkvmerge output above)", file=sys.stderr)
                        for p in cleanup:
                            _rmf(p)
                        for s in siblings:
                            handled.add(s.path)
                        failed = 1
                        log.phase_end()
                        continue
                else:
                    os.makedirs(dest_dir, exist_ok=True)
                    local_src = os.path.join(tmp_dir, i.name)
                    print(f"copying {i.name} to local temp...")
                    _copy2(i.path, local_src)
                    cleanup.append(local_src)

                # pass runs on the merged lossless file, never per part
                side_dir, res = _sub_pass(local_src, tmp_dir, safe, seconv_exe, ffprobe_exe, log=log)
                local_out = os.path.join(tmp_dir, f"{safe}_t{i.num_str}.h265.mkv")
                if _encode(local_src, local_out, args.encoder, hb_exe, ffprobe_exe,
                           no_subs=True, log=log):
                    shutil.move(local_out, dest)
                    _ship_sidecars(side_dir, dest_dir, res)
                    for p in cleanup:
                        _rmf(p)
                    names = " ".join(s.name for s in siblings)
                    if res.ok:
                        for s in siblings:
                            _archive(s.path, done_dir, rip_dir, args.keep)
                    else:
                        _fail_quarantine(log, [s.path for s in siblings],
                                         os.path.join(issues_dir, "movies"), names, res.notes)
                    for s in siblings:
                        handled.add(s.path)
                    if not res.ok:
                        failed = 1
                    log.phase_end()
                else:
                    shutil.rmtree(side_dir, ignore_errors=True)
                    for p in cleanup + [local_out]:
                        _rmf(p)
                    log.note("encode", f"FAILED handbrake: {i.name} -- sources left in place")
                    print(f"FAILED (handbrake): {i.name} -- sources left in place", file=sys.stderr)
                    for s in siblings:
                        handled.add(s.path)
                    failed = 1
                    log.phase_end()
                continue

            # -- extras (non-main _t title, or an extras-disc title): as-is.
            # the .pN suffix is kept so split-disc extras never collide.
            ext = f".p{i.part}" if i.part >= 0 else ""
            dest_dir = os.path.join(lib_dir, safe, "extras")
            name = (f"{safe} - Extra {i.num_str}{ext}.mkv"
                    if i.sfx == "e" else f"{safe}_{i.sfx}{i.num_str}{ext}.mkv")
            dest = os.path.join(dest_dir, name)
            os.makedirs(dest_dir, exist_ok=True)
            local_src = os.path.join(tmp_dir, i.name)
            local_out = os.path.join(tmp_dir, i.base + ".h265.mkv")
            log.phase("encode")
            print(f"copying {i.name} to local temp...")
            _copy2(i.path, local_src)
            if _encode(local_src, local_out, args.encoder, hb_exe, ffprobe_exe, log=log):
                shutil.move(local_out, dest)
                _rmf(local_src)
                _archive(i.path, done_dir, rip_dir, args.keep)
                log.phase_end()
            else:
                _rmf(local_src)
                _rmf(local_out)
                log.note("encode", f"FAILED handbrake: {i.name} -- source left in place")
                print(f"FAILED (handbrake): {i.name} -- source left in place", file=sys.stderr)
                failed = 1
                log.phase_end()
    except Exception as e:
        failed = 1
        print(f"FATAL: {e}", file=sys.stderr)
    finally:
        log.finish(failed)

    if args.keep:
        print(f"--keep: lossless sources moved to {done_dir} -- verify, then clean by hand")
    return failed


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="transcode.py",
        description="Batch-transcode staged lossless MKVs to native-resolution H.265 for the Jellyfin library.",
    )
    p.add_argument("-k", "--keep", action="store_true",
                   help="move lossless sources to rip-DONE instead of deleting them (verify, then clean by hand)")
    p.add_argument("-e", "--encoder", choices=["x265", "nvenc", "qsv", "vce"], default="x265",
                   help="video encoder: x265 (default, software) or a GPU encoder (nvenc/qsv/vce)")
    p.add_argument("-w", "--workdir", metavar="DIR",
                   help="local temp work dir (default: $VIDEO_WORK_DIR or %%TEMP%%\\video-work)")
    p.add_argument("-v", "--verbose", action="count", default=0,
                   help="console verbosity: -v adds HandBrake error lines, -vv passes tool output through raw")
    p.add_argument("prefixes", nargs="*", metavar="NAME",
                   help="transcode only movies whose staged filename starts with this prefix")
    return p.parse_args(argv)


def main(argv=None) -> None:
    sys.exit(run_transcode(parse_args(argv)))


if __name__ == "__main__":
    main()
