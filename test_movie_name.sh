#!/usr/bin/env bash
# self-check for movie-name: the two-tier resolution (metadata pass-through; junk
# label -> TMDB pick; no key -> fallback) plus the rip-time rename. stubs curl
# (a captured search/movie payload) and makemkvcon info so it runs with no
# network and no drive. jq must be on PATH (the devshell ships it). run: bash test_movie_name.sh
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$(mktemp -d)"

command -v jq >/dev/null 2>&1 || { echo "skip: jq not installed (run in the nix devshell)"; exit 0; }

bin=$PWD/bin; mkdir -p "$bin"
# stub curl: always emit the same captured payload, regardless of the URL.
cat > "$bin/curl" <<'EOF'
#!/usr/bin/env bash
cat <<'JSON'
{"results":[
 {"title":"Rogue One: A Star Wars Story","release_date":"2016-12-10","original_language":"en"},
 {"title":"Rogue One","release_date":"2016-12-16","original_language":"en"}
]}
JSON
EOF
chmod +x "$bin/curl"
export PATH="$bin:$PATH"

MN="$repo/movie-name.sh"
blu="$PWD/Rogue One- A Star Wars Story_t00.mkv"   # need not exist on disk

# 1. Blu-ray: real metadata name passes straight through (no prompt, no network).
out=$(bash "$MN" "$blu")
[ "$out" = $'Rogue One- A Star Wars Story\t-\t-' ] || { echo "FAIL blueray passthrough: [$out]"; exit 1; }

# 2. DVD junk label (B1) -> TMDB search -> pick -> returns Title, Year, original_language.
export TMDB_API_KEY=fake
out=$(printf 'rogue one\n1\n' | bash "$MN" "$PWD/B1_t00.mkv" 2>/dev/null)
[ "$out" = $'Rogue One: A Star Wars Story\t2016\ten' ] || { echo "FAIL tmdb pick: [$out]"; exit 1; }

# 3. no $TMDB_API_KEY -> clear warning + fallback to the label, exit 0 (rip still completes).
unset TMDB_API_KEY
err=$(bash "$MN" "$PWD/B1_t00.mkv" 2>&1 >/dev/null) || { echo "FAIL no-key should exit 0"; exit 1; }
out=$(bash "$MN" "$PWD/B1_t00.mkv" 2>/dev/null)
[ "$out" = $'B1\t-\t-' ] || { echo "FAIL no-key fallback stdout: [$out]"; exit 1; }
echo "$err" | grep -qi 'no.*TMDB_API_KEY' || { echo "FAIL no-key warning missing: [$err]"; exit 1; }

# 4. cache: an already-named "<Title> (Year)_tNN.mkv" does not re-prompt.
out=$(printf '' | bash "$MN" "$PWD/Rogue One (2016)_t00.mkv" 2>/dev/null)
[ "$out" = $'Rogue One\t2016\t-' ] || { echo "FAIL cache: [$out]"; exit 1; }

# 5. disc mode: real CINFO:2 passes through; junk CINFO:2 falls to TMDB.
export MAKEMKV_INFO="$bin/mkv-info-real"
cat > "$bin/mkv-info-real" <<'EOF'
#!/usr/bin/env bash
cat <<'INFO'
CINFO:1,6206,"Blu-ray disc"
CINFO:2,0,"Rogue One- A Star Wars Story"
INFO
EOF
chmod +x "$bin/mkv-info-real"
out=$(bash "$MN" disc:0)
[ "$out" = $'Rogue One- A Star Wars Story\t-\t-' ] || { echo "FAIL disc real: [$out]"; exit 1; }

export TMDB_API_KEY=fake
export MAKEMKV_INFO="$bin/mkv-info-junk"
cat > "$bin/mkv-info-junk" <<'EOF'
#!/usr/bin/env bash
cat <<'INFO'
CINFO:1,6206,"DVD disc"
CINFO:2,0,"DIE_HARD_4_0_SE_DISC_1"
INFO
EOF
chmod +x "$bin/mkv-info-junk"
out=$(printf 'die hard\n2\n' | bash "$MN" disc:0 2>/dev/null)
[ "$out" = $'Rogue One\t2016\ten' ] || { echo "FAIL disc junk->tmdb: [$out]"; exit 1; }

echo "PASS: movie-name (passthrough, tmdb pick, original_language, no-key fallback, cache, disc mode)"

# --- rip-video renames staged MKVs by the resolved name (stubs: movie-name, ----
#     makemkvcon, mount-nfs) --------------------------------------------------
unset MAKEMKV_INFO TMDB_API_KEY
vid=$PWD/nas; rip=$vid/rip; mkdir -p "$rip"
# movie-name is called with a PATH (the staged MKV), so it never touches the
# network/drive; stub it to a fixed resolve.
cat > "$bin/movie-name" <<'EOF'
#!/usr/bin/env bash
printf 'Die Hard\t1988\ten\n'
EOF
chmod +x "$bin/movie-name"
cat > "$bin/makemkvcon" <<'EOF'
#!/usr/bin/env bash
out=
for a in "$@"; do case "$a" in */) out=$a;; esac; done
: >"$out/B1_t00.mkv"; : >"$out/B1_t01.mkv"
EOF
chmod +x "$bin/makemkvcon"
cat > "$bin/mount-nfs" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$bin/mount-nfs"
export VIDEOS_MOUNT=$vid

bash "$repo/rip-video.sh" >/dev/null || { echo "FAIL: rip-video exited non-zero"; exit 1; }
[ -e "$rip/Die Hard (1988)_t00.mkv" ] || { echo "FAIL: rip rename t00"; exit 1; }
[ -e "$rip/Die Hard (1988)_t01.mkv" ] || { echo "FAIL: rip rename t01"; exit 1; }
[ -e "$rip/B1_t00.mkv" ] && { echo "FAIL: old label left behind"; exit 1; }

echo "PASS: rip-video renames B1_tNN.mkv -> <Title> (Year)_tNN.mkv"

# 7. a title with '/' is made path-safe at rename (transcode re-sanitizes fully later).
rm -f "$rip"/*.mkv
cat > "$bin/movie-name" <<'EOF'
#!/usr/bin/env bash
printf 'Face/Off\t1997\ten\n'
EOF
chmod +x "$bin/movie-name"
bash "$repo/rip-video.sh" >/dev/null 2>&1 || { echo "FAIL: rip-video (slash) exited non-zero"; exit 1; }
[ -e "$rip/Face-Off (1997)_t00.mkv" ] || { echo "FAIL: slash not stripped at rename"; ls "$rip"; exit 1; }
echo "PASS: rip-video strips '/' in the resolved name at rename"
