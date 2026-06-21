#!/usr/bin/env bash
# launchd から呼ばれるジョブ実行ラッパ。ログ追記＋失敗時に macOS 通知。
set -uo pipefail

JOB="${1:-screen}"
DIR="/Users/yuki/Prog/stock"
UV="$HOME/.local/bin/uv"

cd "$DIR" || exit 1
mkdir -p logs
LOG="logs/launchd_${JOB}.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] start $JOB" >> "$LOG"
"$UV" run python -m app.daily "$JOB" >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAILED $JOB rc=$rc" >> "$LOG"
  /usr/bin/osascript -e "display notification \"stock $JOB ジョブが失敗 (rc=$rc)\" with title \"stock 自動処理\"" 2>/dev/null || true
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] done $JOB" >> "$LOG"
fi
exit $rc
