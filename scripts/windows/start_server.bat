@echo off
REM Web サーバ（UI）を起動する。同一ネットワーク/Tailscale から閲覧するため 0.0.0.0 で待受。
REM スタートアップに登録するか、手動実行で常駐させる。
cd /d "%~dp0..\.."
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
