#!/usr/bin/env python3
"""Real-file sanity + dump-replay verification for the chapter split (ADR 0009).

Two modes, both run from the repo root with real tools and no disc:

  uv run python scripts/verify_chapter_split.py
      Synthesizes a 3-chapter MKV with REAL ffmpeg (testsrc + sine, x264/aac,
      keyframes forced AT the chapter starts so stream-copy cuts are exact),
      names it as a combined `Verify (2000)_S01E01-E03.mkv` staging target,
      cuts it through the REAL skive.chapter_split.split_combined with the
      REAL subprocess.run — only the boundary prompt is scripted, answering
      Enter — then ffprobes every piece: each duration must match its
      chapter span (±1s) and the codecs must be stream-copy clean.

  uv run python scripts/verify_chapter_split.py --replay [LOG]
      Replays the captured Downton Abbey S4 D1 scan dump through the REAL
      `skive rip tv` flow (MAKEMKV_INFO="cat <dump>" — scan.py's replay
      contract) with piped stdin: search "Downton Abbey", pick 1, season 4,
      range 1-3, then q at the confirm — the goal is the confirm table plus
      the play-all-only caption, not a rip. Full output lands in LOG
      (default /tmp/orchestrator/04-dump-replay.log). The dump's media token
      (DVD+R-DL) trips the recordable gate, hence --verified.

      VIDEOS_MOUNT must already be a mounted directory (skive.common
      .ensure_mounted checks /proc/mounts and this box has no sudo), so the
      script picks the first of $XDG_RUNTIME_DIR, /tmp, /dev/shm — all
      tmpfs: nothing real is touched and it is gone at reboot. Everything
      the replay creates under the mount is removed afterwards.

Any failed check prints FAIL and exits 1; a clean run prints only PASS.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# --- Verification 1: real-file cut sanity ----------------------------------

CHAPTER_STARTS = [0.0, 60.0, 150.0]  # chapter starts in seconds; keyframes forced here
TOTAL_S = 240.0
EP_RUNTIMES = {1: 60, 2: 90, 3: 90}  # summed targets land exactly on chapters 2 and 3
DISPLAY = "Verify (2000)"
SEASON = 1
DURATION_TOL_S = 1.0
KEYFRAME_TOL_S = 0.05
CHAPTER_TOL_S = 0.05


class _Enter:
    """The scripted boundary prompt: answer Enter — accept the suggested layout."""

    @staticmethod
    def ask(*_args, **_kwargs):
        return ""


def _ffprobe(path: Path, entries: str, stream: str | None = None) -> str:
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "csv=p=0", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _keyframe_times(path: Path) -> list[float]:
    """Video keyframe PTS times, from ffprobe packet flags."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    times = []
    for line in r.stdout.splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and "K" in parts[1]:
            try:
                times.append(float(parts[0]))
            except ValueError:
                pass
    return times


def verify_real_file_cut() -> bool:
    """Synthesize a chaptered MKV, cut it through the real split, probe pieces."""
    sys.path.insert(0, str(REPO))
    import skive.chapter_split as chapter_split
    from skive.chapter_split import probe_chapters, split_combined
    from skive.common import RipLog

    work = Path(tempfile.mkdtemp(prefix="verify-chapter-split-"))
    show_dir = work / "rip" / "tv" / DISPLAY
    show_dir.mkdir(parents=True)
    combined = show_dir / f"{DISPLAY}_S{SEASON:02d}E01-E03.mkv"
    targets = {e: show_dir / f"{DISPLAY}_S{SEASON:02d}E{e:02d}.mkv" for e in (1, 2, 3)}
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))

    # Synthesize: testsrc video + sine audio, chapters via an ffmetadata
    # sidecar input (-map_chapters 2), keyframes AT the chapter starts so
    # the stream-copy cuts are exact — DVD chapters are keyframe-aligned.
    meta = [";FFMETADATA1"]
    ends = CHAPTER_STARTS[1:] + [TOTAL_S]
    for start_s, end_s in zip(CHAPTER_STARTS, ends):
        meta += ["[CHAPTER]", "TIMEBASE=1/1000",
                 f"START={int(start_s * 1000)}", f"END={int(end_s * 1000)}"]
    meta_file = work / "chapters.txt"
    meta_file.write_text("\n".join(meta) + "\n", encoding="utf-8")
    key_frames = ",".join(str(int(s)) for s in CHAPTER_STARTS)

    synth = subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
         "-f", "ffmetadata", "-i", str(meta_file),
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k",
         "-force_key_frames", key_frames, "-t", str(TOTAL_S),
         "-map_chapters", "2", str(combined)],
        capture_output=True, text=True)
    check("synthesized 3-chapter MKV with real ffmpeg",
          synth.returncode == 0 and combined.exists(), synth.stderr.strip())
    if synth.returncode != 0 or not combined.exists():
        return _report(checks, work)

    probed = probe_chapters(combined, run=subprocess.run)
    ok = (probed is not None and len(probed[0]) == len(CHAPTER_STARTS)
          and all(abs(a - b) <= CHAPTER_TOL_S
                  for a, b in zip(probed[0], CHAPTER_STARTS))
          and TOTAL_S - 1 <= probed[1] <= TOTAL_S + 1)
    check("chapter probe reads the synthesized chapters", ok,
          f"starts={probed[0] if probed else None}, "
          f"duration={probed[1] if probed else None}s")
    if probed is None:
        return _report(checks, work)
    chapter_starts, duration_s = probed

    keys = _keyframe_times(combined)
    at_starts = all(any(abs(k - s) <= KEYFRAME_TOL_S for k in keys)
                    for s in CHAPTER_STARTS)
    check("keyframes sit at the chapter starts (stream-copy cuts will be exact)",
          at_starts, f"keyframes at {keys}")

    # The REAL split path: real probe, real ffmpeg cuts (run=subprocess.run);
    # only the boundary prompt is scripted — Enter accepts the suggestion.
    log = RipLog("verify_chapter_split", "ffmpeg", DISPLAY, root=work)
    log.start()
    real_prompt = chapter_split.Prompt
    chapter_split.Prompt = _Enter
    try:
        split_combined(combined, targets, SEASON, 1, 3, EP_RUNTIMES, log,
                       run=subprocess.run)
    finally:
        chapter_split.Prompt = real_prompt
    log.finish(0)

    check("all three episode pieces exist", all(t.exists() for t in targets.values()))
    check("combined file deleted once every piece exists", not combined.exists())

    spans = list(zip(CHAPTER_STARTS, CHAPTER_STARTS[1:] + [duration_s]))
    for e in (1, 2, 3):
        piece = targets[e]
        want_s = spans[e - 1][1] - spans[e - 1][0]
        if not piece.exists():
            check(f"S{SEASON:02d}E{e:02d} duration matches its chapter span",
                  False, "piece missing")
            continue
        dur_raw = _ffprobe(piece, "format=duration")
        try:
            dur = float(dur_raw)
        except ValueError:
            dur = None
        check(f"S{SEASON:02d}E{e:02d} duration matches its chapter span "
              f"({want_s:.0f}s ±{DURATION_TOL_S:.0f}s)",
              dur is not None and abs(dur - want_s) <= DURATION_TOL_S,
              f"ffprobe duration={dur}s")
        vcodec = _ffprobe(piece, "stream=codec_name", "v:0")
        acodec = _ffprobe(piece, "stream=codec_name", "a:0")
        check(f"S{SEASON:02d}E{e:02d} is stream-copy clean (h264/aac)",
              vcodec == "h264" and acodec == "aac", f"codecs={vcodec}/{acodec}")

    notes = [obj.get("status") for line in (work / "logs" / "rip.jsonl").read_text().splitlines()
             if line for obj in [json.loads(line)] if obj.get("phase") == "split"]
    check("run log records the split success",
          notes == ["3 episodes cut — combined deleted"], str(notes))
    return _report(checks, work)


def _report(checks: list[tuple[str, bool, str]],
            own_workdir: Path | None = None) -> bool:
    """The summary. own_workdir is verification 1's own temp workdir, removed
    on success and kept on failure — NEVER the replay's mount."""
    failed = [name for name, ok, _ in checks if not ok]
    if failed:
        where = (f" — synthetic work kept for inspection at {own_workdir}"
                 if own_workdir else "")
        print(f"FAIL: {len(failed)} of {len(checks)} checks failed{where}")
        return False
    if own_workdir is not None:
        shutil.rmtree(own_workdir, ignore_errors=True)
    print(f"PASS: all {len(checks)} checks — real-file chapter-split sanity holds")
    return True


# --- Verification 2: dump replay through the scanner ------------------------

DUMP = REPO / ".scratch" / "downton-s4-disk1.txt"
DEFAULT_REPLAY_LOG = Path("/tmp/orchestrator/04-dump-replay.log")
# Piped stdin answers, in prompt order: TMDB search query (the dump's volume
# label "G1 70736D1" prefill search finds nothing), pick 1 = Downton Abbey
# (2010), season 4, the range (prefill is "1" — one eligible title), then the
# confirm-table fix row 0=1-3 that hands the lone title the double — the
# caption arms only on a ranged assignment — and q to quit.
ANSWERS = "Downton Abbey\n1\n4\n1-3\n0=1-3\nq\n"
SEASON_HEADER = "Downton Abbey (2010) — season 04"
CAPTION = "title 0 carries S04E01-E03 — play-all-only disc, split after the rip"


def _temp_mount_root() -> Path | None:
    """A directory the real flow accepts as VIDEOS_MOUNT.

    ensure_mounted only proceeds when the path is an already-mounted
    filesystem (/proc/mounts) and this box has no sudo, so a plain temp dir
    cannot be used. The candidates are tmpfs mounts: nothing written there
    survives reboot, so the replay touches nothing real.
    """
    mounted = {parts[1]
               for line in Path("/proc/mounts").read_text().splitlines()
               if len(parts := line.split()) >= 3 and parts[2] == "tmpfs"}
    for cand in [os.environ.get("XDG_RUNTIME_DIR"), "/tmp", "/dev/shm"]:
        if not cand or cand not in mounted or not os.access(cand, os.W_OK):
            continue
        probe = Path(cand) / ".skive-replay-probe"   # access bits can lie
        try:                                        # (sandboxes, ro tmpfs) —
            probe.mkdir()                           # prove the dir writable
            probe.rmdir()
            return Path(cand)
        except OSError:
            continue
    return None


def _tmdb_key() -> str | None:
    key = os.environ.get("TMDB_API_KEY")
    if key:
        return key
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("TMDB_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return None


def verify_dump_replay(log_path: Path, dump: Path) -> bool:
    """Replay the captured dump through the real `skive rip tv` to the confirm."""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))

    mount = _temp_mount_root()
    check("temp VIDEOS_MOUNT found (tmpfs, already-mounted)", mount is not None,
          str(mount))
    key = _tmdb_key()
    check("TMDB_API_KEY available (env or repo .env)", bool(key))
    check("captured dump present", dump.exists(), str(dump))
    if mount is None or not key or not dump.exists():
        return _report(checks)

    before = set(mount.rglob("*"))
    env = dict(os.environ)
    env["MAKEMKV_INFO"] = f"cat {dump}"   # scan.py's replay contract: no disc
    env["VIDEOS_MOUNT"] = str(mount)
    env["TMDB_API_KEY"] = key
    try:
        proc = subprocess.run(
            ["uv", "run", "skive", "rip", "tv", "--verified"],
            input=ANSWERS, capture_output=True, text=True,
            env=env, cwd=REPO, timeout=300)
        out = proc.stdout + "\n--- stderr ---\n" + proc.stderr
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace") + \
              "\n--- stderr ---\n" + (e.stderr or b"").decode(errors="replace") + \
              "\nREPLAY TIMED OUT"
        rc = None
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(out, encoding="utf-8")
    print(f"replay output saved to {log_path}")

    check("run quit at the confirm prompt (rc=1, nothing ripped)",
          rc == 1 and "quit — nothing ripped" in out, f"rc={rc}")
    last_header = out.rfind(SEASON_HEADER)
    last_ok = out.rfind("ok? (Enter=rip")
    check("confirm table printed under the season header",
          last_header != -1 and last_ok != -1 and last_header < last_ok,
          "season header above the final ok? prompt"
          if last_header != -1 and last_ok != -1
          else "season header or ok? prompt missing")
    caption_at = out.find(CAPTION)
    check("play-all-only caption names the lone title and its episode range",
          caption_at != -1, CAPTION if caption_at != -1 else "caption missing")
    check("caption sits under the confirm table",
          caption_at != -1 and last_header < caption_at < last_ok)
    if rc is None:
        keep = {log_path.resolve(), *log_path.resolve().parents}
        after = set(mount.rglob("*"))
        _cleanup_new(mount, before, after, keep)
        return _report(checks)

    after = set(mount.rglob("*"))
    scan_dumps = [p for p in after - before
                  if p.parent.name == "scan" and p.suffix == ".txt"]
    check("the scanner replayed the dump and persisted its scan dump",
          bool(scan_dumps), str(scan_dumps[0]) if scan_dumps else "no scan dump written")
    mkvs = list((mount / "rip").rglob("*.mkv")) if (mount / "rip").exists() else []
    check("nothing ripped — no .mkv anywhere under the temp mount", not mkvs,
          f"{len(mkvs)} mkv file(s)")

    # The replay log itself (often under the same tmpfs mount) must survive
    # the cleanup — exempt it and its parent chain.
    keep = {log_path.resolve(), *log_path.resolve().parents}
    _cleanup_new(mount, before, after, keep)
    left = {p for p in set(mount.rglob("*")) - before if p not in keep}
    check("temp mount cleaned back to its prior state", not left,
          f"left behind: {sorted(left)}" if left else "replay artifacts removed")
    return _report(checks)


def _cleanup_new(root: Path, before: set[Path], after: set[Path],
                 keep: set[Path] = frozenset()) -> None:
    """Remove exactly what the replay created under the mount — deepest first,
    files before dirs; anything that pre-existed (or is exempted via keep)
    stays untouched."""
    new = sorted((after - before) - keep, key=lambda p: len(p.parts), reverse=True)
    for p in new:
        if p.is_file() or p.is_symlink():
            p.unlink(missing_ok=True)
    for p in new:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Real-file sanity + dump-replay verification for the "
                    "chapter split (ADR 0009)")
    parser.add_argument("--replay", action="store_true",
                        help="replay the captured Downton scan dump through the "
                             "real `skive rip tv` instead of the real-file cut")
    parser.add_argument("replay_log", nargs="?", default=None,
                        help=f"replay output log (default {DEFAULT_REPLAY_LOG})")
    parser.add_argument("--dump", default=str(DUMP),
                        help=f"captured scan dump to replay (default {DUMP})")
    args = parser.parse_args()
    if args.replay:
        log = Path(args.replay_log) if args.replay_log else DEFAULT_REPLAY_LOG
        return 0 if verify_dump_replay(log, Path(args.dump)) else 1
    return 0 if verify_real_file_cut() else 1


if __name__ == "__main__":
    sys.exit(main())
