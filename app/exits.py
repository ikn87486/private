"""出口（手仕舞い）ルールの集約モジュール。

固定損切り・固定利確・トレーリングストップ・ATRベース損切りを1か所で判定し、
ヒストリカルのシミュレータ（simulator.py）とフォワードの自動売買（live.py）の
両方が同じロジックを共有する。

evaluate() はスカラ引数の純関数。peak（建玉後の高値）は呼び出し側で増分管理し、
ATRは建玉時の値を渡す設計で、O(n^2) の再計算を避ける。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class ExitConfig:
    """出口ルールの設定。None のルールは無効。"""

    stop_loss: float | None = -0.08      # 固定損切り（例 -0.08 = -8%）
    take_profit: float | None = None     # 固定利確（例 0.20 = +20%）
    trailing_stop: float | None = None   # トレーリング幅（例 0.10 = 高値から-10%）
    atr_period: int = 14                 # ATRの期間
    atr_mult: float | None = None        # ATR損切りの倍率（例 2.5）

    def to_dict(self) -> dict:
        return {
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "trailing_stop": self.trailing_stop,
            "atr_period": self.atr_period,
            "atr_mult": self.atr_mult,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "ExitConfig":
        if not d:
            return cls()
        return cls(
            stop_loss=d.get("stop_loss", -0.08),
            take_profit=d.get("take_profit"),
            trailing_stop=d.get("trailing_stop"),
            atr_period=d.get("atr_period", 14),
            atr_mult=d.get("atr_mult"),
        )

    @property
    def uses_atr(self) -> bool:
        return self.atr_mult is not None


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR（Average True Range）の系列を返す。

    True Range = max(高値-安値, |高値-前日終値|, |安値-前日終値|) の period 日平均。
    """
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period).mean()


def evaluate(
    config: ExitConfig,
    price: float,
    entry_price: float,
    peak_price: float,
    atr_at_entry: float | None,
) -> tuple[bool, str | None]:
    """保有中ポジションについて手仕舞うべきか判定する。

    Args:
        price: 現在値（終値）。
        entry_price: 建玉値。
        peak_price: 建玉後の高値（呼び出し側で max を更新して渡す）。
        atr_at_entry: 建玉時のATR値（ATR損切りを使う場合のみ必要）。

    Returns:
        (手仕舞うか, 理由テキスト)。手仕舞わないなら (False, None)。
    """
    if entry_price <= 0:
        return False, None
    unrealized = price / entry_price - 1

    # 1. 固定利確
    if config.take_profit is not None and unrealized >= config.take_profit:
        return True, f"利確（+{round(config.take_profit * 100)}%到達）"

    # 2. トレーリングストップ（高値から trailing_stop 下落）
    if config.trailing_stop is not None and peak_price > entry_price:
        if price <= peak_price * (1 - config.trailing_stop):
            return True, f"トレーリングストップ（高値から-{round(config.trailing_stop * 100)}%）"

    # 3. ATRベース損切り
    if config.atr_mult is not None and atr_at_entry and atr_at_entry > 0:
        if price <= entry_price - config.atr_mult * atr_at_entry:
            return True, f"ATR損切り（{config.atr_mult}×ATR）"

    # 4. 固定損切り
    if config.stop_loss is not None and unrealized <= config.stop_loss:
        return True, f"固定損切り（{round(config.stop_loss * 100)}%）"

    return False, None
