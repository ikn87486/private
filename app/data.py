"""Yahoo Finance から株価データを取得する。

yfinance を使い、取得結果は簡易的にメモリキャッシュする
（同じ銘柄・期間を何度もバックテストする際の再取得を避けるため）。
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

# (ticker, period, interval) -> DataFrame の簡易キャッシュ
_CACHE: dict[tuple[str, str, str], pd.DataFrame] = {}


def fetch_history(
    ticker: str,
    period: str = "5y",
    interval: str = "1d",
    use_cache: bool = True,
) -> pd.DataFrame:
    """株価の時系列データを取得する。

    Args:
        ticker: 証券コード（例: "7203.T"）。
        period: 取得期間（"1y", "2y", "5y", "10y", "max" など）。
        interval: 足の種類（"1d" 日足など）。
        use_cache: メモリキャッシュを使うか。増分更新で最新を取りに行くときは
            False を指定し、常駐プロセスで古いデータが返らないようにする。

    Returns:
        Open/High/Low/Close/Volume を列に持つ DataFrame（index は日付）。

    Raises:
        ValueError: データが取得できなかった場合。
    """
    key = (ticker, period, interval)
    if use_cache and key in _CACHE:
        return _CACHE[key].copy()

    df = yf.download(
        ticker,
        period=period,
        interval=interval,
        auto_adjust=True,
        progress=False,
    )

    if df is None or df.empty:
        raise ValueError(
            f"銘柄 {ticker} のデータを取得できませんでした。"
            "証券コードや期間を確認してください。"
        )

    # yfinance が MultiColumn を返す場合があるので平坦化する
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.dropna()
    _CACHE[key] = df.copy()
    return df
