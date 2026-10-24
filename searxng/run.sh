#!/usr/bin/env bash
# Start the local SearXNG instance used by the voice chat server's web_search tool.
set -euo pipefail
cd "$(dirname "$0")"
export SEARXNG_SETTINGS_PATH="${PWD}/settings.yml"
exec .venv/bin/python -m searx.webapp
