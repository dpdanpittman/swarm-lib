#!/usr/bin/env bash
# classify.sh — cheap-tier handler. Reads the task, decides whether the work
# is routine (handle inline) or complex (escalate to opus).
#
# Contract:
#   - Reads task JSON on stdin
#   - Writes verdict + (if routine) the final answer to $SWARM_ARTIFACT_PATH
#   - If escalation needed, enqueues a follow-up task with task_type=escalate
#     and exits 0 (the classify task itself succeeded — the work just continues)

set -euo pipefail

TASK_JSON=$(cat)
TASK_ID=$(echo "$TASK_JSON" | jq -r .task_id)
USER_REQUEST=$(echo "$TASK_JSON" | jq -r '.payload.user_request')

echo "[classify] task=$TASK_ID request_len=${#USER_REQUEST}" >> "$SWARM_LOG_PATH"

# -----------------------------------------------------------------------------
# Triage logic — STUB heuristic.
#
# Real implementation: pipe USER_REQUEST to a cheap model (claude haiku or
# ollama gpt-oss:20b) with a prompt like "Respond with routine or complex
# on the first line, then a one-line reason." See README.md.
#
# Stub: classify by input length. Long inputs assumed complex.
# -----------------------------------------------------------------------------

if [[ "${#USER_REQUEST}" -ge 200 ]]; then
  VERDICT="complex"
  REASON="input length $((${#USER_REQUEST})) chars exceeds heuristic threshold (200)"
else
  VERDICT="routine"
  REASON="input length $((${#USER_REQUEST})) chars within haiku capability"
fi

echo "[classify] verdict=$VERDICT  reason=$REASON" >> "$SWARM_LOG_PATH"

if [[ "$VERDICT" == "routine" ]]; then
  # Resolve here and finish. The classify task's artifact IS the final answer.
  {
    echo "# Triage result: routine"
    echo
    echo "**Verdict**: routine"
    echo "**Reason**: $REASON"
    echo
    echo "## Answer (haiku-tier stub)"
    echo
    echo "Stub answer for: $USER_REQUEST"
    echo
    echo "Replace this block with a real claude/ollama call. See README.md."
  } > "$SWARM_ARTIFACT_PATH"
  exit 0
fi

# Complex: enqueue an escalate task that depends on this classify completing.
ESC_TASK_ID="t.escalate"

# Pass through the original request + the classifier's reasoning
ESC_PAYLOAD=$(jq -nc \
  --arg req "$USER_REQUEST" \
  --arg reason "$REASON" \
  --arg classify_task "$TASK_ID" \
  '{user_request:$req, classifier_reason:$reason, classify_task:$classify_task}')

swarm-cli enqueue \
  --run-dir "$SWARM_RUN_DIR" \
  --task-id "$ESC_TASK_ID" \
  --task-type "escalate" \
  --payload "$ESC_PAYLOAD" \
  --depends-on "$TASK_ID" \
  --tier-hint "opus" \
  --created-by "$SWARM_WORKER_ID"

# The classify task itself produces an artifact noting the escalation
{
  echo "# Triage result: complex — escalated to opus"
  echo
  echo "**Verdict**: complex"
  echo "**Reason**: $REASON"
  echo
  echo "Escalation enqueued as \`$ESC_TASK_ID\` (depends on this task)."
  echo "Watch \`artifacts/$ESC_TASK_ID.md\` for the final answer."
} > "$SWARM_ARTIFACT_PATH"

# Update status so the next claimant knows where the chain is
swarm-cli status-write \
  --run-dir "$SWARM_RUN_DIR" \
  --summary "Classify said complex; escalated to opus" \
  --next-step "claim t.escalate once t.classify is marked done" \
  --next-task-id "$ESC_TASK_ID"

exit 0
