#!/usr/bin/env bash
# Kick off an HMD triage run with one classify task.
#
# Usage:
#   ./driver.sh "the user's request"
#
# Prints the run directory to stdout (so you can capture it):
#   RUN_DIR=$(./driver.sh "Audit my Solidity contract")

set -euo pipefail

USER_REQUEST="${1:?usage: driver.sh \"<user request text>\"}"

RUN_ID="hmd-$(date +%s)-$RANDOM"
RUN_DIR="$HOME/.swarm/$RUN_ID"
mkdir -p "$RUN_DIR"

swarm-cli status-init \
  --run-dir "$RUN_DIR" \
  --run-id "$RUN_ID" \
  --summary "HMD triage example: $USER_REQUEST" \
  --next-step "claim t.classify and decide routine vs complex" \
  --next-task-id "t.classify" \
  --metadata "$(printf '{"user_request":%s,"created_by":"driver"}' "$(printf '%s' "$USER_REQUEST" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')")"

swarm-cli enqueue \
  --run-dir "$RUN_DIR" \
  --task-id "t.classify" \
  --task-type "classify" \
  --payload "$(printf '{"user_request":%s}' "$(printf '%s' "$USER_REQUEST" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')")" \
  --tier-hint "haiku" \
  --created-by "driver"

echo "$RUN_DIR"
