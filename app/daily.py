"""launchd / cron から呼ぶ独立実行エントリポイント。

アプリ（Webサーバ）を起動していなくても、この1コマンドで
当日の自動売買、または週次レポート生成を実行できる。

使い方:
    python -m app.daily          # 当日の自動売買（既定）
    python -m app.daily trade    # 同上
    python -m app.daily report   # 週次の振り返りレポート生成
"""

from __future__ import annotations

import sys
from datetime import datetime

from . import db, live, report


def main() -> int:
    job = sys.argv[1] if len(sys.argv) > 1 else "trade"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.init_db()

    try:
        if job == "report":
            r = report.generate_report("live")
            print(f"[{stamp}] レポート生成: {r['headline']}")
        else:
            r = live.run_daily("live")
            if r.get("ok"):
                print(
                    f"[{stamp}] 自動売買 {r['run_date']}: "
                    f"買{len(r['buys'])} 売{len(r['sells'])} "
                    f"建玉{r['n_positions']} 総資産{r['total_value']:,}円"
                )
            else:
                print(f"[{stamp}] 自動売買スキップ: {r.get('reason')}")
    except Exception as e:  # noqa: BLE001
        print(f"[{stamp}] 失敗: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
