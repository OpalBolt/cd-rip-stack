#!/usr/bin/env bash
# end-to-end test of windows/transcode-video.ps1 under pwsh with stub tools.
# needs pwsh (any 7.x; PS 5.1 is the real target — see README_WINDOWS.md).
# run: bash windows/test-transcode.sh
set -u
T=${T:-/tmp/wintest}
REPO=$(cd "$(dirname "$0")/.." && pwd)
PW=${PW:-$(command -v pwsh || echo /tmp/pwsh/pwsh)}
PS1_SCRIPT="$REPO/windows/transcode-video.ps1"
rm -rf "$T"; mkdir -p "$T"/{bin,vid/rip,work}
cd "$T"

cat > bin/HandBrakeCLI.exe <<'EOF'
#!/bin/sh
{ printf 'ARGS:'; printf ' %s' "$@"; echo; } | tee -a "$STUB_LOG"
in=""; out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -i) in="$2"; shift 2 ;;
    -o) out="$2"; shift 2 ;;
    *)  shift ;;
  esac
done
if grep -q FAILME "$in" 2>/dev/null; then exit 3; fi
cat "$in" > "$out"
EOF
cat > bin/mkvmerge.exe <<'EOF'
#!/bin/sh
out="$2"; shift 2
: > "$out"
for a in "$@"; do
  [ "$a" = "+" ] && continue
  if grep -q BADMERGE "$a" 2>/dev/null; then rm -f "$out"; exit 2; fi
  cat "$a" >> "$out"
done
EOF
chmod +x bin/*.exe

mk() { mkdir -p "$(dirname "$1")"; printf '%s' "$2" > "$1"; }

mk "vid/rip/Foo (2001)_t00.mkv"  'FOO-MAIN-BYTES-BYTES-BYTES'   # must outweigh the t01 extra
mk "vid/rip/Foo (2001)_t01.mkv"  'FOO-EXTRA'
mk "vid/rip/Bar (2002)_t00.mkv"  'BAR-DECOY'
mk "vid/rip/Bar (2002)_t01.p1.mkv" 'BAR-P1-BYTES-BYTES-BYTES-BYTES'
mk "vid/rip/Bar (2002)_t01.p2.mkv" 'BAR-P2-BYTES-BYTES-BYTES-BYTES-BYTES-BYTES'
mk "vid/rip/Bar (2002)_t02.mkv"   'BAR-EXTRA'
mk "vid/rip/Baz Extras (2003)_e00.mkv" 'BAZ-E0'
mk "vid/rip/Baz Extras (2003)_e02.mkv" 'BAZ-E2'
mk "vid/rip/B1_t00.mkv"           'JUNK-MIDRIP'
mk "vid/rip/Failme (1999)_t00.mkv" 'FAILME'
mk "vid/rip/Qux (2004)_t00.mkv"   'QUX-MAIN'

export STUB_LOG="$T/hb-calls-1.log"
export VIDEOS="$T/vid" VIDEO_WORK_DIR="$T/work"
mkdir run; cd run
PATH="$T/bin:$PATH" "$PW" -NoProfile -File "$PS1_SCRIPT" > out1.txt 2> err1.txt
rc1=$?

fail() { echo "ASSERT FAIL: $*"; echo "--- rc=$rc1"; cat out1.txt; echo "--- stderr:"; cat err1.txt; exit 1; }
[ "$rc1" = 1 ] || fail "scenario1 exit code, want 1 (Failme), got $rc1"

L="$T/vid/library"
[ "$(cat "$L/Bar (2002)/Bar (2002).mkv")" = 'BAR-P1-BYTES-BYTES-BYTES-BYTESBAR-P2-BYTES-BYTES-BYTES-BYTES-BYTES-BYTES' ] || fail "Bar merged main content/order"
[ "$(cat "$L/Foo (2001)/Foo (2001).mkv")" = 'FOO-MAIN-BYTES-BYTES-BYTES' ] || fail "Foo main"
[ "$(cat "$L/Foo (2001)/extras/Foo (2001)_t01.mkv")" = 'FOO-EXTRA' ] || fail "Foo extra"
[ "$(cat "$L/Bar (2002)/extras/Bar (2002)_t00.mkv")" = 'BAR-DECOY' ] || fail "Bar decoy filed as extra"
[ "$(cat "$L/Bar (2002)/extras/Bar (2002)_t02.mkv")" = 'BAR-EXTRA' ] || fail "Bar extra"
[ "$(cat "$L/Baz Extras (2003)/extras/Baz Extras (2003)_e00.mkv")" = 'BAZ-E0' ] || fail "Baz e0"
[ "$(cat "$L/Baz Extras (2003)/extras/Baz Extras (2003)_e02.mkv")" = 'BAZ-E2' ] || fail "Baz e2"
[ "$(cat "$L/Qux (2004)/Qux (2004).mkv")" = 'QUX-MAIN' ] || fail "Qux main"
[ "$(ls "$T/vid/rip" | sort | tr '\n' ' ')" = 'B1_t00.mkv Failme (1999)_t00.mkv ' ] || fail "rip/ leftovers: $(ls vid/rip)"
grep -q 'skip (junk label' err1.txt || fail "junk skip warning"
grep -q 'FAILED (handbrake)' err1.txt || fail "handbrake failure warning"
grep -q '  total  ' rip.log || fail "FAIL total logged"
grep -q -- ' --preset H.265 MKV 1080p30 ' "$STUB_LOG" || fail "preset flag: $(head -2 "$STUB_LOG")"
grep -q -- " --audio-copy-mask aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac,pcm " "$STUB_LOG" || fail "audio-copy-mask must stay one comma-joined argument"
grep -q -- '--subtitle-burned=none' "$STUB_LOG" || fail "subtitle-burned flag"
grep -q -- '--audio-fallback ac3' "$STUB_LOG" || fail "audio-fallback flag"
grep -q -- '--aencoder copy' "$STUB_LOG" || fail "aencoder flag"
[ "$(grep -c . "$STUB_LOG")" = 9 ] || fail "9 encode calls expected (incl. failed Failme), got $(grep -c . "$STUB_LOG")"
echo "scenario 1 OK ($(wc -l < rip.log) log lines)"

cd "$T"; rm -rf run2 vid; mkdir -p run2 vid/rip
mk "$T/vid/rip/Keep (2005)_t00.mkv" 'KEEP-MAIN'
mk "$T/vid/rip/Keep (2005)_e01.p2.mkv" 'KEEP-EXTRA-P2'
export STUB_LOG="$T/hb-calls-2.log"
cd run2
PATH="$T/bin:$PATH" "$PW" -NoProfile -File "$PS1_SCRIPT" -Keep > out2.txt 2>&1 || { echo "ASSERT FAIL: -Keep exit"; cat out2.txt; exit 1; }
[ "$(ls "$T/vid/rip")" = "" ] || fail "rip emptied with -Keep: $(ls "$T/vid/rip")"
[ "$(ls "$T/vid/rip-DONE" | sort | tr '\n' ' ')" = 'Keep (2005)_e01.p2.mkv Keep (2005)_t00.mkv ' ] || fail "rip-DONE contents"
[ "$(cat "$T/vid/library/Keep (2005)/extras/Keep (2005)_e01.p2.mkv")" = 'KEEP-EXTRA-P2' ] || fail "extra keeps .p2 suffix"
grep -q -- '--keep: lossless sources moved' out2.txt || fail "-Keep end note"
echo "scenario 2 OK"

# scenario 3: single-title movie -> prefix run is ONE phase -> no total line;
# a no-match prefix exits 1 with a warning.
cd "$T"; rm -rf run3; mkdir -p run3 vid/rip
mk "$T/vid/rip/Solo (2006)_t00.mkv" 'SOLO-MAIN'
export STUB_LOG="$T/hb-calls-3.log"
cd run3
PATH="$T/bin:$PATH" "$PW" -NoProfile -File "$PS1_SCRIPT" 'Solo (2006)' > out3.txt 2>&1 || { echo "ASSERT FAIL: prefix run"; cat out3.txt; exit 1; }
[ "$(cat "$T/vid/library/Solo (2006)/Solo (2006).mkv")" = 'SOLO-MAIN' ] || fail "Solo main"
[ "$(grep -c total rip.log)" = 0 ] || fail "single-phase success must not log a total"
PATH="$T/bin:$PATH" "$PW" -NoProfile -File "$PS1_SCRIPT" 'Nope (2999)' > out4.txt 2>&1
[ "$?" = 1 ] || fail "no-match prefix must exit 1"
grep -q 'no staged MKVs match' out4.txt || fail "no-match warning"
echo "scenario 3 OK"

# scenario 4: empty rip dir -> exit 0; rip.log line shape (8 double-space columns)
cd "$T"; rm -rf run4 vid/rip/*; mkdir run4; cd run4
PATH="$T/bin:$PATH" "$PW" -NoProfile -File "$PS1_SCRIPT" > out5.txt 2>&1
[ "$?" = 0 ] || fail "empty rip exit"
grep -q 'no MKVs in' out5.txt || fail "empty rip message"
head -1 "$T/run3/rip.log" | awk -F'  ' 'NF==8 && $1 ~ /^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$/ && $7 ~ /^[0-9]+m[0-9]{2}$/ && $8=="started" {exit 0} {exit 1}' || fail "log line format: $(head -1 "$T/run3/rip.log")"
echo "scenario 4 OK"

# scenario 5: -Encoder nvenc swaps in the GPU encoder flags (default: none).
cd "$T"; rm -rf run5; mkdir -p run5 vid/rip
mk "$T/vid/rip/Gpu (2009)_t00.mkv" 'GPU-MAIN'
export STUB_LOG="$T/hb-calls-5.log"
cd run5
PATH="$T/bin:$PATH" "$PW" -NoProfile -File "$PS1_SCRIPT" -Encoder nvenc > out6.txt 2>&1 || { echo "ASSERT FAIL: gpu run"; cat out6.txt; exit 1; }
grep -q -- '--encoder nvenc_h265' "$STUB_LOG" || fail "gpu encoder flag: $(cat "$STUB_LOG")"
grep -q -- '--quality 28' "$STUB_LOG" || fail "gpu quality flag"
[ "$(cat "$T/vid/library/Gpu (2009)/Gpu (2009).mkv")" = 'GPU-MAIN' ] || fail "gpu run output"
grep -q -- '--encoder' "$T/hb-calls-3.log" && fail "default x265 run must not pass --encoder"
echo "scenario 5 OK"
# scenario 6: TV under rip/TV/ — Season NN folders, Specials for season 0,
# " - SNNENN" filing names, junk label skipped, no main-feature logic, movie
# flow untouched alongside.
cd "$T"; rm -rf run6 vid; mkdir -p run6 vid/rip/TV
mk "$T/vid/rip/TV/Show (2010)_S01E02.mkv"     'TV-E02'
mk "$T/vid/rip/TV/Show (2010)_S01E05-E06.mkv" 'TV-DOUBLE'
mk "$T/vid/rip/TV/Show (2010)_S00E01.mkv"     'TV-SPECIAL'
mk "$T/vid/rip/TV/Show (2010)_e03.mkv"        'TV-EXTRA'
mk "$T/vid/rip/TV/B1_S01E02.mkv"              'TV-JUNK'
mk "$T/vid/rip/Mov (2011)_t00.mkv"            'MOV-MAIN'
export STUB_LOG="$T/hb-calls-6.log"
cd run6
PATH="$T/bin:$PATH" "$PW" -NoProfile -File "$PS1_SCRIPT" > out7.txt 2> err6.txt
[ "$?" = 0 ] || { echo "ASSERT FAIL: tv run exit"; cat out7.txt; cat err6.txt; exit 1; }
TL="$T/vid/library/TV/Show (2010)"
[ "$(cat "$TL/Season 01/Show (2010) - S01E02.mkv")" = 'TV-E02' ] || fail "tv episode filing"
[ "$(cat "$TL/Season 01/Show (2010) - S01E05-E06.mkv")" = 'TV-DOUBLE' ] || fail "tv double filing"
[ "$(cat "$TL/Specials/Show (2010) - S00E01.mkv")" = 'TV-SPECIAL' ] || fail "specials folder"
[ "$(cat "$TL/extras/Show (2010)_e03.mkv")" = 'TV-EXTRA' ] || fail "tv extras filing"
[ "$(cat "$T/vid/library/Mov (2011)/Mov (2011).mkv")" = 'MOV-MAIN' ] || fail "movie flow beside tv"
[ "$(ls "$T/vid/rip/TV")" = 'B1_S01E02.mkv' ] || fail "rip/TV leftovers: $(ls "$T/vid/rip/TV")"
grep -q 'skip (junk label' err6.txt || fail "tv junk skip warning"
[ "$(grep -c . "$STUB_LOG")" = 5 ] || fail "5 encode calls expected (4 tv + 1 movie), got $(grep -c . "$STUB_LOG")"
echo "scenario 6 OK"

echo "ALL PASS"
