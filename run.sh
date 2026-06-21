#!/usr/bin/env bash
# 開発サーバを起動する。ブラウザで http://127.0.0.1:8000 を開く。
set -e
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
