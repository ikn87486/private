"""価格データの収集と蓄積。

yfinance から長期の日足を取得し、SQLite の prices テーブルに溜める。
一度溜めたデータは DB から読み出すので、毎回ネットワークに取りに行かずに済む。
"""

from __future__ import annotations

import pandas as pd

from . import data, db
from .stocks import BENCHMARK, STOCKS


def store_prices(ticker: str, df: pd.DataFrame) -> int:
    """DataFrame を prices テーブルに upsert する。追加/更新した行数を返す。"""
    rows = [
        (
            ticker,
            idx.strftime("%Y-%m-%d"),
            float(r["Open"]),
            float(r["High"]),
            float(r["Low"]),
            float(r["Close"]),
            float(r["Volume"]),
        )
        for idx, r in df.iterrows()
    ]
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO prices (ticker, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker, date) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, "
            "close=excluded.close, volume=excluded.volume",
            rows,
        )
    return len(rows)


def collect(tickers: list[str], period: str = "max") -> dict[str, int]:
    """指定銘柄の長期データをまとめて取得し DB に蓄積する。

    Returns:
        {ticker: 取得行数} の辞書。失敗した銘柄は値が 0。
    """
    result: dict[str, int] = {}
    for ticker in tickers:
        try:
            df = data.fetch_history(ticker, period=period)
            result[ticker] = store_prices(ticker, df)
        except Exception:
            result[ticker] = 0
    return result


def collect_all(period: str = "max") -> dict[str, int]:
    """全ユニバース + ベンチマーク指数を蓄積する。"""
    return collect(list(STOCKS.keys()) + [BENCHMARK], period=period)


def update_latest(tickers: list[str]) -> dict[str, int]:
    """各銘柄の最新データだけを取得して追記する（増分更新）。

    DB の最新日以降の新しいバーだけを upsert するので、毎日の自動実行で
    全期間を取り直さずに済む。

    Returns:
        {ticker: 新規に追記した行数} の辞書。
    """
    result: dict[str, int] = {}
    for ticker in tickers:
        try:
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT MAX(date) AS last FROM prices WHERE ticker = ?", (ticker,)
                ).fetchone()
            last = row["last"] if row else None

            # 直近1か月を取得し、DB最新日より新しい行だけ残す（キャッシュは使わない）
            df = data.fetch_history(ticker, period="1mo", use_cache=False)
            if last is not None:
                df = df[df.index > pd.Timestamp(last)]
            result[ticker] = store_prices(ticker, df) if not df.empty else 0
        except Exception as e:  # noqa: BLE001 - 失敗は握りつぶさず記録
            print(f"[update_latest] {ticker} の更新に失敗: {e}")
            result[ticker] = 0
    return result


def latest_date(ticker: str) -> str | None:
    """DB に入っている最新の日付（YYYY-MM-DD）を返す。"""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT MAX(date) AS last FROM prices WHERE ticker = ?", (ticker,)
        ).fetchone()
    return row["last"] if row else None


def load_prices(ticker: str) -> pd.DataFrame:
    """DB から価格データを読み出して DataFrame で返す。

    DB に無ければ自動で取得・蓄積してから返す（オンデマンド収集）。
    """
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM prices "
            "WHERE ticker = ? ORDER BY date",
            (ticker,),
        ).fetchall()

    if not rows:
        df = data.fetch_history(ticker, period="max")
        store_prices(ticker, df)
        return df

    df = pd.DataFrame(rows, columns=["date", "Open", "High", "Low", "Close", "Volume"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    return df


def coverage() -> list[dict]:
    """各銘柄について DB に溜まっているデータの範囲を返す（蓄積状況の確認用）。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT ticker, COUNT(*) AS n, MIN(date) AS start, MAX(date) AS end "
            "FROM prices GROUP BY ticker ORDER BY ticker"
        ).fetchall()
    return [dict(r) for r in rows]
