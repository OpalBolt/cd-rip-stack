# Changelog

Notable user-visible changes to skive. Entries are added by the AI under
`## Unreleased` as part of every `feat`/`fix` PR; the approver verifies the
entry exists before merging. At release time, Unreleased entries move under a
dated heading.

## Unreleased

- fix: a disc with several forced subtitle tracks in one language (e.g. two
  English forced tracks) shipped duplicate sidecars with the same name; now
  the track with the most cues wins and only one sidecar is written.
- backfill: OCR now runs on a local copy of each MKV instead of reading the
  NFS file many times, and a new `-j N` option processes N files in parallel;
  an unexpected error on one file no longer aborts the whole library run.
