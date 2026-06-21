#!/usr/bin/env bash
# launchd エージェントを ~/Library/LaunchAgents に導入する。
# 朝夕のスクリーニング（平日）と週次の精度再計算（土）をサーバ非起動でも自動実行する。
set -euo pipefail

SRC="/Users/yuki/Prog/stock/scripts/launchd"
DEST="$HOME/Library/LaunchAgents"
JOBS=(com.stock.screen.morning com.stock.screen.evening com.stock.accuracy)

mkdir -p "$DEST"
chmod +x /Users/yuki/Prog/stock/scripts/run_job.sh

for p in "${JOBS[@]}"; do
    cp "$SRC/$p.plist" "$DEST/$p.plist"
    launchctl unload "$DEST/$p.plist" 2>/dev/null || true
    launchctl load "$DEST/$p.plist"
    echo "loaded: $p"
done

echo "--- registered jobs ---"
launchctl list | grep com.stock || echo "(none found)"
echo "完了。手動テスト: launchctl start com.stock.screen.evening"
