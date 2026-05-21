#!/usr/bin/env bash
# driver.sh — seven-step-chain demo: kicks off a Tribunal-shaped 7-skill chain
# on swarm-lib substrate.
#
# This is a v0.1.0 reference consumer. It demonstrates:
#   1. A driver script that creates a run directory + BACKLOG.md + status.json
#   2. Enqueues the first task in a 7-step chain
#   3. Exits immediately (the Yield Rule — see DESIGN.md)
#
# A worker_loop.sh polls the queue, claims tasks, invokes handler.sh, which
# enqueues the next step. The chain runs to completion without the driver's
# context staying alive.
#
# Usage:
#   ./driver.sh "your prompt or backlog content here"

set -euo pipefail

BACKLOG="${1:-}"
if [[ -z "$BACKLOG" ]]; then
  echo "usage: $0 <prompt-or-backlog-content>" >&2
  exit 2
fi

if ! command -v swarm-cli >/dev/null 2>&1; then
  echo "swarm-cli not on PATH; run 'pip install -e .' from swarm-lib root" >&2
  exit 3
fi

# Generate a run-id and create the run directory
RUN_ID="chain-$(date -u +%Y-%m-%d-%H%M%S)-$(printf '%04x' $RANDOM)"
RUN_DIR="${SWARM_HOME:-$HOME/.swarm}/runs/$RUN_ID"
mkdir -p "$RUN_DIR"

# Write BACKLOG.md — the human-intent file
cat > "$RUN_DIR/BACKLOG.md" <<EOF
# Backlog for $RUN_ID

$BACKLOG
EOF

# The 7 steps that mirror Tribunal's skill chain.
# Each step is a task in the queue; the handler picks up step N, then
# enqueues step N+1 with depends_on=[N].
STEPS=(intent plan implement review verify classify incentive)
TIERS=(haiku  opus sonnet    opus   sonnet classify haiku)
# Note: TIERS index 5 was 'classify' which isn't a tier — fixing:
TIERS=(haiku  opus  sonnet   opus   sonnet  haiku    sonnet)

# Initialize status.json
swarm-cli status-init \
  --run-dir "$RUN_DIR" \
  --run-id "$RUN_ID" \
  --summary "chain initialized; queued ${STEPS[0]}" \
  --next-step "claim ${STEPS[0]} task" \
  --next-task-id "t.$RUN_ID.${STEPS[0]}" \
  --metadata "$(cat <<JSON
{
  "consumer": "seven-step-chain",
  "steps": ["${STEPS[0]}","${STEPS[1]}","${STEPS[2]}","${STEPS[3]}","${STEPS[4]}","${STEPS[5]}","${STEPS[6]}"]
}
JSON
)"

# Enqueue the first step. The handler will enqueue each subsequent step
# as it completes the current one.
FIRST_STEP="${STEPS[0]}"
swarm-cli enqueue \
  --run-dir "$RUN_DIR" \
  --task-id "t.$RUN_ID.$FIRST_STEP" \
  --task-type "$FIRST_STEP" \
  --tier-hint "${TIERS[0]}" \
  --created-by "driver.sh" \
  --payload "$(cat <<JSON
{
  "step": "$FIRST_STEP",
  "step_index": 0,
  "all_steps": ["${STEPS[0]}","${STEPS[1]}","${STEPS[2]}","${STEPS[3]}","${STEPS[4]}","${STEPS[5]}","${STEPS[6]}"],
  "all_tiers":  ["${TIERS[0]}","${TIERS[1]}","${TIERS[2]}","${TIERS[3]}","${TIERS[4]}","${TIERS[5]}","${TIERS[6]}"],
  "input_artifacts": []
}
JSON
)"

echo "$RUN_DIR"
