# Default movie rips take only the main feature; extras rip on request

Every `rip movie` run ripped every title at or above `RIP_MINLENGTH` — the arbitrated main plus every bonus feature — and transcode then deleted (or `-k`-parked) the unmarked extras alongside the main's source disposal. The extras never encoded, so their rip time (tens of GB on some Blu-rays), their staging space, and their disposal were pure waste; the only value was the deferred option to flip the extras marker later without re-inserting the disc.

The default inverts: a plain `rip movie` rips the arbitrated main alone. Extras reach staging only when asked for — `-i` rips them and marks them keepers (they encode at superfast), `-E` covers bonus discs, `-T` covers cherry-picks. TV is unchanged: its eligibility floor already drops short titles, and extras only enter through a fix row or `-E`.

Selecting one title needs MakeMKV's real title numbers, so the default movie run now takes the full `makemkvcon info` scan (~1–3 min) instead of the fast read; the fast path remains for TV and movie `-i` rip-all runs. With no candidate main on the disc (shorts, bonus-shaped discs) the biggest title becomes the main and rips alone, mirroring transcode's biggest-bytes fallback — a main-only run must name a title.

## Consequences

The pre-rip gate prints one info line naming the rip set (`rip set: t21 only — extras not ripped`) before the confirm, and the run log records it under phase "titles". A later change of heart about a specific extra means re-inserting the disc (with `-i`, `-E`, or `-T`) — the capture-everything optionality is traded for the time and space asked back. The validation record improves for shorts: the `main` field names the actual title instead of none. Transcode is untouched: the unmarked-extras branch stays for `-E`/`-T`/legacy staging, and `backlog-mark.py`, `sync-marks.py`, and `backlog-delete-unmarked.py` still clean up the rips already staged or parked.
