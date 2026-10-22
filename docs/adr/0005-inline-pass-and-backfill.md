# Inline subtitle pass at transcode; platform-independent backfill on Linux

New rips get their subtitle pass inside the transcode run itself — extraction, OCR, naming, then the encode ships with no subtitle tracks. No "run something later" step exists to forget while batching tens of films. The existing library is covered by a one-off, stdlib-only `scripts/backfill.py` (no skive imports), which runs on Linux because the work is CPU-bound, not GPU-bound, and the Linux box is idle. Both paths share the same engine (ADR-0004) and the same keep policy; the Windows box gains seconv + Tesseract to its install spec, nothing from nixpkgs.

## Consequences

- `windows/transcode.py` owns the inline pass for new rips: HandBrake drops subtitle tracks (`--subtitle-lang-list` with a language no track matches) for features/TV, keeps them embedded for extras.
- `scripts/backfill.py` walks `library/`, skips `extras/`, runs the pass, then strips embedded tracks from features/TV only.
