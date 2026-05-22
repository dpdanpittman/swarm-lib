#!/usr/bin/env bash
# escalate.sh — opus-tier handler for tasks the classifier flagged complex.
#
# Stub: writes a placeholder artifact. Replace the STUB block with a real
# claude/ollama call. See ../README.md.

set -euo pipefail

TASK_JSON=$(cat)
TASK_ID=$(echo "$TASK_JSON" | jq -r .task_id)
USER_REQUEST=$(echo "$TASK_JSON" | jq -r '.payload.user_request')
CLASSIFIER_REASON=$(echo "$TASK_JSON" | jq -r '.payload.classifier_reason')

echo "[escalate] task=$TASK_ID running opus-tier handler" >> "$SWARM_LOG_PATH"

# -----------------------------------------------------------------------------
# STUB: replace with a real opus-tier model call.
#
# Real implementation example:
#   claude -p --model opus --output-file "$SWARM_ARTIFACT_PATH" <<EOF
#   $USER_REQUEST
#   (Pre-classified as complex by triage: $CLASSIFIER_REASON. Reason hard.)
#   EOF
# -----------------------------------------------------------------------------

{
  echo "# Escalated answer (opus-tier stub)"
  echo
  echo "**Original request**: $USER_REQUEST"
  echo
  echo "**Why escalated**: $CLASSIFIER_REASON"
  echo
  echo "---"
  echo
  echo "Stub answer. In real use, this is where the opus-tier model writes"
  echo "its full response. See README.md for the wiring."
} > "$SWARM_ARTIFACT_PATH"

exit 0
