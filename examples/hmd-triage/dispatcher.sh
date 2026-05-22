#!/usr/bin/env bash
# HMD dispatcher — routes a swarm-lib task to classify.sh or escalate.sh
# based on its task_type.
#
# worker_loop.sh invokes this with the task JSON on stdin. We re-emit on
# stdin to the child handler so the handlers can stay pure (read JSON,
# write artifact).

set -euo pipefail

TASK_JSON=$(cat)
TASK_TYPE=$(echo "$TASK_JSON" | jq -r .task_type)

HERE="$(cd "$(dirname "$0")" && pwd)"

case "$TASK_TYPE" in
  classify)
    exec "$HERE/classify.sh" <<< "$TASK_JSON"
    ;;
  escalate)
    exec "$HERE/escalate.sh" <<< "$TASK_JSON"
    ;;
  *)
    echo "[dispatcher] unknown task_type: $TASK_TYPE" >&2
    exit 1
    ;;
esac
