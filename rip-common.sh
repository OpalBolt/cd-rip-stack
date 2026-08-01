# rip-common.sh — sourced by rip.sh and rip-video.sh. unified timing + logging
# to ./rip.log in the cwd. the caller sets RIP_SCRIPT, RIP_SW, and RIP_NAME,
# then calls rip_log_start; each rip_phase <name> ... rip_phase_end pair logs
# one line and an EXIT trap logs the total. one phase (CD) or several (video).
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
  _rip_emit "$RIP_PHASE_START_TS" "$(date '+%F %T')" "$RIP_PHASE_NAME" \
            $(( elapsed / 60 )) $(( elapsed % 60 )) ok
}

rip_log_finish() {                         # EXIT trap: log the total
  local rc=$? elapsed status
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
