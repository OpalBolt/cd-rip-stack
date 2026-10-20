# Fast scan is the default gate; the full MakeMKV scan escalates on ambiguity

`rip_preamble` ran a full `makemkvcon info` (minutes) before any prompt, so every rip waited on the scan. We replace the gate with a fast read — mounted `lsdvd` (DVD) or `ffprobe bluray:` (BD) — yielding titles as durations in seconds. The full scan is demoted to an escalation, fired synchronously only when the fast scan finds two titles within ±1s (duplicate/decoy/angle playlists) or a movie `-T` menu is requested.

## Consequences

The fast path yields scanner title numbers that don't match MakeMKV's, so TV rips `all` and joins staged `_tNN` files to scanner titles by duration (±1s) before renaming. The escalation path keeps MakeMKV numbers and rips per-title with no join. Media type, volume label, and no-disc detection come from an early-killed `makemkvcon` DRV-line sniff, so the existing DRV parsers (`_parse_media`, `_parse_label`) stay in place unchanged.
