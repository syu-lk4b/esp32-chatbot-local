#!/usr/bin/env bash
# Starts the voice chat backend: local SearXNG (search) + voice server (ASR/LLM/TTS).
# Usage: ./start.sh [stop]   Logs: searxng/searxng.log, server/server.log
set -euo pipefail
cd "$(dirname "$0")"

readonly PATTERNS=("searx.webapp" "python -m voice_server")

stop() {
  local p
  for p in "${PATTERNS[@]}"; do pkill -f "${p}" || true; done
  # uvicorn can hang on shutdown (background threads); escalate after 5 s
  for _ in $(seq 1 10); do
    pgrep -f "${PATTERNS[1]}" >/dev/null || pgrep -f "${PATTERNS[0]}" >/dev/null || return 0
    sleep 0.5
  done
  for p in "${PATTERNS[@]}"; do pkill -9 -f "${p}" || true; done
}

if [[ "${1:-}" == "stop" ]]; then
  stop
  echo "stopped"
  exit 0
fi
if [[ "${1:-}" =~ ^(-h|--help)$ ]]; then
  sed -n '2,3p' "$0"
  exit 0
fi

# The LLM server must already be up: LM Studio (default) or Ollama, per server/.env.
backend="$(sed -n 's/^LLM_BACKEND=//p' server/.env)"; backend="${backend:-openai}"
llm_url="$(sed -n 's/^LLM_URL=//p' server/.env)"
if [[ "${backend}" == "ollama" ]]; then
  probe="${llm_url:-http://127.0.0.1:11434}/api/version"
else
  probe="${llm_url:-http://127.0.0.1:1234/v1}/models"
fi
curl -sf -m 3 "${probe}" >/dev/null || { echo "LLM server (${backend}) not reachable at ${probe}; start LM Studio's server or ollama serve"; exit 1; }
stop
sleep 1
# Redirect the whole subshell (not just the child) so no fd keeps the caller's stdout open.
(cd searxng && exec nohup ./run.sh) > searxng/searxng.log 2>&1 < /dev/null &
(cd server && set -a && . ./.env && set +a && exec nohup .venv/bin/python -m voice_server) \
  > server/server.log 2>&1 < /dev/null &

for _ in $(seq 1 120); do
  if grep -q "Uvicorn running" server/server.log 2>/dev/null; then
    echo "voice server ready on :$(sed -n 's/^VOICE_PORT=//p' server/.env)"
    exit 0
  fi
  if grep -q "Traceback" server/server.log 2>/dev/null; then
    tail -20 server/server.log
    exit 1
  fi
  sleep 1
done
echo "timed out waiting for server; see server/server.log"
exit 1
