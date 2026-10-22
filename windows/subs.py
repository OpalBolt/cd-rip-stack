#!/usr/bin/env python3
"""Shared subtitle pass — extract + OCR embedded bitmap subs into SRT sidecars.

Library encodes must ship with zero subtitle tracks: bitmap VobSub/PGS forces
Jellyfin to transcode every subtitled play. This module holds the whole
subtitle-pass policy (eng/dan only, forced always kept, per-language full-track
dedupe by OCR cue count -> `.srt` / `.sdh`) and the tool invocations (ffprobe
inventory, seconv OCR). Consumers: windows/transcode.py (`import subs`, script
dir on sys.path) and scripts/backfill.py (Linux, import by path). Stdlib only —
runs on a clean Windows python.exe.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

_LANGS = ("eng", "dan")
# second's events * 10 >= winner's * 3  <=>  >= 30% (integer math, no float traps)
_SDH_NUM, _SDH_DEN = 3, 10


@dataclass
class Track:
    index: int  # MKV/ffprobe stream index (the number seconv --track-number takes)
    lang: str  # three-letter code as ffprobe reports it ("eng", "dan", "und", ...)
    codec: str  # ffprobe codec_name ("hdmv_pgs_subtitle", "dvd_subtitle", ...)
    forced: bool  # disposition.forced


@dataclass
class Keep:
    track: Track
    flag: str  # "" | "forced" | "sdh" — final sidecar suffix


@dataclass
class PassResult:
    ok: bool  # False if any selected track failed to convert or produced an empty sidecar
    sidecars: list[str]  # final sidecar file names written to out_dir
    notes: list[str]  # human-readable report lines (skipped langs, dropped dupes, failures)


def sidecar_name(base: str, lang: str, flag: str) -> str:
    return f"{base}.{lang}" + (f".{flag}" if flag else "") + ".srt"


def decide(tracks: list[Track]) -> tuple[list[Keep], list[str]]:
    """Keep eng/dan: forced always (flag "forced"), full tracks provisionally
    (flag ""). Everything else is skipped, one note per language."""
    keeps: list[Keep] = []
    skipped: dict[str, list[int]] = {}
    for t in tracks:
        if t.lang not in _LANGS:
            skipped.setdefault(t.lang, []).append(t.index)
        else:
            keeps.append(Keep(t, "forced" if t.forced else ""))
    notes = [
        f"skip {lang} (tracks {','.join(map(str, idxs))}): not eng/dan"
        for lang, idxs in skipped.items()
    ]
    return keeps, notes


def settle(per_language: list[tuple[Track, int]]) -> tuple[list[Keep], list[str]]:
    """Pick one language's full-track sidecars by OCR'd cue count: the track
    with more events wins (flag ""), a runner-up with >= 30% of the winner's
    count is presumed the SDH duplicate (flag "sdh"), anything less is dropped
    and reported. Equal counts: lower stream index wins."""
    notes: list[str] = []
    if not per_language:
        return [], notes
    ranked = sorted(per_language, key=lambda tn: (-tn[1], tn[0].index))
    win, wn = ranked[0]
    keeps = [Keep(win, "")]
    sdh: Track | None = None
    for t, n in ranked[1:]:
        if sdh is None and n * _SDH_DEN >= wn * _SDH_NUM:
            keeps.append(Keep(t, "sdh"))
            sdh = t
        elif sdh is None:
            notes.append(f"drop {t.lang} track {t.index} ({n} events): "
                         f"below 30% of track {win.index} ({wn} events)")
        else:
            notes.append(f"drop {t.lang} track {t.index} ({n} events): "
                         f"sdh taken by track {sdh.index}")
    return keeps, notes


def inventory(mkv: str, ffprobe_exe: str) -> list[Track]:
    """ffprobe the file's subtitle streams: index, codec, language tag, forced."""
    r = subprocess.run(
        [ffprobe_exe, "-v", "error", "-select_streams", "s",
         "-show_entries", "stream=index,codec_name:stream_tags=language:stream_disposition=forced",
         "-of", "json", mkv],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"ffprobe failed on {mkv}: {r.stderr.strip()}", file=sys.stderr)
        return []
    return [
        Track(
            index=int(s["index"]),
            lang=(s.get("tags") or {}).get("language") or "und",
            codec=s.get("codec_name") or "",
            forced=bool((s.get("disposition") or {}).get("forced")),
        )
        for s in json.loads(r.stdout).get("streams", [])
    ]


def count_events(srt_path: str) -> int:
    """Subtitle cues = SRT timestamp lines (`00:00:01,000 --> ...`)."""
    with open(srt_path, encoding="utf-8", errors="replace") as fh:
        return sum(1 for line in fh if " --> " in line)


def convert(mkv: str, track: Track, seconv_exe: str, out_dir: str) -> str | None:
    """OCR one track into a FRESH temp subdir of out_dir (seconv names its
    output `<stem>.<lang>[.forced].srt`, so same-language tracks would collide
    in one dir). Returns the produced SRT path, or None (stderr note) on
    nonzero returncode / no file. Caller removes the temp dirs."""
    work = tempfile.mkdtemp(dir=out_dir, prefix=f"track{track.index}-")
    r = subprocess.run(
        [seconv_exe, mkv, "subrip",
         f"--track-number:{track.index}", f"--ocr-language:{track.lang}",
         f"--output-folder:{work}"],
        capture_output=True, text=True,
    )
    produced = sorted(f for f in os.listdir(work) if f.endswith(".srt"))
    if r.returncode != 0 or not produced:
        print(f"seconv failed: track {track.index} ({track.lang}) in {mkv}, "
              f"rc={r.returncode}\n{r.stderr.strip()}", file=sys.stderr)
        return None
    return os.path.join(work, produced[0])


def run_pass(mkv: str, out_dir: str, base: str, seconv_exe: str,
             ffprobe_exe: str) -> PassResult:
    """The whole pass, orchestration only: inventory -> decide -> OCR each keep
    in its own temp dir -> settle full tracks per language -> move winners into
    out_dir under their final sidecar names. ok=False if any selected track
    failed; partial success still moves the good sidecars."""
    notes: list[str] = []
    tracks = inventory(mkv, ffprobe_exe)
    keeps, skip_notes = decide(tracks)
    notes += skip_notes
    print(f"subtitle pass: {len(tracks)} subtitle track(s), {len(keeps)} kept: "
          f"{os.path.basename(mkv)}")
    if not keeps:
        return PassResult(True, [], notes)

    tmp_root = tempfile.mkdtemp(prefix="subs-pass-")
    ok = True
    sidecars: list[str] = []
    try:
        os.makedirs(out_dir, exist_ok=True)
        done: list[tuple[Keep, str, int]] = []  # keep, produced srt, cue count
        for k in keeps:
            print(f"  OCR {k.track.lang} track {k.track.index}"
                  f"{' (forced)' if k.track.forced else ''}...")
            srt = convert(mkv, k.track, seconv_exe, tmp_root)
            if srt is None:
                ok = False
                notes.append(f"FAIL {k.track.lang} (track {k.track.index}): seconv failed")
                continue
            n = count_events(srt)
            if n == 0:
                ok = False
                notes.append(f"FAIL {k.track.lang} (track {k.track.index}): empty sidecar (0 events)")
                continue
            done.append((k, srt, n))

        # forced keeps keep their flag; full tracks settle per language
        full_src = {k.track.index: srt for k, srt, _n in done if not k.track.forced}
        finals: list[tuple[Keep, str]] = [
            (Keep(k.track, "forced"), srt) for k, srt, _n in done if k.track.forced
        ]
        by_lang: dict[str, list[tuple[Track, int]]] = {}
        for k, _srt, n in done:
            if not k.track.forced:
                by_lang.setdefault(k.track.lang, []).append((k.track, n))
        for cands in by_lang.values():
            settled, settle_notes = settle(cands)
            notes += settle_notes
            finals += [(k2, full_src[k2.track.index]) for k2 in settled]

        # same final name = same content class, so overwriting is fine
        for k, srt in finals:
            dest = os.path.join(out_dir, sidecar_name(base, k.track.lang, k.flag))
            shutil.move(srt, dest)
            sidecars.append(os.path.basename(dest))
        for name in sidecars:
            print(f"  wrote {name}")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    return PassResult(ok, sidecars, notes)
