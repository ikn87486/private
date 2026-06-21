"""売買シグナルを生成する戦略群。

各戦略は株価 DataFrame とパラメータを受け取り、
`position` 列（1=保有, 0=現金）を持つ Series を返す。
バックテスト側はこの position の変化を「売買」とみなす。
"""

from __future__ import annotations

import pandas as pd


def sma_cross(df: pd.DataFrame, short: int = 25, long: int = 75) -> pd.Series:
    """移動平均クロス戦略。

    短期移動平均が長期移動平均を上回っている間は保有（1）、
    下回っている間は現金（0）。いわゆるゴールデンクロスで買い、
    デッドクロスで売り。

    Args:
        short: 短期移動平均の期間。
        long: 長期移動平均の期間。
    """
    close = df["Close"]
    sma_short = close.rolling(window=short).mean()
    sma_long = close.rolling(window=long).mean()
    position = (sma_short > sma_long).astype(int)
    return position


def rsi_reversion(
    df: pd.DataFrame, period: int = 14, low: int = 30, high: int = 70
) -> pd.Series:
    """RSI 逆張り戦略。

    RSI が `low` 以下になったら買い、`high` 以上になったら売る。
    間の領域では直前のポジションを維持する。
    """
    close = df["Close"]
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = 100 - (100 / (1 + rs))

    position = pd.Series(index=close.index, dtype="float64")
    holding = 0
    for i, value in enumerate(rsi):
        if pd.isna(value):
            position.iloc[i] = holding
            continue
        if value <= low:
            holding = 1
        elif value >= high:
            holding = 0
        position.iloc[i] = holding
    return position.astype(int)


def breakout(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """ブレイクアウト戦略（ドンチアン風）。

    過去 `window` 日の高値を更新したら買い、
    過去 `window` 日の安値を割ったら売る。
    """
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    upper = high.rolling(window=window).max()
    lower = low.rolling(window=window).min()

    position = pd.Series(index=close.index, dtype="float64")
    holding = 0
    for i in range(len(close)):
        if i == 0 or pd.isna(upper.iloc[i - 1]) or pd.isna(lower.iloc[i - 1]):
            position.iloc[i] = holding
            continue
        price = close.iloc[i]
        if price >= upper.iloc[i - 1]:
            holding = 1
        elif price <= lower.iloc[i - 1]:
            holding = 0
        position.iloc[i] = holding
    return position.astype(int)


def _ema(series: pd.Series, span: int) -> pd.Series:
    """指数移動平均（EMA）。"""
    return series.ewm(span=span, adjust=False).mean()


def macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """MACD戦略（順張り）。

    MACD線（短期EMA − 長期EMA）がシグナル線（MACDのEMA）を上回っている間は
    保有（1）、下回っている間は現金（0）。トレンドの転換と勢いを捉える。
    """
    close = df["Close"]
    macd_line = _ema(close, fast) - _ema(close, slow)
    signal_line = _ema(macd_line, signal)
    return (macd_line > signal_line).astype(int)


def stochastic(
    df: pd.DataFrame, period: int = 14, low: int = 20, high: int = 80
) -> pd.Series:
    """ストキャスティクス逆張り戦略。

    %D（%K=直近period日の高安レンジ内での終値位置、の3日移動平均）が
    `low` 以下で買い、`high` 以上で売り。間は直前のポジションを維持する。
    """
    close = df["Close"]
    lowest = df["Low"].rolling(window=period).min()
    highest = df["High"].rolling(window=period).max()
    k = (close - lowest) / (highest - lowest).replace(0, 1e-9) * 100
    d = k.rolling(window=3).mean()

    position = pd.Series(index=close.index, dtype="float64")
    holding = 0
    for i, value in enumerate(d):
        if pd.isna(value):
            position.iloc[i] = holding
            continue
        if value <= low:
            holding = 1
        elif value >= high:
            holding = 0
        position.iloc[i] = holding
    return position.astype(int)


def bollinger(df: pd.DataFrame, window: int = 20, num_std: float = 2.0) -> pd.Series:
    """ボリンジャーバンド逆張り戦略。

    終値が下限バンド（移動平均 − num_std×標準偏差）以下で買い、
    上限バンド（移動平均 + num_std×標準偏差）以上で売り。間は維持。
    """
    close = df["Close"]
    mid = close.rolling(window=window).mean()
    std = close.rolling(window=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std

    position = pd.Series(index=close.index, dtype="float64")
    holding = 0
    for i in range(len(close)):
        if pd.isna(lower.iloc[i]) or pd.isna(upper.iloc[i]):
            position.iloc[i] = holding
            continue
        price = close.iloc[i]
        if price <= lower.iloc[i]:
            holding = 1
        elif price >= upper.iloc[i]:
            holding = 0
        position.iloc[i] = holding
    return position.astype(int)


# 戦略の定義。UI のセレクトボックスやパラメータ探索で使う。
#  - func: シグナル生成関数
#  - params: {パラメータ名: (ラベル, デフォルト値, 探索候補リスト)}
STRATEGIES: dict[str, dict] = {
    "sma_cross": {
        "label": "移動平均クロス",
        "func": sma_cross,
        "params": {
            "short": ("短期移動平均（日）", 25, [5, 10, 15, 20, 25]),
            "long": ("長期移動平均（日）", 75, [50, 75, 100, 150, 200]),
        },
    },
    "rsi_reversion": {
        "label": "RSI逆張り",
        "func": rsi_reversion,
        "params": {
            "period": ("RSI期間（日）", 14, [7, 14, 21]),
            "low": ("買いライン", 30, [20, 25, 30, 35]),
            "high": ("売りライン", 70, [65, 70, 75, 80]),
        },
    },
    "breakout": {
        "label": "ブレイクアウト",
        "func": breakout,
        "params": {
            "window": ("ブレイク判定期間（日）", 20, [10, 20, 40, 60]),
        },
    },
    "macd": {
        "label": "MACD",
        "func": macd,
        "params": {
            "fast": ("短期EMA（日）", 12, [8, 12, 15]),
            "slow": ("長期EMA（日）", 26, [20, 26, 30]),
            "signal": ("シグナルEMA（日）", 9, [9]),
        },
    },
    "stochastic": {
        "label": "ストキャスティクス",
        "func": stochastic,
        "params": {
            "period": ("期間（日）", 14, [9, 14, 21]),
            "low": ("買いライン", 20, [15, 20, 25]),
            "high": ("売りライン", 80, [75, 80, 85]),
        },
    },
    "bollinger": {
        "label": "ボリンジャーバンド",
        "func": bollinger,
        "params": {
            "window": ("期間（日）", 20, [10, 20, 30]),
            "num_std": ("標準偏差の倍率(σ)", 2.0, [1.5, 2.0, 2.5]),
        },
    },
}


# ---------------------------------------------------------------------------
# 戦略横断のヘルパー（パラメータ制約・売買理由）。
# 戦略追加時はここを1か所直せば、最適化・検証・売買理由が揃う。
# ---------------------------------------------------------------------------


def is_valid_combo(strategy: str, params: dict) -> bool:
    """パラメータの組み合わせが有効か（戦略別の制約）を判定する。

    最適化・ウォークフォワード検証で不正な組（短期≥長期など）を除外するのに使う。
    """
    if strategy == "sma_cross":
        return params["short"] < params["long"]
    if strategy == "macd":
        return params["fast"] < params["slow"]
    if strategy in ("rsi_reversion", "stochastic"):
        return params["low"] < params["high"]
    return True


def entry_reason(strategy: str, params: dict, feats: dict | None = None) -> str:
    """エントリー理由の文章を返す。"""
    feats = feats or {}
    if strategy == "sma_cross":
        return f"ゴールデンクロス（{params['short']}日線が{params['long']}日線を上抜け）"
    if strategy == "rsi_reversion":
        return f"RSI {feats.get('entry_rsi')} で売られすぎ → 反発を狙って買い"
    if strategy == "breakout":
        return f"直近{params['window']}日の高値を更新（上放れ）"
    if strategy == "macd":
        return f"MACD線がシグナル線を上抜け（{params['fast']}/{params['slow']}）"
    if strategy == "stochastic":
        return f"ストキャス%Dが{params['low']}以下で売られすぎ → 反発を狙って買い"
    if strategy == "bollinger":
        return f"終値が下限バンド（-{params['num_std']}σ）を割り込み → 逆張り買い"
    return "買いシグナル発生"


def exit_reason(
    strategy: str, params: dict, stopped: bool = False, stop_pct: float = -0.08
) -> str:
    """イグジット理由の文章を返す。"""
    if stopped:
        return f"損切り（{int(stop_pct * 100)}%下落でルール手仕舞い）"
    if strategy == "sma_cross":
        return f"デッドクロス（{params['short']}日線が{params['long']}日線を下抜け）"
    if strategy == "rsi_reversion":
        return "RSI が買われすぎ圏に到達 → 売り"
    if strategy == "breakout":
        return f"直近{params['window']}日の安値を割れ（下放れ）"
    if strategy == "macd":
        return f"MACD線がシグナル線を下抜け（{params['fast']}/{params['slow']}）"
    if strategy == "stochastic":
        return f"ストキャス%Dが{params['high']}以上で買われすぎ → 売り"
    if strategy == "bollinger":
        return f"終値が上限バンド（+{params['num_std']}σ）を上抜け → 売り"
    return "売りシグナル発生"
