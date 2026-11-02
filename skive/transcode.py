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
repeated here.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from skive import common
from skive import subs
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
_RX_DDIR = re.compile(r"^d\d+$")  # rip/<type>/<Name (Year)>/d<disc> staging subdir
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
    the encoder. no_subs selects zero subtitle tracks (features and TV
    episodes, the language-list trick); extras keep pass-through (embedded
    subs stay). Every HandBrake output line is
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
                print(line, end="", flush=True)
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
            print(line, flush=True)
    return rc


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
    else delete. keep comes from the -k/--keep flag."""
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


def _quarantine(src: str, item: Item, issues_root: str,
                log: common.RipLog) -> None:
    """Sources that must not be lost wait in issues/<type>/<Name (Year)>
    (CONTEXT.md: Issues folder, never clobbering), together with this run's
    log evidence."""
    d = os.path.join(issues_root, "tv" if item.is_tv else "movies", item.movie)
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, os.path.basename(src))
    if os.path.exists(dest):
        print(f"quarantine: {dest} already exists -- source left in place", file=sys.stderr)
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
        print(n)
    return side_dir, res


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
    print(f"FAILED (subtitle pass): {label} -- encode shipped subless, "
          f"sources quarantined to {folder}", file=sys.stderr)
    for item in items:
        _quarantine(item.path, item, issues_root, log)


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
    keep = args.keep
    retry = args.retry
    # --retry walks the issues folder instead of rip/ staging; the source
    # only leaves issues/ on success (delete, or -k park), a failure rests
    # in place with this run's evidence.
    input_dir = os.path.join(vid, "issues" if retry else "rip")
    lib_dir = os.path.join(vid, "library")
    lossless_root = os.path.join(vid, "lossless")
    issues_root = os.path.join(vid, "issues")  # sub-pass failures park sources here
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
    inflight_side: str | None = None  # this item's sidecar temp dir, once the pass starts
    log.start()
    try:
        for i in items:
            if i.path in handled:
                continue
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
                side_dir = res = None  # extras: no pass, embedded subs stay
                if episode:
                    # the sidecar dir is created up front and registered as
                    # this item's temp BEFORE the pass runs, so an interrupt
                    # mid-pass drops it too (CONTEXT.md: Interrupt); the
                    # sidecar base name carries the episode label, not the
                    # bare show name
                    side_dir = tempfile.mkdtemp(dir=tmp_dir, prefix="subs-")
                    inflight_side = side_dir
                    side_dir, res = _sub_pass(local_src, side_dir,
                                              f"{safe} - S{i.sn}E{i.en}{i.tail}",
                                              seconv_exe, ffprobe_exe, log=log)
                local_out = os.path.join(tmp_dir, i.base + ".h265.mkv")
                inflight = cleanup + [local_out]
                if _encode(local_src, local_out, args.encoder, hb_exe, ffprobe_exe,
                           no_subs=episode, log=log):
                    shutil.move(local_out, dest)
                    if side_dir is not None:
                        _ship_sidecars(side_dir, dest_dir, res)
                    if episode:
                        _copy_show_doc(i.path, dest_dir)  # the doc travels beside its encodes
                    for p in cleanup:
                        _rmf(p)
                    if res is None or res.ok:
                        _archive(i.path, i, lossless_root, keep)
                    else:
                        _fail_quarantine(log, [i], issues_root, i.name, res.notes)
                        failed = 1
                    handled.add(i.path)
                    log.phase_end()
                else:
                    if side_dir is not None:
                        shutil.rmtree(side_dir, ignore_errors=True)
                    for p in cleanup + [local_out]:
                        _rmf(p)
                    log.note("encode", f"FAILED handbrake: {i.name} -- source left in place")
                    handled.add(i.path)
                    failed = 1
                    log.phase_end()
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
                # part order: no-suffix first (-1 -- rescues a forgotten
                # --part on disc 1), then .pN ascending.
                siblings = sorted(
                    (s for s in items if s.movie == i.movie and s.sfx == "t" and s.num == i.num),
                    key=lambda s: s.part,
                )
                dest_dir = os.path.join(lib_dir, "movies", safe)
                dest = os.path.join(dest_dir, safe + ".mkv")
                log.phase("encode")
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
                        failed = 1
                        log.phase_end()
                        continue
                    os.makedirs(dest_dir, exist_ok=True)
                    local_parts = []
                    for s in siblings:
                        lb = os.path.join(tmp_dir, s.name)
                        print(f"copying {s.name} to local temp...")
                        inflight.append(lb)  # BEFORE the copy: mid-copy interrupt drops the partial
                        _copy2(s.path, lb)
                        local_parts.append(lb)
                        cleanup.append(lb)
                    local_src = os.path.join(tmp_dir, f"{safe}_t{i.num_str}.merged.mkv")
                    inflight.append(local_src)  # BEFORE the merge: mid-merge interrupt drops the partial
                    mrc = _merge(local_src, local_parts, mkv_exe, log=log)
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
                        log.phase_end()
                        continue
                else:
                    os.makedirs(dest_dir, exist_ok=True)
                    local_src = os.path.join(tmp_dir, i.name)
                    print(f"copying {i.name} to local temp...")
                    inflight.append(local_src)  # BEFORE the copy
                    _copy2(i.path, local_src)
                    cleanup.append(local_src)

                # the inline subtitle pass runs on the merged (or single) local
                # copy BEFORE the encode (the main encodes zero-track
                # meanwhile); the sidecar dir is this item's temp from
                # creation, so an interrupt mid-pass drops it too (CONTEXT.md:
                # Interrupt)
                side_dir = tempfile.mkdtemp(dir=tmp_dir, prefix="subs-")
                inflight_side = side_dir
                side_dir, res = _sub_pass(local_src, side_dir, safe,
                                          seconv_exe, ffprobe_exe, log=log)
                local_out = os.path.join(tmp_dir, f"{safe}_t{i.num_str}.h265.mkv")
                inflight = cleanup + [local_out]
                if _encode(local_src, local_out, args.encoder, hb_exe, ffprobe_exe,
                           no_subs=True, log=log):
                    shutil.move(local_out, dest)
                    _ship_sidecars(side_dir, dest_dir, res)
                    # the doc that picked this main gets the encode appended
                    # (append-once) and a library copy beside the encode
                    if i.movie in doc_path:
                        _record_encode(doc_path[i.movie], dest, i.num, ffprobe_exe)
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
                            _archive(s.path, s, lossless_root, keep)
                    else:
                        _fail_quarantine(log, siblings, issues_root, names, res.notes)
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
                    log.phase_end()
                continue

            # -- extras (non-main _t title, or an extras-disc title): the
            # ` - Extra NN` form, number rolled forward past taken names —
            # no staging label ever reaches the library. Every embedded track
            # stays: no subtitle pass, no zero-subs trick.
            ext = f".p{i.part}" if i.part >= 0 else ""
            dest_dir = os.path.join(lib_dir, "movies", safe, "extras")
            dest = os.path.join(dest_dir, _extra_name(dest_dir, safe, i.num, ext))
            log.phase("encode")
            os.makedirs(dest_dir, exist_ok=True)
            cleanup: list[str] = []
            local_src = os.path.join(tmp_dir, i.name)
            print(f"copying {i.name} to local temp...")
            inflight.append(local_src)  # BEFORE the copy
            _copy2(i.path, local_src)
            cleanup.append(local_src)
            local_out = os.path.join(tmp_dir, i.base + ".h265.mkv")
            inflight = cleanup + [local_out]
            if _encode(local_src, local_out, args.encoder, hb_exe, ffprobe_exe,
                       log=log):  # no no_subs: embedded subs stay in the file
                shutil.move(local_out, dest)
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
    except KeyboardInterrupt:
        # CONTEXT.md Interrupt: drop this item's partial temp output (the local
        # copy, the unshipped sidecar dir, and .h265 encode live in the work
        # dir), leave the SOURCE in staging — sources only move on success —
        # record status=interrupted, exit 130.
        for p in inflight:
            _rmf(p)
        if inflight_side is not None:
            shutil.rmtree(inflight_side, ignore_errors=True)
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
