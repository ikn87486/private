"""予測の実績照合（記録）。

`screen_snapshots` に保存した各予測について、期限が到来したら実際の後続価格から
損益を確定する。結果論（あと出し）を避けるため、建玉は予測日の翌営業日始値で行い、
損切り・利確・期限は予測時に固定した値で前向きに（到来順に）判定する。

`forward_exit` はフォワード・シミュレータ（screen_sim.py）からも共有する“出口判定の唯一の実装”。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from . import collector, db

COST = 0.001  # 片道の取引コスト（手数料+スリッページ）


def forward_exit(
    df: pd.DataFrame,
    entry_pos: int,
    entry_price: float,
    stop: float | None,
    target: float | None,
    horizon: int,
    cost: float = COST,
) -> dict | None:
    """建玉後の出口を前向きに判定する（後から最適点を選ばない）。

    Args:
        df: Open/High/Low/Close を持つ価格DataFrame（DatetimeIndex）。
        entry_pos: 建玉するバーの位置（このバーの Open で約定済みとみなす）。保有1日目。
        entry_price: 約定値（通常 df.Open[entry_pos]）。
        stop / target: 予測時に固定した損切り・利確の価格水準（None なら無効）。
        horizon: 最大保有営業日数。期限内に stop/target が出なければ期限日終値で手仕舞い。

    Returns:
        {exit_pos, exit_date, exit_price, exit_reason, trade_return, raw_return}。
        期限まで価格が無い（未成熟）場合は None。
    """
    last = len(df) - 1
    deadline = entry_pos + horizon - 1
    if entry_pos < 0 or deadline > last or entry_price <= 0:
        return None

    opens = df["Open"]; highs = df["High"]; lows = df["Low"]; closes = df["Close"]

    exit_pos = deadline
    exit_price = float(closes.iloc[deadline])
    exit_reason = "timeout"

    for i in range(entry_pos, deadline + 1):
        o = float(opens.iloc[i]); h = float(highs.iloc[i]); lo = float(lows.iloc[i])
        # 同日に両方到達したら不利な損切りを優先（保守的）。
        if stop is not None and lo <= stop:
            exit_pos = i
            exit_price = min(o, stop)  # 寄りで割れていれば寄りで約定
            exit_reason = "stop"
            break
        if target is not None and h >= target:
            exit_pos = i
            exit_price = max(o, target)  # 寄りで超えていれば寄りで約定
            exit_reason = "target"
            break

    trade_return = (exit_price / entry_price - 1 - 2 * cost) * 100
    raw_return = (float(closes.iloc[deadline]) / entry_price - 1) * 100
    return {
        "exit_pos": exit_pos,
        "exit_date": df.index[exit_pos].strftime("%Y-%m-%d"),
        "exit_price": round(exit_price, 2),
        "exit_reason": exit_reason,
        "trade_return": round(trade_return, 2),
        "raw_return": round(raw_return, 2),
    }


def _entry_position(df: pd.DataFrame, snapshot_date: str) -> int | None:
    """予測日の翌営業日（最初に snapshot_date より後にあるバー）の位置を返す。"""
    after = df.index[df.index > pd.Timestamp(snapshot_date)]
    if len(after) == 0:
        return None
    return int(df.index.get_loc(after[0]))


def fill_outcomes(asof: str | None = None) -> dict:
    """期限到来済み・未記入の予測に実績を記入する。

    Returns:
        {"filled": 記入件数, "pending": 未成熟で見送った件数}。
    """
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT s.snapshot_date, s.ticker, s.horizon, s.stop, s.target "
            "FROM screen_snapshots s "
            "LEFT JOIN screen_outcomes o "
            "  ON o.snapshot_date = s.snapshot_date AND o.ticker = s.ticker "
            "     AND o.horizon = s.horizon "
            "WHERE o.snapshot_date IS NULL"
        ).fetchall()

    cache: dict[str, pd.DataFrame] = {}
    filled: list[tuple] = []
    pending = 0
    now = datetime.now().isoformat(timespec="seconds")

    for r in rows:
        ticker = r["ticker"]
        df = cache.get(ticker)
        if df is None:
            try:
                df = collector.load_prices(ticker)
            except Exception:
                df = pd.DataFrame()
            cache[ticker] = df
        if df.empty:
            pending += 1
            continue

        p = _entry_position(df, r["snapshot_date"])
        if p is None:
            pending += 1
            continue
        entry_fill = float(df["Open"].iloc[p])
        res = forward_exit(df, p, entry_fill, r["stop"], r["target"], r["horizon"])
        if res is None:
            pending += 1  # 期限まで価格が揃っていない（未成熟）
            continue

        filled.append(
            (
                r["snapshot_date"], ticker, r["horizon"], round(entry_fill, 2),
                res["exit_date"], res["exit_price"], res["exit_reason"],
                res["trade_return"], res["raw_return"],
                1 if res["raw_return"] > 0 else 0, now,
            )
        )

    if filled:
        with db.connect() as conn:
            conn.executemany(
                "INSERT INTO screen_outcomes "
                "(snapshot_date, ticker, horizon, entry_fill, exit_date, exit_price, "
                " exit_reason, trade_return, raw_return, hit_up, filled_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(snapshot_date, ticker, horizon) DO NOTHING",
                filled,
            )

    return {"filled": len(filled), "pending": pending}


def outcomes_summary() -> dict:
    """記入済み実績のかんたんな集計（画面表示用）。"""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, "
            "       AVG(CASE WHEN trade_return > 0 THEN 1.0 ELSE 0.0 END)*100 AS win_rate, "
            "       AVG(trade_return) AS avg_return, "
            "       AVG(hit_up)*100 AS hit_rate "
            "FROM screen_outcomes"
        ).fetchone()
    return {
        "n": row["n"] or 0,
        "win_rate": round(row["win_rate"], 1) if row["win_rate"] is not None else None,
        "avg_return": round(row["avg_return"], 2) if row["avg_return"] is not None else None,
        "hit_rate": round(row["hit_rate"], 1) if row["hit_rate"] is not None else None,
    }
