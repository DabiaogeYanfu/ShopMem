#!/bin/bash
# Stop this user's vLLM servers: all of them, or the one on a given port.
#   scripts/stop_vllm.sh          # all
#   scripts/stop_vllm.sh 8005     # the server started with --port 8005
# The [v] in the pattern keeps pgrep from matching this script's own command line.
PATTERN="[v]llm serve"
[ $# -ge 1 ] && PATTERN="[v]llm serve .*--port $1( |$)"
pids=$(pgrep -u "$USER" -f "$PATTERN")
[ -z "$pids" ] && { echo "no matching vLLM server"; exit 0; }
kill $pids && echo "stopped: $pids"
