# cd-ripping on Windows

Windows is a **transcode box, nothing else**. Ripping always happens on the Linux machine (`rip-video` stages into `rip/` on the NAS) — it will never be done from Windows, and no rip tooling exists here.

This is a standalone port of `transcode-video`: one PowerShell script, no Nix, no repo checkout, nothing we install or manage. You set the machine up yourself — this page only tells you what is needed.

## Requirements

Install these yourself; the script assumes they are present and installs nothing:

- Windows 10/11 — PowerShell 5.1 ships with it, nothing extra.
- **HandBrake** — the GUI installer from [handbrake.fr](https://handbrake.fr). The script looks for `HandBrakeCLI.exe` on PATH, then in the HandBrake install folder (`C:\Program Files\HandBrake\`). If your build does not ship it, download the Windows CLI package from the HandBrake downloads page and put `HandBrakeCLI.exe` in a folder on PATH.
- **MKVToolNix** — installer from [mkvtoolnix.download](https://mkvtoolnix.download). Only needed for films ripped in parts (the `mkvmerge` step). The script finds `mkvmerge.exe` on PATH or in the install folder.
- The NAS videos share reachable from Windows (see next section).

## Point VIDEOS at the share

Map the share first. SMB (any Windows edition):

```powershell
net use Z: \\nas\videos /persistent:yes
```

Windows Pro/Enterprise can also mount NFS directly: `mount -o anon nas:/export/videos Z:`. Either way, the script only needs a path it can read and write.

With the mapping above you're done — the script defaults to `Z:\`. Only if you use
a different root, set `VIDEOS` permanently (new terminals pick it up):

```powershell
[Environment]::SetEnvironmentVariable('VIDEOS', '\\nas\videos', 'User')
```

**SSH sessions don't see the mapping.** Drive letters are per logon session, so
a shell opened over OpenSSH doesn't inherit the desktop's `Z:` (`cd Z:\` fails
even though Explorer shows it). Per SSH session, either map it again:

```powershell
net use Z: \\nas\videos   # add /user:nas\you if it asks for credentials
```

or point `VIDEOS` at the UNC path — and note `cd \\nas\videos\rip` works in
PowerShell but not cmd (there use `pushd`).

Layout under it is the same as on Linux:

```text
Z:\rip\       lossless staging: <Movie (Year)>_tNN.mkv, _tNN.pN.mkv (split-film half), _eNN[.pN].mkv (extras)
Z:\library\   point Jellyfin here: <Movie (Year)>\<Movie (Year)>.mkv + extras\
Z:\rip-DONE\  where -Keep parks lossless sources after encoding (verify, then delete)
```

Optional: `VIDEO_WORK_DIR` — local scratch where lossless files are copied for encoding. Default `%TEMP%\video-work`, which sits on `C:`. A Blu-ray remux runs 30–50 GB, so point it at a drive with room:

```powershell
[Environment]::SetEnvironmentVariable('VIDEO_WORK_DIR', 'D:\video-work', 'User')
```

## Run it

Copy `windows\transcode-video.ps1` anywhere you like (it is standalone) and open PowerShell there:

```powershell
.\transcode-video.ps1                  # transcode everything staged in rip\
.\transcode-video.ps1 "X2 (2003)"      # one movie by staged-name prefix
.\transcode-video.ps1 "The Godfather"  # prefix matches all three
.\transcode-video.ps1 -Keep            # park sources in rip-DONE instead of deleting
.\transcode-video.ps1 -Encoder nvenc   # use the GPU (see below)
Get-Help .\transcode-video.ps1         # full usage
```

### GPU encoding

By default the script encodes with software x265 — the same encoder and quality
as the Linux transcode machine, so encodes are comparable no matter which box
ran them.

If the Windows machine has a GPU, opt in with `-Encoder`:

- `-Encoder nvenc` — NVIDIA
- `-Encoder qsv` — Intel Quick Sync
- `-Encoder vce` — AMD

GPU encoding is roughly 5–10x faster but slightly worse quality per bit (bigger
files at the same quality). Quality is fixed at 28 to match the software
preset's target. If the card/driver can't do it, HandBrake errors, the movie is
logged `FAILED`, and its lossless source is left in place — nothing is lost.

If Windows refuses to run the script ("running scripts is disabled"), allow local scripts once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

or run it without changing policy: `powershell -ExecutionPolicy Bypass -File .\transcode-video.ps1`.

## Behavior

Same as the Linux version (see the main README for the full story):

- Junk-labelled files — a rip still in progress, or one whose rename failed (no `(Year)` in the name) — are always skipped, so a running rip and the transcoder never race.
- A film ripped in parts (`_tNN.pN.mkv`) has its main feature merged into one movie with `mkvmerge`; extras stay separate and keep their `.pN` suffix.
- The transcode is a pass-through: every audio and subtitle track kept soft, video only re-encoded to 1080p H.265.
- On success the lossless source is deleted (or moved to `rip-DONE` with `-Keep`); on failure it is left in place so a re-run retries.
- Each run appends to `.\rip.log` in the directory you run it from, same line format as the Linux scripts. The log is per machine.

## Troubleshooting

- `HandBrakeCLI.exe not found` / `mkvmerge.exe not found` — the message shows where it looked. Either add the folder to PATH, or edit nothing and rely on the default install dirs the script probes.
- Slow encodes over the network — expected: the script pulls each file to local temp first, so encoding is local, but the initial copy and final upload cross the network. Wired gigabit makes both painless.
- A stale `.merged.mkv`/`.h265.mkv` left in `VIDEO_WORK_DIR` after a crash — safe to delete; a re-run recreates and overwrites it.
