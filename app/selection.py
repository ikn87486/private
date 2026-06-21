"""ボットが取引する戦略の選定（フェーズ3 → フェーズ4 の橋渡し）。

ユニバースの各銘柄について全戦略をウォークフォワード検証し、
「信頼できる（◎）」と判定された中で最も検証成績の良い戦略を採用する。
結果は live_strategies テーブルに保存し、ライブ売買エンジンが参照する。
"""

from __future__ import annotations

import json
from datetime import datetime

from . import db, validation
from . import strategies as strat
from .stocks import STOCKS


def select_strategies(train_years: int = 3, test_years: int = 1) -> list[dict]:
    """全銘柄×全戦略を検証し、検証◎の最良戦略を銘柄ごとに採用する。

    Returns:
        採用した {ticker, strategy, params, oos_compound, verdict} のリスト。
    """
    chosen: list[dict] = []

    for ticker in STOCKS:
        best = None
        for strategy in strat.STRATEGIES:
            try:
                r = validation.walk_forward(ticker, strategy, train_years, test_years)
            except Exception:
                continue
            if not r.get("ok") or r.get("vclass") != "good":
                continue
            # 検証◎の中で検証複利が最大のものを採用
            if best is None or r["oos_compound"] > best["oos_compound"]:
                # 直近ウィンドウで実際に使われた最適パラメータを採用
                last_params = r["windows"][-1]["params"]
                best = {
                    "ticker": ticker,
                    "strategy": strategy,
                    "params": last_params,
                    "oos_compound": r["oos_compound"],
                    "verdict": r["verdict"],
                }
        if best:
            chosen.append(best)

    _store(chosen)
    return chosen


def _store(chosen: list[dict]) -> None:
    """選定結果で live_strategies を洗い替える。"""
    now = datetime.now().isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute("DELETE FROM live_strategies")
        conn.executemany(
            "INSERT INTO live_strategies "
            "(ticker, strategy, params, oos_compound, verdict, selected_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    c["ticker"], c["strategy"], json.dumps(c["params"], ensure_ascii=False),
                    c["oos_compound"], c["verdict"], now,
                )
                for c in chosen
            ],
        )


def get_live_strategies() -> list[dict]:
    """選定済み戦略を DB から読み出す（params は dict に復元）。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM live_strategies ORDER BY oos_compound DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["params"] = json.loads(d["params"])
        out.append(d)
    return out
