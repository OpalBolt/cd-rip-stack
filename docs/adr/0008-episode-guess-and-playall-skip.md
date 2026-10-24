# Durations prefill the range prompt (episode guess); play-alls are never ripped

The range prompt asked for the episode count blind — the confirm table only appeared after the answer — and its prefill counted eligible titles, which is right only when play-all detection and the floor already excluded every non-episode. Two changes, one rationale: TMDB runtimes are trusted enough to *suggest*, never to assign.

First, the episode guess. Before the prompt, a title overview prints (duration, size, audit ratio, estimated assignments marked `(est.)`, play-alls marked not ripped, no-fit titles marked extra), and the prefill comes from a greedy walk instead of a title count: from the first unstaged episode (discs are ripped in order, so staged episodes are a contiguous prefix), each episode claims the first remaining eligible title whose disc÷TMDB ratio is green (~0.92–1.04); the walk stops at the first episode with no fitting title or no TMDB runtime. Without runtimes or matches the prefill falls back to the old count-based logic, so season 0 degrades gracefully.

Second, play-alls are not ripped at all. Fast-scan detection (the existing ±120s subset sum) escalates to the full MakeMKV scan (~1–3 min), whose real title numbers let the rip select around the play-all — previously the drive read the whole compilation (~15–25 min on a DVD like Downton Abbey S1D1) only for the bytes to be filed as a redundant extra.

ADR 0001 stands: the guess produces the range `(lo, hi)` only, assignments remain stated-range + disc position, durations grade but never decide. ADR 0002's trigger list gains the play-all arm.

## Accepted limits

- A making-of above the floor that *precedes* the episodes and happens to fit an episode's runtime gets consumed by the walk — the count usually still lands, and the confirm table remains the check. Uniform-length seasons stay blind (the Generation Kill lesson).
- A wrong or missing TMDB runtime truncates the guess early; the human states the range exactly as before. The guess shortens work, it never silently decides.
- A false-positive play-all is rescuable: a fix row assigning it an episode includes it in the rip selection (skip set = play-alls whose final assignment is extra).

## Consequences

- Most TV discs carry play-alls, so TV mostly rides the full-scan path; movies stay fast. Escalated TV rips rename by MakeMKV numbers directly (the existing full-scan path, no duration join).
- `--extras` mode (no season, no table, no guess) is untouched — play-alls still rip there.
