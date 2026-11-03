# Roadmap

Deferred work, with the reason each item waits.

## Music

- **Music library checker** (`skive validate` for music): AccurateRip mismatches, missing tracks, missing validation files, lossy albums. Unblocked — album records are born complete at filing since the rip-music work; it waits only for a session to build it.
- **Download staging command**: `skive add bandcamp|other <zip-or-dir>` to unzip or copy downloads into `rip/<music source>/`. For now you unzip by hand. Build it if that gets annoying.
- **Rip preflight for CDs**: check the library for the album before cyanrip spins. cyanrip learns the release only partway through the run, and beets' duplicate prompt catches the duplicate at filing anyway. Skipping the check costs about five minutes per duplicate rip.
- **Lossy policy**: convert or refuse lossy files from `other`. Today they are filed as they are.
- **Interrupt-safe filing**: copy, verify, then delete instead of beets' move. Today beets' `resume: ask` finishes a half-moved album.

## Code

- **Delete the retired Windows module**: remove `windows/` (`transcode.py`,
  `subs.py`) and its test `tests/test_windows_transcode.py`. Retired per
  ADR 0010; kept only until the docs remake landed.
