"""エントリー時の相場コンテキストを計算する。

「なぜ勝った/負けたのか」を後から分析するために、建玉した日の
相場状況（地合い・トレンド・過熱感・変動の大きさ）を数値化して記録する。
"""

from __future__ import annotations

import pandas as pd


def rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI(14) の系列を返す。"""
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def market_regime(benchmark_df: pd.DataFrame, date: pd.Timestamp) -> str:
    """その日の地合いを判定する。

    ベンチマーク（日経平均）の終値が自身の75日移動平均を上回っていれば
    「上昇相場」、下回っていれば「下落相場」とみなす。
    """
    if benchmark_df is None or benchmark_df.empty:
        return "不明"
    upto = benchmark_df.loc[:date]
    if len(upto) < 75:
        return "不明"
    close = upto["Close"]
    sma75 = close.rolling(75).mean().iloc[-1]
    return "上昇相場" if close.iloc[-1] > sma75 else "下落相場"


def entry_features(
    df: pd.DataFrame,
    date: pd.Timestamp,
    benchmark_df: pd.DataFrame | None = None,
) -> dict:
    """エントリー日の相場コンテキストを辞書で返す。

    - market_regime: 地合い（上昇相場/下落相場）
    - trend_strength: 終値が75日線から何%離れているか（正=上、負=下）
    - entry_rsi: RSI(14)
    - volatility: 直近20日の日次変動率の標準偏差(%)
    """
    upto = df.loc[:date]
    close = upto["Close"]

    sma75 = close.rolling(75).mean().iloc[-1] if len(close) >= 75 else close.mean()
    trend_strength = float((close.iloc[-1] / sma75 - 1) * 100) if sma75 else 0.0

    rsi = rsi_series(close).iloc[-1] if len(close) >= 15 else float("nan")
    vol = float(close.pct_change().tail(20).std() * 100) if len(close) >= 20 else 0.0

    return {
        "market_regime": (
            market_regime(benchmark_df, date) if benchmark_df is not None else "不明"
        ),
        "trend_strength": round(trend_strength, 2),
        "entry_rsi": round(float(rsi), 1) if pd.notna(rsi) else None,
        "volatility": round(vol, 2),
    }
