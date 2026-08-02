# rip-common.sh — sourced by rip.sh, rip-video.sh, transcode-video.sh, titles.sh,
# sync-music.sh. unified timing + logging to ./rip.log in the cwd. the caller sets
# RIP_SCRIPT, RIP_SW, and RIP_NAME, then calls rip_log_start (logs a `started`
# marker so a long rip shows up at once); each rip_phase <name> ... rip_phase_end
# pair logs one line. the EXIT trap logs a `total`, skipped when a single phase
# succeeded (it would just duplicate that phase line). one phase (CD/video) or many.
# ponytail: shared helper from the 2nd caller on. installed as a sibling next
# to each script and sourced via $(dirname "$0").

rip_log=rip.log

rip_log_start() {                          # begin global timer + EXIT trap
  RIP_START=$(date +%s); RIP_START_TS=$(date '+%F %T')
  _rip_emit "$RIP_START_TS" "-" "-" 0 0 started   # log at once, not only at first phase end
  trap rip_log_finish EXIT
}

rip_phase() {                              # begin a named phase
  RIP_PHASE_NAME=$1
  RIP_PHASE_START=$(date +%s); RIP_PHASE_START_TS=$(date '+%F %T')
}

rip_phase_end() {                          # log the just-finished phase
  local elapsed=$(( $(date +%s) - RIP_PHASE_START ))
  RIP_PHASES=$(( ${RIP_PHASES:-0} + 1 ))
  _rip_emit "$RIP_PHASE_START_TS" "$(date '+%F %T')" "$RIP_PHASE_NAME" \
            $(( elapsed / 60 )) $(( elapsed % 60 )) ok
}

rip_log_finish() {                         # EXIT trap: log the total
  local rc=$? elapsed status
  # one phase that succeeded already logged start..end..status — the total would
  # duplicate it. skip it. (multi-phase runs, or any failure, still get a total.)
  [ "$rc" -eq 0 ] && [ "${RIP_PHASES:-0}" -eq 1 ] && return
  elapsed=$(( $(date +%s) - RIP_START ))
  status=ok; [ "$rc" -ne 0 ] && status="FAIL($rc)"
  _rip_emit "$RIP_START_TS" "$(date '+%F %T')" total \
            $(( elapsed / 60 )) $(( elapsed % 60 )) "$status"
}

# start  end  script  software  name  phase  m:ss  status  → ./rip.log
_rip_emit() {
  printf '%s  %s  %s  %s  %s  %s  %dm%02d  %s\n' \
    "$1" "$2" "$RIP_SCRIPT" "$RIP_SW" "${RIP_NAME:-}" "$3" "$4" "$5" "$6" \
    | tee -a "$rip_log"
}

# rip_note <phase> <text>: log an untimed note line (e.g. "audio kept=eng detected=eng")
# to stderr (visible live) AND rip.log. same 8 columns as a phase line, with "-"
# in the end-time and m:ss slots so it reads as a note, not a timed phase. used by
# rip-video to record which audio language was kept (Task 3).
rip_note() {
  printf '%s  -  %s  %s  %s  %s  -  %s\n' \
    "$(date '+%F %T')" "${RIP_SCRIPT:-}" "${RIP_SW:-}" "${RIP_NAME:-}" "$1" "$2" \
    | tee -a "$rip_log" >&2
}

# require_online <url> <label>: refuse to start if <url> is unreachable. both
# rippers depend on a metadata lookup (TMDB for video, MusicBrainz for music), so
# an offline rip would name/tag wrong. 5s cap so a dead link fails fast.
require_online() {
  curl -fsS --max-time 5 -o /dev/null "$1" \
    || { echo "$(basename "$0"): offline or $2 unreachable — connect and re-run." >&2; exit 1; }
}
