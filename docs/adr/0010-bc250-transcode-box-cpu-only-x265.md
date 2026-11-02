# Transcode moves to the BC-250 box: CPU-only x265, HandBrake in a distrobox

The Windows transcode machine retires. Its replacement is an AMD BC-250 — the
PS5-derived Cyan Skillfish board (Zen 2, 8 cores unlocked, 16 GB GDDR6) already
running Bazzite. That silicon has no usable video engine: the VCN block is
factory-unprovisioned and eFused off, and Sony blocks the firmware, so hardware
encode/decode cannot exist on this machine under any tool. It would not help
anyway: HandBrake on Linux has no VAAPI encoder at all (the VAAPI branch is a
long-running unmerged PR; the VCN/AMF path needs proprietary amdgpu-pro, which
an immutable OS cannot host). The community `bc250-vaapi` shim was evaluated
and rejected for this pipeline: its H.264 path is libx264 on the CPU behind a
VAAPI costume, and its HEVC path is a slice-parallel, CBR-oriented streaming
encoder built for Sunshine/Moonlight — the wrong shape for quality-based
archival encodes.

So the encoder stays what the library was built with: HandBrake, x265, CPU
only, the GPU idle during transcode. The transcode becomes `skive transcode` —
the standalone-by-design `windows/transcode.py` (which stays in the repo,
untouched, for the retired box) gets ported into skive so policy code (run log,
interrupt, validation.json) is shared instead of duplicated. On the box, skive
runs inside one distrobox carrying the system tools HandBrake (RPM Fusion),
mkvmerge and tesseract with eng/dan; mise already provides python, uv, seconv
and ffmpeg from the repo. The NAS videos share mounts at `/mnt/videos`, same
as the rip box; runs are invoked by hand, same operational shape as before.

The switch is gated by a benchmark on real discs: quality identical to the
naked eye and output size within ~10% of the Windows box encoding the same
disc, with the BC-250 allowed to be a little slower. Overnight-per-title is
explicitly not acceptable — the staged backlog must drain in reasonable time,
not weeks. What happens if the benchmark misses the bar is deliberately
unplanned; that bridge gets built when reached.

## Consequences

- No hardware encode path exists on this box, ever. A future hardware-encode
  requirement means different hardware, not different software.
- HandBrake's GPU encoder flags (`nvenc`/`qsv`/`vce`) are dead weight on this
  machine; only `x265` can run there.
- The vaapi shim remains uninstalled for the transcode path; if the box ever
  serves as a Sunshine/Moonlight game streamer, that is its home turf — a
  separate concern from this pipeline.
- `windows/transcode.py` and its tests stay as the retired box's frozen
  artifact; new transcode work happens only in skive.
