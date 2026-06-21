"""バックテストエンジンとパラメータ探索。

position（1=保有/0=現金）の系列から、実際の損益・勝率・最大ドローダウンなどを
計算する。シグナルは「翌営業日の始値で約定」とみなして先読みバイアスを避ける。
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import strategies as strat


@dataclass
class Trade:
    """1回の売買（エントリー〜イグジット）。"""

    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float


@dataclass
class BacktestResult:
    """バックテスト結果のまとめ。"""

    ticker: str
    strategy: str
    params: dict
    total_return_pct: float          # 戦略の累積リターン(%)
    buy_hold_return_pct: float       # 単純保有(バイ&ホールド)のリターン(%)
    n_trades: int
    win_rate_pct: float
    avg_return_pct: float            # 1トレード平均リターン(%)
    max_drawdown_pct: float
    sharpe: float
    final_equity: float              # 初期資金を1.0とした最終資産
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)  # {date, equity, buyhold}


def _signal_to_trades(
    df: pd.DataFrame, position: pd.Series, cost: float
) -> tuple[list[Trade], pd.Series]:
    """position 系列を実際の売買と日次リターンに変換する。

    エントリー/イグジットは「シグナル発生の翌日始値」で約定。
    `cost` は片道の取引コスト（手数料+スリッページ, 例: 0.001 = 0.1%）。
    """
    open_price = df["Open"]
    # 翌日約定にするためポジションを1日ずらす
    held = position.shift(1).fillna(0).astype(int)

    trades: list[Trade] = []
    in_position = False
    entry_price = 0.0
    entry_date = ""

    for i in range(len(df)):
        prev = held.iloc[i - 1] if i > 0 else 0
        curr = held.iloc[i]
        date = df.index[i].strftime("%Y-%m-%d")
        price = float(open_price.iloc[i])

        if not in_position and prev == 0 and curr == 1:
            in_position = True
            entry_price = price
            entry_date = date
        elif in_position and curr == 0:
            ret = (price / entry_price) - 1 - 2 * cost
            trades.append(
                Trade(entry_date, date, entry_price, price, round(ret * 100, 2))
            )
            in_position = False

    # 最終日まで保有中なら最終終値で手仕舞い
    if in_position:
        last_price = float(df["Close"].iloc[-1])
        ret = (last_price / entry_price) - 1 - 2 * cost
        trades.append(
            Trade(
                entry_date,
                df.index[-1].strftime("%Y-%m-%d"),
                entry_price,
                last_price,
                round(ret * 100, 2),
            )
        )

    # 日次リターン系列（エクイティカーブ用）
    daily_ret = df["Close"].pct_change().fillna(0)
    strat_daily = daily_ret * held
    return trades, strat_daily


def _max_drawdown(equity: pd.Series) -> float:
    """最大ドローダウン(%)を計算する（正の値で返す）。"""
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    return float(abs(drawdown.min()) * 100)


def run_backtest(
    df: pd.DataFrame,
    ticker: str,
    strategy: str,
    params: dict,
    cost: float = 0.001,
) -> BacktestResult:
    """単一銘柄・単一パラメータでバックテストを実行する。"""
    spec = strat.STRATEGIES[strategy]
    position = spec["func"](df, **params)

    trades, strat_daily = _signal_to_trades(df, position, cost)

    equity = (1 + strat_daily).cumprod()
    buyhold = (1 + df["Close"].pct_change().fillna(0)).cumprod()

    returns = [t.return_pct for t in trades]
    wins = [r for r in returns if r > 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
    avg_return = (sum(returns) / len(returns)) if returns else 0.0

    # シャープレシオ（年率化, リスクフリーレート0と仮定）
    if strat_daily.std() > 0:
        sharpe = float(strat_daily.mean() / strat_daily.std() * math.sqrt(252))
    else:
        sharpe = 0.0

    # エクイティカーブは表示用に間引く（最大300点）
    step = max(1, len(equity) // 300)
    curve = [
        {
            "date": df.index[i].strftime("%Y-%m-%d"),
            "equity": round(float(equity.iloc[i]), 4),
            "buyhold": round(float(buyhold.iloc[i]), 4),
        }
        for i in range(0, len(equity), step)
    ]

    return BacktestResult(
        ticker=ticker,
        strategy=strategy,
        params=params,
        total_return_pct=round(float(equity.iloc[-1] - 1) * 100, 2),
        buy_hold_return_pct=round(float(buyhold.iloc[-1] - 1) * 100, 2),
        n_trades=len(trades),
        win_rate_pct=round(win_rate, 1),
        avg_return_pct=round(avg_return, 2),
        max_drawdown_pct=round(_max_drawdown(equity), 2),
        sharpe=round(sharpe, 2),
        final_equity=round(float(equity.iloc[-1]), 4),
        trades=trades,
        equity_curve=curve,
    )


def optimize(
    df: pd.DataFrame,
    ticker: str,
    strategy: str,
    cost: float = 0.001,
) -> list[BacktestResult]:
    """戦略のパラメータを総当たりで探索し、利益が出る条件を洗い出す。

    「どういう条件なら利益が出るか」を計算する中心機能。
    各パラメータの候補リストの全組み合わせをバックテストし、
    累積リターンの高い順に並べて返す。
    """
    spec = strat.STRATEGIES[strategy]
    param_names = list(spec["params"].keys())
    candidate_lists = [spec["params"][name][2] for name in param_names]

    results: list[BacktestResult] = []
    for combo in itertools.product(*candidate_lists):
        params = dict(zip(param_names, combo))

        # 不正な組み合わせ（短期≥長期など）は除外
        if not strat.is_valid_combo(strategy, params):
            continue

        try:
            res = run_backtest(df, ticker, strategy, params, cost)
        except Exception:
            continue
        results.append(res)

    results.sort(key=lambda r: r.total_return_pct, reverse=True)
    return results
