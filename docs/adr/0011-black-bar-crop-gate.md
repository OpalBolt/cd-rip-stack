# Black-bar crop: sampled detection behind a refusal-biased gate

The library is inconsistent about black bars, and neither half was decided.
The x265/HandBrake era silently auto-cropped from opening frames only: stock
presets read the head of the file and hardcode the crop, so titles landed
already cropped (720×436/434/438 on DVDs) whether or not the opening frame
represented the film. The VAAPI era (ADR 0010's `-e auto` path) kept the
bars untouched — the same film shipped both ways (The Day After Tomorrow
survives as a 704×576 VAAPI encode beside cropped x265 siblings). This ADR
replaces both silent extremes with one explicit gate on every encode path:
the VAAPI filter chain crops before the alignment crop, HandBrake gets
`--crop t:b:l:r` with the preset chosen from the cropped height.

## The gate

Black-bar detection ports the committed trial oracle (scripts/crop_detect_test.py)
verbatim into skive/transcode.py: roughly six cropdetect probes spread over
10–90% of the duration, 30 frames decoded per probe window (limit=24, round=2,
last settled frame wins). Per-side bar size is the median across probes, and
the gate constants, exactly as shipped, are:

- `MIN_PX = 16` — a per-side bar under 16 px crops as nothing (alignment noise)
- `MAX_AXIS_PCT = 30` — per-axis crop total (left+right, top+bottom) capped at
  30% of the axis
- `TOLERANCE = 4` — a probe dissents when it sits further than ±4 px from the
  side's median
- `MAX_DISSENT = 1` — at most ONE dissenting probe per side is absorbed; two
  dissents refuse the crop
- `MIN_READINGS = 3` — fewer readable probes means detection failed, no crop

Crop values are floored to even. A refusal or an unreadable detection never
fails the item: it ships uncropped with a loud note.

## Why sampled detection, not opening-frame autocrop

HandBrake's stock autocrop reads only the opening frames. A credits roll or a
dark scene at the head misreads silently and irreversibly — that is how the
x265-era encodes got the geometry they have. The trial put real numbers on the
inverse failure: The Day After Tomorrow (2004), 704×576 progressive, read a
clean 2.35:1 letterbox (0/72/0/72) on five probes, but the 90% probe sitting in
the credits region reported a 156 px PHANTOM left bar. Under the trial's
original unanimity rule that single outlier vetoed the whole crop, which was
clearly wrong — so the gate was revised mid-trial to the one-dissent budget
above. Verdict for the title: crop 0/72/0/72 → 704×432, 25.0% of pixels
dropped. Six probes across the body of the film plus a per-side median plus a
one-dissent budget absorbs exactly the kind of outlier the trial exposed,
instead of betting everything on frame one.

## Why hands-off, not per-title confirmation

Transcode runs are batch and unattended; a confirmation prompt per title breaks
the shape. The conservative caps bound the overcrop risk tightly enough to stay
automatic: a bar must be at least 16 px to count, the crop can never exceed 30%
of an axis, detection needs at least 3 readable probes, and a side with more
than one dissenting probe refuses outright. The six-movie sweep validated the
bias: 4 titles were already tight (no bar ≥ 16 px, no crop), and 2 were
correctly REFUSED on genuine scatter — 8 Mile (dark-scene phantoms) and Beauty
and the Beast (opening/credits boxes, 2 dissents each). Zero false-positive
crops across the sweep.

## The trade-off: bits now, re-rip risk forever

Cropped frames stop paying bits for black bars — about 25% of pixels on the
trial title, sizeable across a library. But the sources are stripped after
verify (ADR 0006): a bad crop means re-ripping the physical disc. That
asymmetry is why the gates are refusal-biased. Unstable or over-cap detection
ships uncropped with a loud note, never a guess; a few percent of bits spent on
bars is cheaper than a disc re-rip.

## Recording

Every item gets one timed `detect` line in the run log — applied crop or
refusal reason plus elapsed seconds, in the same shape as the copy/merge/subs/
encode lines. The validation.json encode record carries `"crop":
"l/t/r/b"|null` and `"size": "WxH"` (the final encode geometry), so the
library's crop state is auditable per title.

## Consequences

- Both silent extremes are gone: every encode path crops by the same gate or
  explicitly refuses.
- The one-dissent rule is a budget, not a license: two dissents on a side, or
  any over-cap or under-read detection, refuses — refusal is the safe default.
- Existing x265-era encodes stay as they are (already cropped); the gate
  governs new encodes only. Reconciling the old encodes is unplanned.
- scripts/crop_detect_test.py remains the acceptance oracle for the detector;
  the shipped constants must not drift from it.
