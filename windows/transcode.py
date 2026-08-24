#!/usr/bin/env python3
"""Standalone Windows transcode — batch re-encode rip\\ staging into the Jellyfin library.

Port of windows/transcode-video.ps1 to stdlib-only Python (3.10+; runs on a
clean `python.exe`, no rich, no skive imports). The whole Windows install spec
is: winget Python 3, HandBrake (CLI reachable), MKVToolNix, and the share
mapped (default `Z:\\`, `$env:VIDEOS` overrides).

The staging subdirectory decides the type: anything under `rip\\{TV,tv}` is TV
(no main-feature pick, no merging); everything else is a movie. This is a
pass-through re-encode: every audio + subtitle track kept, video only
re-encoded, nothing burned in.

Usage:  python transcode.py [-k] [-e x265|nvenc|qsv|vce] [-w DIR] [prefix...]
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Staged names: <Movie (Year)>_tNN[.pN].mkv (titles), _eNN[.pN].mkv (extras
# disc), <Show (Year)>_SNNENN[-ENN].mkv (TV, incl. doubles).
_RX_TITLE = re.compile(
    r"^(?P<movie>.+)_(?P<sfx>[te])(?P<num>\d+)(?P<part>\.p(?P<pn>\d+))?\.mkv$"
)
_RX_EP = re.compile(
    r"^(?P<movie>.+)_S(?P<sn>\d{2})E(?P<en>\d{2})(?P<tail>-E\d{2})?\.mkv$"
)
_TV_ROOTS = ("TV", "tv")  # rip\TV (legacy ps1 staging) and rip\tv (SPEC layout)
_HB = "HandBrakeCLI.exe"
_MKV = "mkvmerge.exe"
# one comma-joined argument: stays a single argv entry
_COPY_MASK = "aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac,pcm"


def sanitize(s: str) -> str:
    """Strip filesystem-hostile chars; keeps ( ) for "Title (Year)"."""
    return re.sub(r'[/:*?"<>|]', "", s)


class RipLog:
    """Same 8-column rip.log as the Linux side: start end script software name phase m:ss status."""

    SCRIPT = "transcode.py"
    SOFT = "handbrake"

    def __init__(self, path: str = "rip.log", now=None):
        self.path = Path(path)
        self._now = now or datetime.now
        self.name = "-"
        self._phases = 0

    def _emit(self, t1, t2, phase, m, s, status) -> None:
        line = f"{t1}  {t2}  {self.SCRIPT}  {self.SOFT}  {self.name}  {phase}  {m}m{s:02d}  {status}"
        print(line)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def start(self) -> None:
        self._t0 = self._now()
        self._t0_ts = self._t0.strftime("%Y-%m-%d %H:%M:%S")
        self._emit(self._t0_ts, "-", "-", 0, 0, "started")

    def phase(self, name: str) -> None:
        self._phase_name = name
        self._phase_t0 = self._now()

    def phase_end(self) -> None:
        now = self._now()
        el = int((now - self._phase_t0).total_seconds())
        self._phases += 1
        self._emit(
            self._phase_t0.strftime("%Y-%m-%d %H:%M:%S"),
            now.strftime("%Y-%m-%d %H:%M:%S"),
            self._phase_name, el // 60, el % 60, "ok",
        )

    def finish(self, rc: int) -> None:
        # one phase that succeeded already logged start..end..status — a total
        # would just duplicate it (mirrors rip-common.sh). failures get one.
        if rc == 0 and self._phases == 1:
            return
        now = self._now()
        el = int((now - self._t0).total_seconds())
        status = "ok" if rc == 0 else f"FAIL({rc})"
        self._emit(self._t0_ts, now.strftime("%Y-%m-%d %H:%M:%S"), "total",
                   el // 60, el % 60, status)


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
        else:
            print(f"skip (junk label -- mid-rip or failed rename): {f}", file=sys.stderr)
            continue
        # junk guard: no "(YYYY)" tail = rip in progress or failed rename
        if not re.search(r"\(\d{4}\)$", movie):
            print(f"skip (junk label -- mid-rip or failed rename): {f}", file=sys.stderr)
            continue
        if want:
            if not any(f.name.startswith(w) for w in want):
                continue
            for w in want:
                if f.name.startswith(w):
                    want_hit.add(w)
        items.append(Item(
            path=str(f), name=f.name, base=f.stem, movie=movie, sfx=sfx,
            num=num, num_str=num_str, part=part, size=f.stat().st_size,
            is_tv=is_tv, sn=sn, en=en, tail=tail,
        ))
    return items, want_hit


def _encode(src: str, out: str, encoder: str, hb_exe: str) -> bool:
    """HandBrake pass-through re-encode. GPU encoders override the encoder."""
    cmd = [hb_exe, "-i", src, "-o", out]
    if encoder != "x265":
        cmd += ["--encoder", f"{encoder}_h265", "--quality", "28"]
    cmd += [
        "--preset", "H.265 MKV 1080p30",
        "--all-audio", "--aencoder", "copy",
        "--audio-copy-mask", _COPY_MASK,
        "--audio-fallback", "ac3",
        "--all-subtitles", "--subtitle-burned=none",
    ]
    return subprocess.run(cmd).returncode == 0


def _rmf(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


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


def run_transcode(args) -> int:
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
    tmp_dir = args.workdir or os.environ.get("VIDEO_WORK_DIR") or os.path.join(tempfile.gettempdir(), "video-work")
    os.makedirs(tmp_dir, exist_ok=True)

    hb_exe = _find_tool(_HB, os.path.join("HandBrake", _HB))
    mkv_exe = _find_tool(_MKV, os.path.join("MKVToolNix", _MKV))
    if not hb_exe or not mkv_exe:
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
    # main's .pN halves sum; tie -> lowest number). _eNN are never main.
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
            # -- Specials), _eNN -> the show's extras.
            if i.is_tv:
                show_dir = os.path.join(lib_dir, "TV", safe)
                if i.sfx == "S":
                    season = "Specials" if int(i.sn) == 0 else f"Season {i.sn}"
                    dest_dir = os.path.join(show_dir, season)
                    dest = os.path.join(dest_dir, f"{safe} - S{i.sn}E{i.en}{i.tail}.mkv")
                else:
                    ext = f".p{i.part}" if i.part >= 0 else ""
                    dest_dir = os.path.join(show_dir, "extras")
                    dest = os.path.join(dest_dir, f"{safe}_{i.sfx}{i.num_str}{ext}.mkv")
                os.makedirs(dest_dir, exist_ok=True)
                local_src = os.path.join(tmp_dir, i.name)
                local_out = os.path.join(tmp_dir, i.base + ".h265.mkv")
                log.phase("encode")
                print(f"copying {i.name} to local temp...")
                shutil.copy2(i.path, local_src)
                if _encode(local_src, local_out, args.encoder, hb_exe):
                    shutil.move(local_out, dest)
                    _rmf(local_src)
                    _archive(i.path, done_dir, rip_dir, args.keep)
                    log.phase_end()
                else:
                    _rmf(local_src)
                    _rmf(local_out)
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
                os.makedirs(dest_dir, exist_ok=True)
                log.phase("encode")
                cleanup: list[str] = []
                if len(siblings) > 1:
                    local_parts = []
                    for s in siblings:
                        lb = os.path.join(tmp_dir, s.name)
                        print(f"copying {s.name} to local temp...")
                        shutil.copy2(s.path, lb)
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
                    local_src = os.path.join(tmp_dir, i.name)
                    print(f"copying {i.name} to local temp...")
                    shutil.copy2(i.path, local_src)
                    cleanup.append(local_src)

                local_out = os.path.join(tmp_dir, f"{safe}_t{i.num_str}.h265.mkv")
                if _encode(local_src, local_out, args.encoder, hb_exe):
                    shutil.move(local_out, dest)
                    for p in cleanup:
                        _rmf(p)
                    for s in siblings:
                        _archive(s.path, done_dir, rip_dir, args.keep)
                    for s in siblings:
                        handled.add(s.path)
                    log.phase_end()
                else:
                    for p in cleanup + [local_out]:
                        _rmf(p)
                    print(f"FAILED (handbrake): {i.name} -- sources left in place", file=sys.stderr)
                    for s in siblings:
                        handled.add(s.path)
                    failed = 1
                    log.phase_end()
                continue

            # -- extras (non-main _t title, or an _e extras-disc title): as-is.
            # the .pN suffix is kept so split-disc extras never collide.
            ext = f".p{i.part}" if i.part >= 0 else ""
            dest_dir = os.path.join(lib_dir, safe, "extras")
            dest = os.path.join(dest_dir, f"{safe}_{i.sfx}{i.num_str}{ext}.mkv")
            os.makedirs(dest_dir, exist_ok=True)
            local_src = os.path.join(tmp_dir, i.name)
            local_out = os.path.join(tmp_dir, i.base + ".h265.mkv")
            log.phase("encode")
            print(f"copying {i.name} to local temp...")
            shutil.copy2(i.path, local_src)
            if _encode(local_src, local_out, args.encoder, hb_exe):
                shutil.move(local_out, dest)
                _rmf(local_src)
                _archive(i.path, done_dir, rip_dir, args.keep)
                log.phase_end()
            else:
                _rmf(local_src)
                _rmf(local_out)
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
        description="Batch-transcode staged lossless MKVs to 1080p H.265 for the Jellyfin library.",
    )
    p.add_argument("-k", "--keep", action="store_true",
                   help="move lossless sources to rip-DONE instead of deleting them (verify, then clean by hand)")
    p.add_argument("-e", "--encoder", choices=["x265", "nvenc", "qsv", "vce"], default="x265",
                   help="video encoder: x265 (default, software) or a GPU encoder (nvenc/qsv/vce)")
    p.add_argument("-w", "--workdir", metavar="DIR",
                   help="local temp work dir (default: $VIDEO_WORK_DIR or %%TEMP%%\\video-work)")
    p.add_argument("prefixes", nargs="*", metavar="NAME",
                   help="transcode only movies whose staged filename starts with this prefix")
    return p.parse_args(argv)


def main(argv=None) -> None:
    sys.exit(run_transcode(parse_args(argv)))


if __name__ == "__main__":
    main()
