.PHONY: setup dev test

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	.venv/bin/python -m piper.download_voices --download-dir models zh_CN-huayan-medium

dev:
	set -a && . ./.env && set +a && .venv/bin/python -m voice_server

test:
	.venv/bin/python -m pytest -q tests
