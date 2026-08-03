#!/usr/bin/env bash
# rip-menu — guided entry surface for the ripping toolkit. run inside the nix
# devshell so fzf + every command is on PATH. lists the commands grouped by
# workflow with a one-line description; the focused item shows its full help
# section in a preview pane, then runs the pick.
set -euo pipefail

# kanagawa palette for fzf. exported so the read-prompt below (and any nested
# fzf) shares it. roles: fg fujiWhite, bg/gutter sumiInk1, bg+ waveBlue2
# (hovered/selected row), hl springBlue, hl+ crystalBlue, info fujiGray,
# prompt oniViolet, header crystalBlue, pointer autumnRed, marker autumnGreen,
# border sumiInk4.
export FZF_DEFAULT_OPTS="--color=fg:#DCD7BA,bg:#1F1F28,bg+:#2D4F67,hl:#7FB4CA,hl+:#7E9CD8,info:#727169,prompt:#957FB8,header:#7E9CD8,pointer:#C34043,marker:#76946A,spinner:#957FB8,border:#54546D,gutter:#1F1F28"

entries=(
  "index — Workflow overview"
  # CD
  "rip — Rip a CD to FLAC"
  "tag — Tag ripped music and sort into the library"
  "sync-music — Move the library to the NAS"
  "sync-sd — Copy the library to an SD card"
  # Video
  "rip-video — Rip a disc to a lossless MKV"
  "transcode-video — Encode staged MKVs into the library"
  "titles — List a disc's titles, longest first"
  # Tools
  "movie-name — Resolve a movie name (metadata or TMDB)"
)

# stateless: computed on open, nothing stored. pending counts watch the same
# dirs the commands operate on.
mounted() { grep -q " $1 " /proc/mounts; }   # portable — no util-linux dep

audio_rip="$HOME/media/audio/music/rip"
vid="${VIDEOS_MOUNT:-/mnt/videos}"

pending=()
if [ -d "$audio_rip" ]; then
  # find can exit non-zero (unreadable entry / NFS hiccup); the count is
  # advisory, so || true keeps a failed find (→ 0) from aborting the menu.
  n=$(find "$audio_rip" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l || true)
  [ "$n" -gt 0 ] && pending+=("$n in ~/media/audio/music/rip — awaiting tag")
fi
# ponytail: mount guard BEFORE any access — a stale/unmounted NFS share would
# hang find; skip the video count entirely rather than risk a hang or show "0".
if mounted "$vid" && [ -d "$vid/rip" ]; then
  n=$(find "$vid/rip" -name '*.mkv' 2>/dev/null | wc -l || true)
  [ "$n" -gt 0 ] && pending+=("$n staged MKVs — transcode on desktop")
fi

header="CD · Video · Tools — pick an action"
if [ "${#pending[@]}" -gt 0 ]; then
  # join with " · ": ${pending[*]} only uses the first char of IFS, so build
  # the join by hand rather than rely on IFS trickery.
  joined=""
  for p in "${pending[@]}"; do joined+="${joined:+ · }$p"; done
  header="Pending: $joined"$'\n'"$header"
fi

# help file ships next to rip-menu (flake installs it as a sibling); in the
# nix store $0 is /nix/store/.../bin/rip-menu, so the help sits in that bin dir.
help_file="$(dirname "$0")/rip-menu-help.txt"

# loop so selecting `index` (the overview) re-shows the menu instead of running
# anything; Esc / Ctrl-C on fzf aborts the whole menu. --preview runs in a
# subshell with no $0, so the absolute help path is baked in here. awk prints
# the body of `## {1}` (the focused command's first token), `##` markers
# stripped; {1} is fzf's first space-delimited field, --delimiter=' '.
while true; do
  pick_line=$(printf '%s\n' "${entries[@]}" \
    | fzf --ansi --delimiter=' ' --header="$header" \
          --preview="awk -v c={1} '\$1==\"##\"{s=(\$2==c);next} s' \"$help_file\"" \
          --preview-window='right:60%:wrap' --height=80% --reverse --cycle \
    ) || exit 0
  cmd="${pick_line%% *}"
  [ "$cmd" = "index" ] && continue
  break
done

# per-command arg hints: a non-empty hint earns a read prompt after pick; an
# empty hint runs the command immediately. the description after the em-dash is
# ignored — only the first token (the command) matters.
declare -A arg_hint=(
  [rip]="offset (required) + optional engine/flags — e.g. 6  or  6 whipper"
  [rip-video]="titles — e.g. 1,3-4  or  --extras  or  --part N  (blank = all)"
  [titles]="disc — e.g. disc:1 (blank = disc:0)"
  [movie-name]="args — e.g. --rename /mnt/videos/rip"
  [sync-sd]="card mountpoint (blank = auto-detect)"
  [tag]=""
  [sync-music]=""
  [transcode-video]=""
)

cmd="${pick_line%% *}"
hint="${arg_hint[$cmd]:-}"

if [ -n "$hint" ]; then
  # ponytail: plain read replaces gum input — one fewer dep. word-splitting on
  # $args is intentional (1,3-4 → one arg; --rename /p → two). SC2086 disabled.
  # ▸ in crystalBlue (#7E9CD8), hint in fujiGray (#727169); \033[0m resets.
  printf '\033[38;2;126;156;216m▸\033[0m \033[38;2;114;113;105m%s\033[0m ' "$hint"
  read -r args || exit 0          # Ctrl-D / EOF aborts cleanly
  # shellcheck disable=SC2086
  exec "$cmd" $args
else
  exec "$cmd"
fi
