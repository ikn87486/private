"""スクリーナーのフォワード・シミュレーション（結果論を排した検証）。

過去のある期間について「もしスコア上位を機械的に売買していたら」を再現する。
重要なのは後出しをしないこと:
  - 銘柄選定はその日の終値までで決まる因果的スコア（screener.build_score_panel）。
  - 約定は選定日の翌営業日始値。
  - 損切り・利確・期限は建玉時に固定し、以後の各日で到来順に判定（outcomes.forward_exit）。

同じ入力なら必ず同じ結果になる（決定論）＝あと出しの余地が無いことの担保。
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime

import numpy as np
import pandas as pd

from . import backtest, collector, db, exits, outcomes, screener
from .stocks import MARKET_BENCHMARK, stock_name


def _rebalance_dates(index: pd.DatetimeIndex, start, end, rebalance: str) -> list:
    """リバランス日（建玉判定を行う日）の一覧を返す。"""
    idx = index[(index >= start) & (index <= end)]
    if rebalance == "daily":
        return list(idx)
    # weekly: 各 ISO 週の最初の営業日。
    out, seen = [], set()
    for d in idx:
        iso = d.isocalendar()
        key = (iso[0], iso[1])
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def run_simulation(
    start: str | None = None,
    end: str | None = None,
    horizon: int = 5,
    top_n: int = 5,
    rebalance: str = "weekly",
    capital: float = 1_000_000,
    cost: float = 0.001,
    tickers: list[str] | None = None,
) -> dict:
    """フォワード・シミュレーションを実行し、結果を返す＆DBに保存する。"""
    score, frames, markets = screener.build_score_panel(tickers)
    if score is None or score.empty:
        return {"ok": False, "reason": "スコアを計算できませんでした。先にデータを取得してください。"}

    index = score.index
    end = pd.Timestamp(end) if end else index.max()
    start = pd.Timestamp(start) if start else end - pd.DateOffset(years=2)
    reb_dates = _rebalance_dates(index, start, end, rebalance)
    if not reb_dates:
        return {"ok": False, "reason": "指定期間に営業日がありません。"}

    price_cache: dict[str, pd.DataFrame] = {}
    atr_cache: dict[str, pd.Series | None] = {}

    def get_df(t: str) -> pd.DataFrame:
        if t not in price_cache:
            try:
                price_cache[t] = collector.load_prices(t)
            except Exception:
                price_cache[t] = pd.DataFrame()
        return price_cache[t]

    def get_atr(t: str) -> pd.Series | None:
        if t not in atr_cache:
            df = get_df(t)
            atr_cache[t] = exits.atr(df) if not df.empty else None
        return atr_cache[t]

    # --- 建玉判定（各リバランス日、上位 top_n を翌寄りで建玉、出口を固定） ---
    trades: list[dict] = []
    open_until: dict[str, pd.Timestamp] = {}  # ticker -> 手仕舞い予定日

    for d in reb_dates:
        open_now = [t for t, ed in open_until.items() if ed > d]
        capacity = top_n - len(open_now)
        if capacity <= 0:
            continue
        ranked = score.loc[d].dropna().sort_values(ascending=False)
        added = 0
        for t in ranked.index:
            if added >= capacity:
                break
            if t in open_now:
                continue
            df = get_df(t)
            if df.empty:
                continue
            after = df.index[df.index > d]
            if len(after) == 0:
                continue
            p = int(df.index.get_loc(after[0]))
            atr_s = get_atr(t)
            if atr_s is None or p - 1 < 0 or pd.isna(atr_s.iloc[p - 1]) or atr_s.iloc[p - 1] <= 0:
                continue
            atr_prev = float(atr_s.iloc[p - 1])  # 建玉前日までのATR（先読み回避）
            entry_price = float(df["Open"].iloc[p])
            stop = entry_price - screener.ATR_MULT * atr_prev
            target = entry_price + screener.RR * screener.ATR_MULT * atr_prev
            res = outcomes.forward_exit(df, p, entry_price, stop, target, horizon, cost)
            if res is None:
                continue
            trades.append(
                {
                    "ticker": t,
                    "market": markets.get(t),
                    "entry_date": df.index[p],
                    "entry_price": round(entry_price, 2),
                    "exit_date": pd.Timestamp(res["exit_date"]),
                    "exit_price": res["exit_price"],
                    "return_pct": res["trade_return"],
                    "exit_reason": res["exit_reason"],
                    "score_at_entry": round(float(ranked[t]), 1),
                }
            )
            open_until[t] = pd.Timestamp(res["exit_date"])
            added += 1

    if not trades:
        return {"ok": False, "reason": "対象期間にトレードが発生しませんでした。"}

    # --- エクイティ曲線（保有玉の日次%リターンを均等平均：通貨中立） ---
    sim_end = max(tr["exit_date"] for tr in trades)
    master = index[(index >= start) & (index <= sim_end)]

    ret_cache: dict[str, pd.Series] = {}

    def get_ret(t: str) -> pd.Series:
        if t not in ret_cache:
            close = frames[t]["close"] if t in frames else get_df(t)["Close"]
            ret_cache[t] = close.reindex(master).ffill().pct_change().fillna(0)
        return ret_cache[t]

    cols: dict[int, pd.Series] = {}
    for i, tr in enumerate(trades):
        r = get_ret(tr["ticker"])
        mask = (master > tr["entry_date"]) & (master <= tr["exit_date"])
        s = pd.Series(np.nan, index=master)
        s[mask] = r[mask]
        held = master[mask]
        if len(held) > 0:  # 建玉日に往復コストを計上
            s.loc[held[0]] = s.loc[held[0]] - 2 * cost
        cols[i] = s
    contrib = pd.DataFrame(cols, index=master)

    port_ret = contrib.mean(axis=1).fillna(0)  # 保有玉が無い日は現金（0%）
    equity = (1 + port_ret).cumprod()

    # ベンチマーク: 日米指数の均等ブレンド（通貨中立）。
    bench_series = []
    for sym in MARKET_BENCHMARK.values():
        try:
            bdf = collector.load_prices(sym)
            bench_series.append(bdf["Close"].reindex(master).ffill().pct_change().fillna(0))
        except Exception:
            pass
    bench_ret = pd.concat(bench_series, axis=1).mean(axis=1) if bench_series else pd.Series(0.0, index=master)
    bench_equity = (1 + bench_ret).cumprod()

    # --- メトリクス ---
    rets = [tr["return_pct"] for tr in trades]
    win_rate = sum(1 for r in rets if r > 0) / len(rets) * 100
    total_return = (float(equity.iloc[-1]) - 1) * 100
    benchmark_return = (float(bench_equity.iloc[-1]) - 1) * 100
    max_dd = backtest._max_drawdown(equity)
    sharpe = float(port_ret.mean() / port_ret.std() * math.sqrt(252)) if port_ret.std() > 0 else 0.0

    params = {
        "start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"),
        "horizon": horizon, "top_n": top_n, "rebalance": rebalance, "cost": cost,
    }
    summary = {
        **params,
        "n_trades": len(trades),
        "win_rate": round(win_rate, 1),
        "avg_return": round(float(np.mean(rets)), 2),
        "total_return": round(total_return, 1),
        "benchmark_return": round(benchmark_return, 1),
        "excess": round(total_return - benchmark_return, 1),
        "max_dd": round(max_dd, 1),
        "sharpe": round(sharpe, 2),
    }

    run_id = _save(summary, params, trades, master, equity, bench_equity)
    summary["run_id"] = run_id

    step = max(1, len(equity) // 300)
    curve = [
        {
            "date": master[i].strftime("%Y-%m-%d"),
            "equity": round(float(equity.iloc[i]), 4),
            "benchmark": round(float(bench_equity.iloc[i]), 4),
        }
        for i in range(0, len(equity), step)
    ]
    reasons = dict(Counter(tr["exit_reason"] for tr in trades))
    trade_rows = sorted(trades, key=lambda x: x["entry_date"], reverse=True)[:100]
    display_trades = [
        {
            "ticker": tr["ticker"], "name": stock_name(tr["ticker"]), "market": tr["market"],
            "entry_date": tr["entry_date"].strftime("%Y-%m-%d"),
            "entry_price": tr["entry_price"],
            "exit_date": tr["exit_date"].strftime("%Y-%m-%d"),
            "exit_price": tr["exit_price"],
            "return_pct": tr["return_pct"], "exit_reason": tr["exit_reason"],
            "score_at_entry": tr["score_at_entry"],
        }
        for tr in trade_rows
    ]

    return {
        "ok": True,
        "summary": summary,
        "curve": curve,
        "trades": display_trades,
        "reasons": reasons,
    }


def _save(summary, params, trades, master, equity, bench_equity) -> int:
    """シミュレーション結果を DB に保存し、run_id を返す。"""
    now = datetime.now().isoformat(timespec="seconds")
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO sim_runs (created_at, params_json, start_date, end_date, horizon, "
            "n_trades, win_rate, total_return, benchmark_return, max_dd, sharpe) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                now, json.dumps(params, ensure_ascii=False), params["start"], params["end"],
                params["horizon"], summary["n_trades"], summary["win_rate"],
                summary["total_return"], summary["benchmark_return"], summary["max_dd"],
                summary["sharpe"],
            ),
        )
        run_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO sim_trades (run_id, ticker, market, entry_date, entry_price, "
            "exit_date, exit_price, return_pct, exit_reason, score_at_entry) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    run_id, tr["ticker"], tr["market"],
                    tr["entry_date"].strftime("%Y-%m-%d"), tr["entry_price"],
                    tr["exit_date"].strftime("%Y-%m-%d"), tr["exit_price"],
                    tr["return_pct"], tr["exit_reason"], tr["score_at_entry"],
                )
                for tr in trades
            ],
        )
        conn.executemany(
            "INSERT INTO sim_equity (run_id, date, equity, benchmark) VALUES (?,?,?,?)",
            [
                (run_id, master[i].strftime("%Y-%m-%d"),
                 round(float(equity.iloc[i]), 4), round(float(bench_equity.iloc[i]), 4))
                for i in range(len(master))
            ],
        )
    return run_id


def latest_run() -> dict | None:
    """最新のシミュレーション結果（サマリ＋曲線＋明細）を返す。無ければ None。"""
    with db.connect() as conn:
        run = conn.execute("SELECT * FROM sim_runs ORDER BY id DESC LIMIT 1").fetchone()
        if not run:
            return None
        rid = run["id"]
        eq = conn.execute(
            "SELECT date, equity, benchmark FROM sim_equity WHERE run_id = ? ORDER BY date",
            (rid,),
        ).fetchall()
        tr = conn.execute(
            "SELECT * FROM sim_trades WHERE run_id = ? ORDER BY entry_date DESC LIMIT 100",
            (rid,),
        ).fetchall()

    eq = [dict(r) for r in eq]
    step = max(1, len(eq) // 300)
    curve = [{"date": e["date"], "equity": e["equity"], "benchmark": e["benchmark"]}
             for e in eq[::step]]
    summary = dict(run)
    summary["excess"] = round((summary["total_return"] or 0) - (summary["benchmark_return"] or 0), 1)
    summary["start"] = summary.get("start_date")
    summary["end"] = summary.get("end_date")
    try:  # params_json から top_n / rebalance を復元（表示用）
        p = json.loads(summary.get("params_json") or "{}")
        summary["top_n"] = p.get("top_n")
        summary["rebalance"] = p.get("rebalance", "weekly")
    except Exception:
        pass
    trades = [
        {**dict(r), "name": stock_name(r["ticker"])} for r in tr
    ]
    reasons = dict(Counter(t["exit_reason"] for t in trades))
    return {"ok": True, "summary": summary, "curve": curve, "trades": trades, "reasons": reasons}
