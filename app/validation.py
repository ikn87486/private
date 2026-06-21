"""ウォークフォワード検証（過剰最適化の検出）。

過去を「学習期間（in-sample）」と「検証期間（out-of-sample）」に分け、
学習期間で選んだ最適パラメータを未知の検証期間に当てて成績を測る。
これを時間をずらしながら繰り返し、検証期間でも通用するかを評価する。

  学習でだけ高成績 → 過剰最適化（将来は危ない）
  検証でも安定して利益 → 信頼できる戦略

高速化のため、各パラメータ組のポジション系列は銘柄ごとに一度だけ計算し、
各ウィンドウでは日付スライスして評価する（指標のウォームアップも自然に確保される）。
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd

from . import collector
from . import strategies as strat

COST = 0.001


def _param_combos(strategy: str) -> list[dict]:
    """戦略の全パラメータ組み合わせ（不正な組を除外）を返す。"""
    spec = strat.STRATEGIES[strategy]
    names = list(spec["params"].keys())
    lists = [spec["params"][n][2] for n in names]
    combos = []
    for c in itertools.product(*lists):
        p = dict(zip(names, c))
        if not strat.is_valid_combo(strategy, p):
            continue
        combos.append(p)
    return combos


def _slice_metrics(
    df: pd.DataFrame, position: pd.Series, start, end
) -> tuple[float, float, int]:
    """[start, end) の期間だけで (リターン%, シャープ, 取引回数) を計算する。"""
    held = position.shift(1).fillna(0).astype(int)
    mask = (df.index >= start) & (df.index < end)
    if mask.sum() < 2:
        return 0.0, 0.0, 0

    daily = df["Close"].pct_change().fillna(0)
    strat_daily = (daily * held)[mask]

    # 取引コストを建玉日に概算で反映
    entries = ((held == 1) & (held.shift(1) == 0))[mask]
    n_trades = int(entries.sum())
    total_return = float((1 + strat_daily).prod() - 1) - n_trades * 2 * COST

    if strat_daily.std() > 0:
        sharpe = float(strat_daily.mean() / strat_daily.std() * math.sqrt(252))
    else:
        sharpe = 0.0
    return round(total_return * 100, 2), round(sharpe, 2), n_trades


def _windows(index: pd.DatetimeIndex, train_years: int, test_years: int):
    """学習・検証ウィンドウを時間をずらしながら生成する。"""
    start = index.min()
    last = index.max()
    out = []
    train_start = start
    while True:
        train_end = train_start + pd.DateOffset(years=train_years)
        test_end = train_end + pd.DateOffset(years=test_years)
        if test_end > last + pd.DateOffset(days=1):
            break
        out.append((train_start, train_end, test_end))
        train_start = train_start + pd.DateOffset(years=test_years)
    return out


def walk_forward(
    ticker: str,
    strategy: str,
    train_years: int = 3,
    test_years: int = 1,
    metric: str = "sharpe",
) -> dict:
    """1銘柄でウォークフォワード検証を実行する。

    各ウィンドウで学習期間の `metric`（既定: シャープレシオ）が最大のパラメータを選び、
    その設定で検証期間（未知データ）の成績を測る。
    """
    df = collector.load_prices(ticker)
    if len(df) < 252 * (train_years + test_years):
        return {"ticker": ticker, "windows": [], "ok": False,
                "reason": "データ期間が不足しています。"}

    combos = _param_combos(strategy)
    spec = strat.STRATEGIES[strategy]
    # パラメータ組ごとのポジションを一度だけ計算
    positions = {tuple(p.items()): spec["func"](df, **p) for p in combos}

    wins = _windows(df.index, train_years, test_years)
    metric_idx = 1 if metric == "sharpe" else 0  # (return, sharpe, trades)

    rows = []
    for train_start, train_end, test_end in wins:
        # 学習期間で最良パラメータを選ぶ
        best_p = None
        best_score = -1e9
        best_is = (0.0, 0.0, 0)
        for p in combos:
            pos = positions[tuple(p.items())]
            m = _slice_metrics(df, pos, train_start, train_end)
            score = m[metric_idx]
            if score > best_score:
                best_score, best_p, best_is = score, p, m
        if best_p is None:
            continue

        # 検証期間（未知データ）で成績を測る
        pos = positions[tuple(best_p.items())]
        oos = _slice_metrics(df, pos, train_end, test_end)
        rows.append({
            "train": f"{train_start:%Y/%m}〜{train_end:%Y/%m}",
            "test": f"{train_end:%Y/%m}〜{test_end:%Y/%m}",
            "params": best_p,
            "is_return": best_is[0],
            "is_sharpe": best_is[1],
            "oos_return": oos[0],
            "oos_sharpe": oos[1],
            "oos_trades": oos[2],
        })

    return _summarize(ticker, strategy, rows)


def _summarize(ticker: str, strategy: str, rows: list[dict]) -> dict:
    """ウィンドウ結果を集計し、信頼性の判定を付ける。"""
    if not rows:
        return {"ticker": ticker, "strategy": strategy, "windows": [], "ok": False,
                "reason": "検証ウィンドウを作れませんでした。"}

    oos_returns = [r["oos_return"] for r in rows]
    is_returns = [r["is_return"] for r in rows]
    n = len(rows)
    profitable = sum(1 for x in oos_returns if x > 0)

    # 検証期間の複利リターン（実運用に近い指標）
    oos_compound = float(np.prod([1 + x / 100 for x in oos_returns]) - 1) * 100
    is_avg = round(float(np.mean(is_returns)), 2)
    oos_avg = round(float(np.mean(oos_returns)), 2)

    # 判定: 検証期間で利益が出ているか、勝ちウィンドウの割合、学習との乖離
    win_ratio = profitable / n * 100
    if oos_compound > 0 and win_ratio >= 50:
        verdict, vclass = "信頼できる（未知データでも通用）", "good"
    elif is_avg > 5 and oos_avg <= 0:
        verdict, vclass = "過剰最適化の疑い（学習だけ良い）", "bad"
    else:
        verdict, vclass = "不安定・弱い（明確な優位性なし）", "weak"

    return {
        "ticker": ticker,
        "strategy": strategy,
        "ok": True,
        "windows": rows,
        "n_windows": n,
        "oos_compound": round(oos_compound, 1),
        "oos_avg": oos_avg,
        "is_avg": is_avg,
        "degradation": round(is_avg - oos_avg, 2),  # 学習→検証の劣化幅
        "win_ratio": round(win_ratio, 0),
        "verdict": verdict,
        "vclass": vclass,
    }


def scan_universe(
    tickers: list[str],
    strategy: str,
    train_years: int = 3,
    test_years: int = 1,
) -> dict:
    """ユニバース全体でウォークフォワード検証し、戦略の汎用性を評価する。"""
    results = []
    for t in tickers:
        try:
            r = walk_forward(t, strategy, train_years, test_years)
        except Exception:
            continue
        if r.get("ok"):
            results.append(r)

    if not results:
        return {"strategy": strategy, "results": [], "ok": False}

    good = [r for r in results if r["vclass"] == "good"]
    oos_avgs = [r["oos_avg"] for r in results]
    # 信頼できる戦略を上位に。同じ判定内では検証複利の高い順。
    order = {"good": 0, "weak": 1, "bad": 2}
    results.sort(key=lambda r: (order[r["vclass"]], -r["oos_compound"]))

    return {
        "strategy": strategy,
        "ok": True,
        "results": results,
        "n": len(results),
        "n_good": len(good),
        "good_ratio": round(len(good) / len(results) * 100, 0),
        "universe_oos_avg": round(float(np.mean(oos_avgs)), 2),
    }
