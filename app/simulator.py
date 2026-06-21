"""仮想売買（ペーパートレード）シミュレータ。

蓄積した過去データに戦略を当てはめ、1往復ごとの売買を「取引ジャーナル」に
記録する。各取引にはエントリー/イグジットの理由と、建玉時の相場コンテキスト、
そして勝ち/負けの判定を残す。これが勝因・敗因分析の素データになる。

簡略化している点（正直な前提）:
- ポジションサイズは「初期資金 × 配分率」で固定（同時保有の現金制約は厳密には見ない）。
  銘柄をまたいだ厳密なポートフォリオ運用は将来フェーズで対応。
- 約定はシグナル発生の翌営業日始値。取引コストは片道0.1%。
- ロングのみ（空売りなし）。
"""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from . import collector, context, db, exits
from . import strategies as strat
from .exits import ExitConfig
from .stocks import BENCHMARK

COST = 0.001          # 片道の取引コスト（手数料+スリッページ）
ALLOC_FRAC = 0.2      # 1トレードに使う資金の割合（初期資金比）
DEFAULT_STOP = -0.08  # 既定の損切りライン（-8%）。


def simulate(
    ticker: str,
    strategy: str,
    params: dict,
    account_name: str = "paper",
    exit_config: ExitConfig | None = None,
) -> int:
    """1銘柄を過去全期間でシミュレートし、取引をジャーナルに記録する。

    Args:
        exit_config: 出口ルール。None なら既定（固定損切り -8% のみ）。

    Returns:
        記録した取引（往復）の件数。
    """
    config = exit_config or ExitConfig(stop_loss=DEFAULT_STOP)
    account = db.get_or_create_account(account_name)
    df = collector.load_prices(ticker)
    try:
        bench = collector.load_prices(BENCHMARK)
    except Exception:
        bench = None

    spec = strat.STRATEGIES[strategy]
    position = spec["func"](df, **params)
    held = position.shift(1).fillna(0).astype(int)  # 翌日約定

    open_price = df["Open"]
    close_price = df["Close"]
    alloc = account["initial_cash"] * ALLOC_FRAC
    atr_series = exits.atr(df, config.atr_period) if config.uses_atr else None

    records: list[tuple] = []
    in_pos = False
    entry_price = entry_date = None
    entry_feats: dict = {}
    peak = 0.0
    atr_at_entry: float | None = None
    shares = 0
    now = datetime.now().isoformat(timespec="seconds")

    for i in range(len(df)):
        date = df.index[i]
        prev = held.iloc[i - 1] if i > 0 else 0
        curr = held.iloc[i]
        price_open = float(open_price.iloc[i])

        # --- エントリー ---
        if not in_pos and prev == 0 and curr == 1:
            entry_price = price_open
            entry_date = date
            shares = int(alloc / entry_price) if entry_price > 0 else 0
            if shares <= 0:
                continue
            entry_feats = context.entry_features(df, date, bench)
            peak = entry_price
            atr_at_entry = (
                float(atr_series.iloc[i]) if atr_series is not None
                and pd.notna(atr_series.iloc[i]) else None
            )
            in_pos = True
            continue

        if not in_pos:
            continue

        # 建玉後の高値を更新（トレーリング用）
        close_now = float(close_price.iloc[i])
        peak = max(peak, close_now)

        # --- 出口ルール判定（保有中、当日終値ベース） ---
        rule_exit, rule_reason = exits.evaluate(
            config, close_now, entry_price, peak, atr_at_entry
        )

        # --- イグジット判定 ---
        if curr == 0 or rule_exit:
            # 売りシグナルは翌日始値、ルール出口は当日終値で約定
            if curr == 0:
                exit_price = price_open
                reason = strat.exit_reason(strategy, params)
            else:
                exit_price = close_now
                reason = rule_reason
            ret = (exit_price / entry_price) - 1 - 2 * COST
            pnl = shares * (exit_price - entry_price) - shares * entry_price * 2 * COST
            holding_days = int((date - entry_date).days)
            outcome = "win" if ret > 0 else ("loss" if ret < 0 else "even")

            records.append(
                (
                    account["id"], ticker, strategy, json.dumps(params, ensure_ascii=False),
                    shares,
                    entry_date.strftime("%Y-%m-%d"), round(entry_price, 2),
                    strat.entry_reason(strategy, params, entry_feats),
                    date.strftime("%Y-%m-%d"), round(exit_price, 2),
                    reason,
                    round(pnl, 0), round(ret * 100, 2), holding_days,
                    "closed", outcome,
                    entry_feats.get("market_regime"), entry_feats.get("trend_strength"),
                    entry_feats.get("entry_rsi"), entry_feats.get("volatility"),
                    now,
                )
            )
            in_pos = False

    if records:
        with db.connect() as conn:
            conn.executemany(
                "INSERT INTO trades ("
                "account_id, ticker, strategy, params, shares, "
                "entry_date, entry_price, entry_reason, "
                "exit_date, exit_price, exit_reason, "
                "pnl, return_pct, holding_days, status, outcome, "
                "market_regime, trend_strength, entry_rsi, volatility, created_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                records,
            )
        _recalc_cash(account["id"])

    return len(records)


def simulate_many(
    tickers: list[str],
    strategy: str,
    params: dict,
    account_name: str = "paper",
) -> dict[str, int]:
    """複数銘柄をまとめてシミュレートする。"""
    return {t: simulate(t, strategy, params, account_name) for t in tickers}


def _recalc_cash(account_id: int) -> None:
    """口座の現金を「初期資金 + 確定損益の合計」で更新する。"""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) AS total FROM trades "
            "WHERE account_id = ? AND status = 'closed'",
            (account_id,),
        ).fetchone()
        conn.execute(
            "UPDATE accounts SET cash = initial_cash + ? WHERE id = ?",
            (row["total"], account_id),
        )
