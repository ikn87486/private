"""データ収集の鮮度チェック（Mac/Windows 共通）。

直近の予測スナップショット・価格・較正・実績の状況を表示する。
研究室PCで `uv run python scripts/check_data.py` を実行すると、
「ここ数日きちんとデータが取れているか」が一目で分かる。
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

# スクリプトを直接実行してもリポジトリ直下を import できるようにする。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db


def main() -> None:
    db.init_db()
    today = date.today()
    with db.connect() as c:
        print("==== データ収集の鮮度チェック ====")
        print(f"今日: {today.isoformat()}\n")

        print("[予測スナップショット] 直近の基準日と件数:")
        rows = c.execute(
            "SELECT snapshot_date, COUNT(*) n FROM screen_snapshots "
            "GROUP BY snapshot_date ORDER BY snapshot_date DESC LIMIT 7"
        ).fetchall()
        if not rows:
            print("  (なし) -- まだ一度も screen が走っていません")
        for r in rows:
            print(f"  {r['snapshot_date']}  {r['n']}行")

        r = c.execute("SELECT MAX(date) d, COUNT(DISTINCT ticker) t FROM prices").fetchone()
        print(f"\n[価格] 最新日: {r['d']} / 銘柄数: {r['t']}")

        r = c.execute(
            "SELECT COUNT(*) n, MAX(filled_at) f FROM screen_outcomes"
        ).fetchone()
        print(f"[実績記入] 件数: {r['n']} / 最終記入: {r['f']}")

        r = c.execute("SELECT MAX(updated_at) u FROM screen_calibration").fetchone()
        print(f"[較正] 最終更新: {r['u']}")

        # 直近3営業日（土日を除く）が揃っているかの簡易判定
        snap_dates = {row["snapshot_date"] for row in rows}
        missing = []
        d = today
        checked = 0
        while checked < 3:
            if d.weekday() < 5:  # 平日のみ
                if d.isoformat() not in snap_dates:
                    missing.append(d.isoformat())
                checked += 1
            d -= timedelta(days=1)
        print()
        if missing:
            print(f"[注意] 直近の平日でスナップショットが無い日: {', '.join(missing)}")
            print("  → タスクがその日に動いたか /logs か logs/win_screen.log を確認してください。")
        else:
            print("[OK] 直近3営業日のスナップショットが揃っています。")


if __name__ == "__main__":
    main()
