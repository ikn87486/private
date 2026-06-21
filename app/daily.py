"""launchd / cron から呼ぶ独立実行エントリポイント。

アプリ（Webサーバ）を起動していなくても、この1コマンドで定期処理を実行できる。

使い方:
    python -m app.daily            # 当日の自動売買（既定）
    python -m app.daily trade      # 同上
    python -m app.daily report     # 週次の振り返りレポート生成
    python -m app.daily screen     # スクリーナー: 最新データ取得→予測保存→満期実績の記入
    python -m app.daily accuracy    # 較正＋精度（ウォークフォワード/実運用）の再計算
"""

from __future__ import annotations

import sys
from datetime import datetime

from . import calibration, collector, db, live, outcomes, report, screener
from .stocks import MARKET_BENCHMARK, UNIVERSE


def _run_screen(stamp: str) -> None:
    """最新データを取得し、全銘柄を再評価して予測を保存、満期分の実績を記入する。"""
    tickers = list(UNIVERSE.keys()) + list(MARKET_BENCHMARK.values())
    existing = {c["ticker"] for c in collector.coverage()}
    new = [t for t in tickers if t not in existing]
    if new:
        collector.collect(new, period="max")          # 新規銘柄は全期間
    collector.update_latest([t for t in tickers if t in existing])  # 既存は増分

    results = screener.screen()
    screener.save_snapshot(results)
    filled = outcomes.fill_outcomes()
    print(f"[{stamp}] screen: {len(results)}銘柄保存 / 実績記入 {filled}")


def _run_accuracy(stamp: str) -> None:
    """較正と精度テーブル（ウォークフォワード/実運用）を再計算する。"""
    cal = calibration.build_calibration()
    wf = calibration.walk_forward_accuracy()
    lv = calibration.live_accuracy()
    print(f"[{stamp}] accuracy: calib={cal.get('ok')} wf_rows={wf.get('n_rows')} "
          f"live_rows={lv.get('n_rows')}")


def main() -> int:
    job = sys.argv[1] if len(sys.argv) > 1 else "trade"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.init_db()

    try:
        if job == "report":
            r = report.generate_report("live")
            print(f"[{stamp}] レポート生成: {r['headline']}")
        elif job == "screen":
            _run_screen(stamp)
        elif job == "accuracy":
            _run_accuracy(stamp)
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
        print(f"[{stamp}] 失敗({job}): {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
