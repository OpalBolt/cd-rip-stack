# Sidecar subtitles, never embedded

Main features and TV episodes carry no embedded subtitle tracks in the library — their subtitles are `.srt` sidecars named `<base>.<lang>[.forced|.sdh].srt`, extracted and OCR'd from the staged lossless MKV at transcode time. Embedded bitmap tracks force Jellyfin to burn them in and transcode the whole video; text sidecars play direct. Extras deliberately keep their subs embedded — they are rarely watched, not worth OCR CPU. `rip-DONE` is purged, so the sidecars are the long-term subtitle store; until they are verified, nothing downstream deletes the lossless source.

## Considered Options

- Keep subs embedded and let Jellyfin burn them — rejected: server transcodes on every subtitled play.
- Embed OCR'd text tracks instead of sidecars — rejected: a remux per encode, and sidecars are editable/replaceable without touching video files.
- Sidecars for extras too — rejected by Mads: OCR cost for content nobody watches with subs.
