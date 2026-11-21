# Changelog

Notable user-visible changes to skive. Entries are added by the AI under
`## Unreleased` as part of every `feat`/`fix` PR; the approver verifies the
entry exists before merging. At release time, Unreleased entries move under a
dated heading.

## Unreleased

- fix: the box starts on drive-less machines (the transcode box) — optical
  device nodes moved to `compose.rip.yaml`, which the `./skive` wrapper adds
  only when `/dev/sr*` exists on the host (a device compose lists must exist
  at container create), and `skive doctor` skips the drive, makemkvcon, and
  key checks as warns on a box with no optical nodes instead of failing them
- feat: the skive box — one Fedora container (Containerfile + compose)
  driven through a `./skive` wrapper replaces the distrobox setup script
  and the Arch host install path; clone, build, `./skive doctor` is now the
  one setup path on any machine.
- feat: a `MAKEMKV_KEY` declared in the environment self-heals at startup —
  skive writes it into MakeMKV's settings.conf (idempotently; the env key
  always wins), so key rotation is a one-line `.env` edit. With no env key,
  the remind-only behavior stands.
- feat: `skive doctor` checks the machine in one green table — drives, share
  mounts, MakeMKV key, tesseract languages, encode capability — and exits
  nonzero when a prerequisite is missing; it checks, never fixes.
- fix: a disc with several forced subtitle tracks in one language (e.g. two
  English forced tracks) shipped duplicate sidecars with the same name; now
  the track with the most cues wins and only one sidecar is written.
- backfill: OCR now runs on a local copy of each MKV instead of reading the
  NFS file many times, and a new `-j N` option processes N files in parallel;
  an unexpected error on one file no longer aborts the whole library run.
