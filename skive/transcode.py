"""skive transcode — batch re-encode rip/ staging into the Jellyfin library.

BC-250 slice 02, ported from the frozen windows/transcode.py: the movie MAIN
arm only. The batch walks the videos share's rip/ staging at both resting
depths, skips junk labels, picks each movie's main (the validation file's
recorded main, biggest-bytes only as the legacy fallback), encodes with
HandBrake at native resolution (zero subtitle tracks), and ships to
library/movies/<Name>/<Name>.mkv with the validation document travelling
beside the encode and the encode result appended to it; the lossless source
is deleted after the verified ship (slice 06 adds the -k park). Split mains
skip whole until the merge pass (slice 05); TV, extras and the inline
subtitle pass are later slices — those staged items fall through the batch
unencoded.

Shared policy (run log, verbosity tiers, interrupt machinery, sanitize)
comes from skive.common; the validation document's reader/writer/name from
skive.validation. The Windows original's deliberate duplicates are not
repeated here.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from skive import common
from skive.validation import (
    VALIDATION_FILENAME,
    load_validation,
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
_TV_ROOTS = ("TV", "tv")  # rip/TV (legacy ps1 staging) and rip/tv (SPEC layout)
# one comma-joined argument: stays a single argv entry
_COPY_MASK = "aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac,pcm"


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
        print(f"{exe} not found on PATH -- see README (transcode box: Install)",
              file=sys.stderr)
    return found


def _scan(input_dir: str, want: list[str]) -> tuple[list[Item], set[str]]:
    """Walk the input tree (rip/ staging; issues/ with --retry, slice 06) for
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
            print(f"skip (junk label -- mid-rip or failed rename): {f}", file=sys.stderr)
            continue
        # junk guard: no "(YYYY)" tail = rip in progress or failed rename
        if not re.search(r"\(\d{4}\)$", movie):
            print(f"skip (junk label -- mid-rip or failed rename): {f}", file=sys.stderr)
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
            no_subs: bool = False, log: common.RipLog | None = None) -> bool:
    """HandBrake pass-through re-encode at native resolution. GPU encoders override
    the encoder. no_subs selects zero subtitle tracks (features/TV, the language-list
    trick); extras keep pass-through (slice 04). Every HandBrake output line is
    written verbatim to the run's raw log (log.raw), the same tee discipline as
    skive/makemkv.py, while the console sees it gated by the verbosity tiers
    (common.VERBOSITY): quiet at 0, HandBrake error lines at -v, everything at -vv;
    anything the level hid is replayed verbatim when the encode fails.
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
    verbose = common.VERBOSITY
    hidden: list[str] = []  # console-suppressed lines, replayed on failure
    try:
        for line in proc.stdout:
            stripped = line.rstrip()
            if verbose >= 2 or (verbose >= 1 and stripped.startswith("ERROR")):
                print(line, end="", flush=True)
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


def _archive(src: str, item: Item, lossless_root: str, keep: bool) -> None:
    """-Keep parks the lossless source under lossless/<type>/<Name (Year)>
    (CONTEXT.md: Lossless folder — never clobbering, never auto-deleted);
    else delete. Slice 06 adds the flag; until then keep is always False."""
    if not keep:
        _rmf(src)
        return
    d = os.path.join(lossless_root, "tv" if item.is_tv else "movies", item.movie)
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, os.path.basename(src))
    if os.path.exists(dest):
        print(f"--keep: {dest} already exists -- source left in {os.path.dirname(src)}", file=sys.stderr)
    else:
        shutil.move(src, dest)


def _record_encode(doc_path: str, dest: str, src_num: int, ffprobe_exe: str) -> None:
    """Append the main encode's result to the title folder's validation.json
    and copy the doc into the library folder beside the encode. The encodes
    entry is {"file", "src", "dur_s"} (skive/validation.py's schema): the
    dest file's name, the source title number as "tNN", and the actual
    duration probed from the encode output. Append-once: an entry already
    naming this dest file — a re-run — is replaced by the fresh probe, never
    duplicated. A doc absent or unreadable here skips the append and the
    copy: the record never crashes the pipeline."""
    doc = load_validation(Path(doc_path))
    if doc is None:
        return
    entry = {"file": os.path.basename(dest), "src": f"t{src_num}",
             "dur_s": _probe_duration(dest, ffprobe_exe)}
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


def run_transcode(args) -> int:
    common.set_verbosity(getattr(args, "verbose", 0))
    vid = os.environ.get("VIDEOS_MOUNT", "/mnt/videos")
    if not os.path.isdir(vid):
        print(
            f"skive transcode: {vid} is not reachable. The videos share isn't "
            f"mounted -- mount it at {vid} (see README: transcode box), or set VIDEOS_MOUNT.",
            file=sys.stderr,
        )
        return 1
    keep = getattr(args, "keep", False)    # slice 06 adds the -k flag
    retry = getattr(args, "retry", False)  # slice 06 adds the --retry flag
    # --retry re-runs quarantined items from issues/ (slice 06); this slice
    # always walks rip/ staging.
    input_dir = os.path.join(vid, "issues" if retry else "rip")
    lib_dir = os.path.join(vid, "library")
    lossless_root = os.path.join(vid, "lossless")
    tmp_dir = args.workdir or os.environ.get("VIDEO_WORK_DIR") or os.path.join(tempfile.gettempdir(), "video-work")
    os.makedirs(tmp_dir, exist_ok=True)

    hb_exe = _find_on_path("HandBrakeCLI")
    mkv_exe = _find_on_path("mkvmerge")
    ffprobe_exe = _find_on_path("ffprobe")
    seconv_exe = _find_on_path("seconv")
    tess_exe = _find_on_path("tesseract")  # seconv shells out to it
    if not hb_exe or not mkv_exe or not ffprobe_exe or not seconv_exe or not tess_exe:
        return 1

    log = common.RipLog("skive transcode", "handbrake", "-", root=vid)
    items, want_hit = _scan(input_dir, args.prefixes)
    for w in args.prefixes:
        if w not in want_hit:
            print(f"no staged MKVs match: {w}", file=sys.stderr)
    if not items:
        if args.prefixes:
            print("no matching MKVs to transcode", file=sys.stderr)
            return 1
        print(f"no MKVs in {input_dir}")
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
    doc_path: dict[str, str] = {}      # movie -> its validation.json (append + copy)
    rec_missing: dict[str, str] = {}   # movie -> recorded main with no staged title
    for movie in sorted({i.movie for i in items if i.sfx == "t" and not i.is_tv}):
        dpath = os.path.join(os.path.dirname(
            next(i.path for i in items if i.movie == movie and i.sfx == "t")),
            VALIDATION_FILENAME)
        doc = load_validation(Path(dpath))
        if doc is None:
            continue
        doc_path[movie] = dpath
        rec = doc.get("main")
        if rec is None:
            continue  # the record says no candidate main existed: bytes fallback
        if not isinstance(rec, str) or not _RX_MAIN.match(rec):
            print(f"validation: {dpath}: unreadable main {rec!r} -- biggest-bytes fallback",
                  file=sys.stderr)
            continue
        num = int(rec[1:])
        if any(i.num == num for i in items if i.movie == movie and i.sfx == "t"):
            main_num[movie] = (num, grp.get((movie, num), 0))  # the record wins
        else:
            rec_missing[movie] = rec

    failed = 0
    handled: set[str] = set()
    missing_warned: set[str] = set()  # movies whose no-staged-main note already printed
    # The in-flight item's local temp files (CONTEXT.md: Interrupt): the
    # interrupt path deletes exactly these — the local copy and the encode
    # partial — while the SOURCE stays in staging (sources only move on
    # success). Reset per item; stale entries after a finished item are
    # harmless (the files are already gone and _rmf tolerates that).
    inflight: list[str] = []
    log.start()
    try:
        for i in items:
            if i.path in handled:
                continue
            inflight = []
            log.name = i.movie
            safe = common.sanitize(i.movie)

            # -- TV episodes: the season/episode arm is slice 05 — they fall
            # -- through the batch unencoded this slice.
            if i.is_tv:
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

            # -- main feature: encode as one movie (a split main skips whole
            # -- until the merge pass, slice 05).
            if i.sfx == "t" and i.movie in main_num and main_num[i.movie][0] == i.num:
                siblings = sorted(
                    (s for s in items if s.movie == i.movie and s.sfx == "t" and s.num == i.num),
                    key=lambda s: s.part,
                )
                dest_dir = os.path.join(lib_dir, "movies", safe)
                dest = os.path.join(dest_dir, safe + ".mkv")
                if len(siblings) > 1:
                    # slice 05 adds the part merge; until then a split main
                    # skips entire -- no partial ship, no source move.
                    names = " ".join(s.name for s in siblings)
                    msg = (f"{i.movie}: split main ({len(siblings)} parts) needs the "
                           f"merge pass -- parts left in staging")
                    log.note("merge", msg)
                    for s in siblings:
                        handled.add(s.path)
                    continue
                log.phase("encode")
                os.makedirs(dest_dir, exist_ok=True)
                # the local copy is registered in `inflight` AS CREATED so an
                # interrupt during the copy window drops the partial too
                # (CONTEXT.md: Interrupt)
                cleanup: list[str] = []
                local_src = os.path.join(tmp_dir, i.name)
                print(f"copying {i.name} to local temp...")
                inflight.append(local_src)  # BEFORE the copy
                _copy2(i.path, local_src)
                cleanup.append(local_src)
                # the inline subtitle pass (sidecars, slice 03) runs here in
                # the full flow; the main already encodes with zero subtitle
                # tracks meanwhile
                local_out = os.path.join(tmp_dir, f"{safe}_t{i.num_str}.h265.mkv")
                inflight = cleanup + [local_out]
                if _encode(local_src, local_out, args.encoder, hb_exe, ffprobe_exe,
                           no_subs=True, log=log):
                    shutil.move(local_out, dest)
                    # the doc that picked this main gets the encode appended
                    # (append-once) and a library copy beside the encode
                    if i.movie in doc_path:
                        _record_encode(doc_path[i.movie], dest, i.num, ffprobe_exe)
                    for p in cleanup:
                        _rmf(p)
                    _archive(i.path, i, lossless_root, keep)
                    handled.add(i.path)
                    log.phase_end()
                else:
                    for p in cleanup + [local_out]:
                        _rmf(p)
                    log.note("encode", f"FAILED handbrake: {i.name} -- source left in place")
                    handled.add(i.path)
                    failed = 1
                    log.phase_end()
                continue

            # -- extras (non-main _t title, or an extras-disc title): the
            # -- ` - Extra NN` arm is slice 04 — they fall through unencoded.
    except KeyboardInterrupt:
        # CONTEXT.md Interrupt: drop this item's partial temp output (the local
        # copy and .h265 encode live in the work dir), leave the SOURCE in
        # staging — sources only move on success — record status=interrupted,
        # exit 130.
        for p in inflight:
            _rmf(p)
        log.interrupted()
        print("skive transcode: interrupted -- partial temp output deleted, source left in staging",
              file=sys.stderr)
        return 130
    except Exception as e:
        failed = 1
        print(f"FATAL: {e}", file=sys.stderr)
    finally:
        log.finish(failed)

    if keep:
        print(f"--keep: lossless sources moved to {lossless_root} -- verify, then clean by hand")
    return failed
