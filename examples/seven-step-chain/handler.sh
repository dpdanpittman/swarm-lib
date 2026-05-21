#!/usr/bin/env bash
# handler.sh — generic step handler for the seven-step-chain demo.
#
# Receives task JSON on stdin, writes a placeholder artifact, then enqueues
# the NEXT step in the chain (if any) with depends_on=[current_task_id].
#
# v0.1.0 STUB BEHAVIOR: this handler writes a marker artifact and does NOT
# actually invoke Claude. Swap the marker step with your real
# `claude -p --model "$MODEL" ...` invocation when wiring up live LLM
# orchestration. The chain plumbing (claim/complete/enqueue-next/status)
# is identical regardless of what "real work" the handler does inside.
#
# Environment (provided by worker_loop.sh):
#   SWARM_RUN_DIR        — the run directory
#   SWARM_TASK_ID        — this task's id
#   SWARM_WORKER_ID      — the claiming worker's id
#   SWARM_ARTIFACT_PATH  — where to write this step's output

set -euo pipefail

TASK_JSON=$(cat)
STEP=$(echo "$TASK_JSON" | jq -r '.payload.step')
STEP_INDEX=$(echo "$TASK_JSON" | jq -r '.payload.step_index')
TIER=$(echo "$TASK_JSON" | jq -r '.tier_hint // "sonnet"')
ALL_STEPS=$(echo "$TASK_JSON" | jq -r '.payload.all_steps | join(",")')
ALL_TIERS=$(echo "$TASK_JSON" | jq -r '.payload.all_tiers | join(",")')
RUN_ID=$(echo "$TASK_JSON" | jq -r '.run_id')

# Read backlog + any prior artifacts (for real LLM wiring later)
BACKLOG_CONTENT=""
if [[ -f "$SWARM_RUN_DIR/BACKLOG.md" ]]; then
  BACKLOG_CONTENT=$(cat "$SWARM_RUN_DIR/BACKLOG.md")
fi

# --- STUB: write a marker artifact ---------------------------------------
# Real wiring: replace this block with
#   MODEL=$(map_tier_to_model "$TIER")
#   SKILL_NAME="tribunal-$STEP"
#   claude -p --model "$MODEL" --skill "$SKILL_NAME" "$PROMPT" > "$SWARM_ARTIFACT_PATH"
cat > "$SWARM_ARTIFACT_PATH" <<EOF
# Step: $STEP (index $STEP_INDEX, tier $TIER)

Task ID: $SWARM_TASK_ID
Worker:  $SWARM_WORKER_ID
Run:     $RUN_ID
Time:    $(date -u +%Y-%m-%dT%H:%M:%SZ)

## Backlog excerpt
$(echo "$BACKLOG_CONTENT" | head -10)

## Stub output
This is a v0.1.0 placeholder artifact. The handler did NOT invoke Claude;
it wrote this marker to validate the substrate's chain wiring (claim →
complete → enqueue-next). Replace the STUB block in handler.sh with a
real LLM invocation for live use.
EOF
# -------------------------------------------------------------------------

# Compute the next step (if any) and enqueue it
IFS=',' read -ra STEPS_ARR <<< "$ALL_STEPS"
IFS=',' read -ra TIERS_ARR <<< "$ALL_TIERS"

NEXT_INDEX=$((STEP_INDEX + 1))
if [[ "$NEXT_INDEX" -lt "${#STEPS_ARR[@]}" ]]; then
  NEXT_STEP="${STEPS_ARR[$NEXT_INDEX]}"
  NEXT_TIER="${TIERS_ARR[$NEXT_INDEX]}"
  NEXT_TASK_ID="t.$RUN_ID.$NEXT_STEP"

  # Build the input_artifacts list — for now, just the just-written artifact
  PRIOR_ARTIFACT="artifacts/${SWARM_TASK_ID}.md"

  swarm-cli enqueue \
    --run-dir "$SWARM_RUN_DIR" \
    --task-id "$NEXT_TASK_ID" \
    --task-type "$NEXT_STEP" \
    --tier-hint "$NEXT_TIER" \
    --depends-on "$SWARM_TASK_ID" \
    --created-by "handler.sh:$STEP" \
    --payload "$(jq -nc \
      --arg step "$NEXT_STEP" \
      --argjson idx "$NEXT_INDEX" \
      --argjson steps "$(echo "$TASK_JSON" | jq -c '.payload.all_steps')" \
      --argjson tiers "$(echo "$TASK_JSON" | jq -c '.payload.all_tiers')" \
      --arg prior "$PRIOR_ARTIFACT" \
      '{step: $step, step_index: $idx, all_steps: $steps, all_tiers: $tiers, input_artifacts: [$prior]}')"

  swarm-cli status-write \
    --run-dir "$SWARM_RUN_DIR" \
    --summary "$STEP complete; next: $NEXT_STEP" \
    --next-step "claim $NEXT_STEP task" \
    --next-task-id "$NEXT_TASK_ID"
else
  # Last step — workflow complete
  swarm-cli status-write \
    --run-dir "$SWARM_RUN_DIR" \
    --summary "chain complete (final step: $STEP)" \
    --next-step "(workflow done)"
fi

exit 0
