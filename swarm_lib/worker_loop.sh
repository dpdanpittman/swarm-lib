#!/usr/bin/env bash
# worker_loop.sh — generic swarm-lib consumer loop.
#
# Polls a swarm-lib run directory's pending/ queue, atomically claims tasks
# via `swarm-cli claim`, invokes a user-supplied handler with the task JSON
# on stdin, and marks the task done or failed based on the handler's exit
# code.
#
# Usage:
#   worker_loop.sh \
#     --run-dir ~/src/tribunal/run/r42x \
#     --worker-id mabus-tribunal-1 \
#     --handler ./tribunal_handle.sh \
#     [--task-type-filter implement,verify] \
#     [--poll-interval 5] \
#     [--max-iterations 0]      \  # 0 = unlimited
#     [--heartbeat-interval 30]    # seconds between heartbeats
#
# Handler contract:
#   - Receives task JSON on stdin
#   - Environment:
#       SWARM_RUN_DIR        — the run directory
#       SWARM_TASK_ID        — the claimed task's id
#       SWARM_WORKER_ID      — this worker's id
#       SWARM_ARTIFACT_PATH  — suggested output path (under artifacts/)
#       SWARM_LOG_PATH       — incremental log file; handler may append progress (tail -f friendly)
#   - Writes artifacts under SWARM_RUN_DIR/artifacts/ before exiting
#   - Optionally enqueues follow-up tasks (via swarm-cli or the lib)
#   - Optionally updates status.json (via swarm-cli status-write)
#   - Exit 0 = success → task moves to done/, completed_tasks updated
#   - Exit non-zero = failure → task moves to failed/
#
# Heartbeats / orphan recovery:
#   This loop spawns a background subshell that touches
#   claimed/<worker_id>/.heartbeat every --heartbeat-interval seconds.
#   A separate `swarm-cli reap --run-dir ... --stale-after N` invocation
#   (cron or worker startup) returns stale claims to pending/ when the
#   heartbeat falls behind. See swarm_lib.orphan for the protocol.

set -euo pipefail

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

RUN_DIR=""
WORKER_ID=""
HANDLER=""
TASK_TYPE_FILTER=""
POLL_INTERVAL=5
MAX_ITERATIONS=0   # 0 = unlimited
HEARTBEAT_INTERVAL=30

usage() {
  sed -n '2,40p' "$0" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-dir)              RUN_DIR="$2"; shift 2 ;;
    --worker-id)            WORKER_ID="$2"; shift 2 ;;
    --handler)              HANDLER="$2"; shift 2 ;;
    --task-type-filter)     TASK_TYPE_FILTER="$2"; shift 2 ;;
    --poll-interval)        POLL_INTERVAL="$2"; shift 2 ;;
    --max-iterations)       MAX_ITERATIONS="$2"; shift 2 ;;
    --heartbeat-interval)   HEARTBEAT_INTERVAL="$2"; shift 2 ;;
    -h|--help)              usage; exit 0 ;;
    *)
      echo "[worker_loop] unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$RUN_DIR" || -z "$WORKER_ID" || -z "$HANDLER" ]]; then
  echo "[worker_loop] --run-dir, --worker-id, and --handler are required" >&2
  usage
  exit 2
fi

if ! command -v swarm-cli >/dev/null 2>&1; then
  echo "[worker_loop] swarm-cli not on PATH; install swarm-lib via 'pip install -e .' or similar" >&2
  exit 3
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "[worker_loop] jq is required for parsing claim output" >&2
  exit 3
fi

if [[ ! -x "$HANDLER" ]]; then
  echo "[worker_loop] handler '$HANDLER' is not executable" >&2
  exit 3
fi

mkdir -p "$RUN_DIR/artifacts"

iterations=0
TERMINATE=0

log() {
  echo "[worker_loop:$WORKER_ID] $*" >&2
}

# ---------------------------------------------------------------------------
# Heartbeat keeper — background subshell, killed on exit
# ---------------------------------------------------------------------------

HEARTBEAT_FILE="$RUN_DIR/claimed/$WORKER_ID/.heartbeat"
HEARTBEAT_NOTE_FILE="$RUN_DIR/claimed/$WORKER_ID/.heartbeat-note"
mkdir -p "$RUN_DIR/claimed/$WORKER_ID"
echo "" > "$HEARTBEAT_NOTE_FILE"

# Seed an initial heartbeat before the keeper subshell starts so a fast
# reaper running concurrently doesn't see us as already-stale.
swarm-cli heartbeat --run-dir "$RUN_DIR" --worker-id "$WORKER_ID" >/dev/null 2>&1 || true

(
  while true; do
    NOTE="$(cat "$HEARTBEAT_NOTE_FILE" 2>/dev/null || echo "")"
    swarm-cli heartbeat \
      --run-dir "$RUN_DIR" \
      --worker-id "$WORKER_ID" \
      --note "$NOTE" >/dev/null 2>&1 || true
    sleep "$HEARTBEAT_INTERVAL"
  done
) &
HEARTBEAT_PID=$!

cleanup() {
  if [[ -n "${HEARTBEAT_PID:-}" ]]; then
    kill "$HEARTBEAT_PID" 2>/dev/null || true
    wait "$HEARTBEAT_PID" 2>/dev/null || true
  fi
  # Remove heartbeat + note so a future reap doesn't see a stale file from
  # a worker that exited cleanly. Leave the worker dir itself in place —
  # may still contain failed-to-complete files we want to inspect.
  rm -f "$HEARTBEAT_FILE" "$HEARTBEAT_NOTE_FILE" 2>/dev/null || true
}
trap 'TERMINATE=1' INT TERM
trap 'cleanup' EXIT

log "starting (run_dir=$RUN_DIR, handler=$HANDLER, filter=${TASK_TYPE_FILTER:-<any>}, poll=${POLL_INTERVAL}s, heartbeat=${HEARTBEAT_INTERVAL}s, max=${MAX_ITERATIONS})"

# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------

while [[ "$TERMINATE" -eq 0 ]]; do
  if [[ "$MAX_ITERATIONS" -gt 0 ]] && [[ "$iterations" -ge "$MAX_ITERATIONS" ]]; then
    log "max iterations ($MAX_ITERATIONS) reached, exiting cleanly"
    exit 0
  fi

  # Attempt to claim a task. Empty stdout = nothing claimable.
  CLAIM_ARGS=(--run-dir "$RUN_DIR" --worker-id "$WORKER_ID")
  if [[ -n "$TASK_TYPE_FILTER" ]]; then
    CLAIM_ARGS+=(--task-type-filter "$TASK_TYPE_FILTER")
  fi

  TASK_JSON=$(swarm-cli claim "${CLAIM_ARGS[@]}" || true)

  if [[ -z "$TASK_JSON" ]]; then
    sleep "$POLL_INTERVAL"
    continue
  fi

  TASK_ID=$(echo "$TASK_JSON" | jq -r .task_id)
  TASK_TYPE=$(echo "$TASK_JSON" | jq -r .task_type)

  if [[ -z "$TASK_ID" || "$TASK_ID" == "null" ]]; then
    log "claim returned malformed JSON; skipping" >&2
    sleep "$POLL_INTERVAL"
    continue
  fi

  # Update heartbeat note so the keeper writes the current task id from now on.
  echo "$TASK_ID" > "$HEARTBEAT_NOTE_FILE"

  ARTIFACT_PATH="$RUN_DIR/artifacts/${TASK_ID}.md"
  LOG_PATH="$RUN_DIR/artifacts/${TASK_ID}.log"
  : > "$LOG_PATH"  # truncate any prior log
  log "claimed $TASK_ID (type=$TASK_TYPE), invoking handler"

  HANDLER_EXIT=0
  if SWARM_RUN_DIR="$RUN_DIR" \
     SWARM_TASK_ID="$TASK_ID" \
     SWARM_WORKER_ID="$WORKER_ID" \
     SWARM_ARTIFACT_PATH="$ARTIFACT_PATH" \
     SWARM_LOG_PATH="$LOG_PATH" \
     "$HANDLER" <<< "$TASK_JSON"; then
    swarm-cli complete \
      --run-dir "$RUN_DIR" \
      --worker-id "$WORKER_ID" \
      --task-id "$TASK_ID" \
      --success
    log "$TASK_ID done"
  else
    HANDLER_EXIT=$?
    swarm-cli complete \
      --run-dir "$RUN_DIR" \
      --worker-id "$WORKER_ID" \
      --task-id "$TASK_ID" \
      --failure
    log "$TASK_ID failed (handler exit $HANDLER_EXIT)"
  fi

  # Clear the per-task note; the next iteration writes its own when claiming.
  echo "" > "$HEARTBEAT_NOTE_FILE"
  iterations=$((iterations + 1))
done

log "terminated cleanly after $iterations iteration(s)"
exit 0
