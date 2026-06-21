#!/usr/bin/env bash
# 導入した launchd エージェントを解除・削除する。
set -uo pipefail

DEST="$HOME/Library/LaunchAgents"
JOBS=(com.stock.screen.morning com.stock.screen.evening com.stock.accuracy)

for p in "${JOBS[@]}"; do
    launchctl unload "$DEST/$p.plist" 2>/dev/null || true
    rm -f "$DEST/$p.plist"
    echo "removed: $p"
done
echo "完了。"
