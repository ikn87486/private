"""短期〜中期（数日〜2週間）の上昇候補スクリーナー。

価格・出来高という「信頼できる一次データ」だけから、各銘柄の上昇しやすさを
**総合スコア**として算出する。スコアは calibration.py で過去データに照らして
較正され、「スコア帯ごとに実際どれだけ上がったか」（上昇確率・期待リターン幅）に
変換される。これにより「確率○%」は願望ではなく過去実績に裏打ちされた数字になる。

このモジュールはスコアの素となる特徴量計算とスコアリングの“唯一の実装”を持ち、
スクリーニング（screen）と較正（calibration.build_calibration）の双方が共有する。
既存の指標実装（context / strategies / exits）を再利用する。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from . import collector, context, db
from .exits import atr as atr_series
from .stocks import (
    HORIZONS,
    MARKET_BENCHMARK,
    UNIVERSE,
    market_of,
    stock_name,
)

# --- 調整可能なパラメータ ---------------------------------------------------

# スコア合成の重み（合計1.0）。
WEIGHTS = {
    "trend": 0.20,        # 75日線からの上方乖離（順張りの素地）
    "momentum": 0.25,     # 直近20日リターン + MACD上向き
    "pullback": 0.15,     # RSIが過熱でない（高値掴み回避）
    "rel_strength": 0.25, # 対指数の相対強さ
    "volume": 0.15,       # 出来高の盛り上がり
}

# 流動性の下限（売買代金=終値×出来高 の20日平均）。これ未満は対象外。
LIQUIDITY_MIN = {"JP": 5e8, "US": 5e7}  # JP: 5億円 / US: 5000万USD

ATR_MULT = 2.0  # 損切り幅 = ATR × この倍率
RR = 1.5        # リスクリワード比。利確幅 = 損切り幅 × RR（表示・記録・検証で共通）
MIN_BARS = 120  # スコア算出に必要な最低データ本数

# スコアリングが使う特徴量パネルのキー。
_SCORE_FEATURES = (
    "trend_strength",
    "mom_20d",
    "macd_hist",
    "rsi",
    "bb_pos",
    "rel_strength",
    "vol_ratio",
    "regime_down",
    "turnover_20d",
)


# --- 特徴量計算（先読み回避・全期間ベクトル化） ------------------------------


def feature_frame(df: pd.DataFrame, benchmark_df: pd.DataFrame | None) -> pd.DataFrame:
    """1銘柄の特徴量を日次系列（DataFrame）で返す。

    すべて「その日までのデータ」だけで決まる量なので先読みバイアスは無い。
    既存の指標実装（context.rsi_series, exits.atr など）を再利用する。
    """
    close = df["Close"]
    volume = df["Volume"]

    sma25 = close.rolling(25).mean()
    sma75 = close.rolling(75).mean()

    # MACDヒストグラム（strategies.macd と同じEMA設定）
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_hist = macd_line - macd_line.ewm(span=9, adjust=False).mean()

    # ボリンジャー内の相対位置 0..1（strategies.bollinger と同じ20日・2σ）
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid + 2.0 * bb_std
    bb_lower = bb_mid - 2.0 * bb_std
    bb_pos = (close - bb_lower) / (bb_upper - bb_lower).replace(0, 1e-9)

    mom_20d = close.pct_change(20) * 100

    # 相対強さ: 自身の20日リターン − ベンチマークの20日リターン
    if benchmark_df is not None and not benchmark_df.empty:
        bench_mom = benchmark_df["Close"].pct_change(20) * 100
        bench_mom = bench_mom.reindex(close.index, method="ffill")
        # 地合い: ベンチマーク終値 < 自身の75日線 → 下落相場（スコア減衰に使う）
        bench_sma75 = benchmark_df["Close"].rolling(75).mean()
        regime_down = (benchmark_df["Close"] < bench_sma75).astype(float)
        regime_down = regime_down.reindex(close.index, method="ffill").fillna(0)
    else:
        bench_mom = pd.Series(0.0, index=close.index)
        regime_down = pd.Series(0.0, index=close.index)
    rel_strength = mom_20d - bench_mom

    vol_ratio = volume.rolling(5).mean() / volume.rolling(20).mean().replace(0, 1e-9)
    turnover_20d = (close * volume).rolling(20).mean()

    return pd.DataFrame(
        {
            "close": close,
            "sma_aligned": (sma25 > sma75).astype(float),
            "trend_strength": (close / sma75 - 1) * 100,
            "mom_20d": mom_20d,
            "macd_hist": macd_hist,
            "rsi": context.rsi_series(close),
            "bb_pos": bb_pos,
            "rel_strength": rel_strength,
            "vol_ratio": vol_ratio,
            "atr": atr_series(df),
            "turnover_20d": turnover_20d,
            "regime_down": regime_down,
        }
    )


def compute_scores(panels: dict[str, pd.DataFrame], markets: dict[str, str]) -> pd.DataFrame:
    """特徴量パネル（各キー: [日付 × 銘柄] のDataFrame）から総合スコアを計算する。

    クロスセクション（同一日の全銘柄）でランク正規化し、絶対評価の項と合成する。
    screen（1日分）も calibration（全期間）もこの関数を共有することで、
    今日のスコアと過去の較正スコアが必ず同じ定義になる。

    Args:
        panels: _SCORE_FEATURES をキーに持つ [日付 × 銘柄] のDataFrame群。
        markets: {ticker: "JP"|"US"} 流動性しきい値の判定に使う。

    Returns:
        [日付 × 銘柄] の総合スコア（0..100）。流動性不足・データ不足は NaN。
    """
    # クロスセクション・ランク（0..100）。同一日の銘柄間で相対評価する。
    trend = panels["trend_strength"].rank(axis=1, pct=True) * 100
    momentum = panels["mom_20d"].rank(axis=1, pct=True) * 100
    momentum = (momentum + (panels["macd_hist"] > 0) * 8.0).clip(0, 100)
    relstr = panels["rel_strength"].rank(axis=1, pct=True) * 100

    # 絶対評価。RSIは55を山頂とする山型（過熱・売られすぎ両端で減点）。
    rsi = panels["rsi"]
    pullback = (100 - (rsi - 55).abs() * 2.5).clip(0, 100)
    pullback = (pullback - (panels["bb_pos"] > 0.85) * 25.0).clip(0, 100)
    # 出来高: 5/20日比 0.7→0点, 1.5→100点。
    volume = ((panels["vol_ratio"] - 0.7) / 0.8 * 100).clip(0, 100)

    # 欠損サブスコアは中立50で補完（一部指標欠損でも総合は出す）。
    score = (
        WEIGHTS["trend"] * trend.fillna(50)
        + WEIGHTS["momentum"] * momentum.fillna(50)
        + WEIGHTS["pullback"] * pullback.fillna(50)
        + WEIGHTS["rel_strength"] * relstr.fillna(50)
        + WEIGHTS["volume"] * volume.fillna(50)
    )

    # 地合いが下落相場の銘柄は順張りスコアを減衰。
    score = score.where(panels["regime_down"] != 1, score * 0.7)

    # 中核指標が揃わない（ウォームアップ）期間はスコア無効。
    core_valid = (
        panels["trend_strength"].notna()
        & panels["mom_20d"].notna()
        & panels["rsi"].notna()
    )
    score = score.where(core_valid)

    # 流動性フィルタ: 売買代金20日平均が市場別しきい値未満は除外。
    turnover = panels["turnover_20d"]
    min_by_col = pd.Series(
        {t: LIQUIDITY_MIN[markets.get(t, "US")] for t in turnover.columns}
    )
    liquid = turnover >= min_by_col
    score = score.where(liquid)

    return score.clip(0, 100)


# --- スクリーニング本体 -----------------------------------------------------


def _load_benchmarks() -> dict[str, pd.DataFrame]:
    """市場別ベンチマーク（^N225 / ^GSPC）をまとめて読み込む。"""
    out: dict[str, pd.DataFrame] = {}
    for market, sym in MARKET_BENCHMARK.items():
        try:
            out[market] = collector.load_prices(sym)
        except Exception:
            out[market] = pd.DataFrame()
    return out


def _build_panels(
    frames: dict[str, pd.DataFrame], keys: tuple[str, ...]
) -> dict[str, pd.DataFrame]:
    """{ticker: feature_frame} から {feature: [日付 × 銘柄]} パネルを作る。"""
    panels: dict[str, pd.DataFrame] = {}
    for key in keys:
        panels[key] = pd.DataFrame({t: f[key] for t, f in frames.items()})
    return panels


def build_score_panel(
    tickers: list[str] | None = None,
) -> tuple[pd.DataFrame | None, dict[str, pd.DataFrame], dict[str, str]]:
    """全銘柄の因果的な総合スコア行列 [日付 × 銘柄] を作る。

    較正（calibration）とフォワード・シミュレーション（screen_sim）が共有することで、
    今日のスコア・過去の較正・検証のスコアがすべて同一定義になることを保証する。

    Returns:
        (score[日付×銘柄], frames{ticker: feature_frame}, markets{ticker: 'JP'|'US'})。
        対象が無ければ (None, {}, {})。
    """
    tickers = tickers or list(UNIVERSE.keys())
    benchmarks = _load_benchmarks()
    frames: dict[str, pd.DataFrame] = {}
    markets: dict[str, str] = {}
    for t in tickers:
        try:
            df = collector.load_prices(t)
        except Exception:
            continue
        if df is None or len(df) < MIN_BARS:
            continue
        market = market_of(t)
        frames[t] = feature_frame(df, benchmarks.get(market))
        markets[t] = market
    if not frames:
        return None, {}, {}
    panels = _build_panels(frames, _SCORE_FEATURES)
    score = compute_scores(panels, markets)
    return score, frames, markets


def _reasons(row: pd.Series) -> list[str]:
    """特徴量から「なぜ上がりそうか」の根拠テキストを最大4つ生成する。"""
    out: list[str] = []
    if row.get("sma_aligned") == 1:
        out.append("上昇トレンド（25日線>75日線）")
    ts = row.get("trend_strength")
    if pd.notna(ts) and ts > 0:
        out.append(f"75日線から+{ts:.1f}%上方")
    if row.get("macd_hist", 0) > 0:
        out.append("MACDが上向き（勢いあり）")
    rsi = row.get("rsi")
    if pd.notna(rsi):
        if 45 <= rsi <= 65:
            out.append(f"RSI {rsi:.0f}（過熱なし）")
        elif rsi > 70:
            out.append(f"RSI {rsi:.0f}（過熱に注意）")
    rs = row.get("rel_strength")
    if pd.notna(rs) and rs > 0:
        out.append(f"対指数 +{rs:.1f}%（相対的に強い）")
    vr = row.get("vol_ratio")
    if pd.notna(vr) and vr > 1.1:
        out.append(f"出来高 {vr:.1f}倍（注目度上昇）")
    return out[:4]


def screen(
    tickers: list[str] | None = None,
    asof: str | None = None,
    with_earnings: bool = True,
) -> list[dict]:
    """全銘柄を評価し、1銘柄=1行の結果リストを総合スコア降順で返す。

    上昇確率・期待リターン幅は calibration テーブルから（スコア帯×期間で）引く。
    較正未生成のときは確率系を None にして、スコアと売買レベルだけ返す。
    """
    from . import calibration  # 循環import回避のため遅延import

    tickers = tickers or list(UNIVERSE.keys())
    benchmarks = _load_benchmarks()

    frames: dict[str, pd.DataFrame] = {}
    last_rows: dict[str, pd.Series] = {}
    markets: dict[str, str] = {}
    for t in tickers:
        try:
            df = collector.load_prices(t)
        except Exception:
            continue
        if df is None or len(df) < MIN_BARS:
            continue
        market = market_of(t)
        feats = feature_frame(df, benchmarks.get(market))
        if asof:
            feats = feats.loc[:asof]
        if feats.empty:
            continue
        frames[t] = feats
        last_rows[t] = feats.iloc[-1]
        markets[t] = market

    if not frames:
        return []

    # 最新日の1行クロスセクションでスコアを計算する。
    cross = {
        key: pd.DataFrame({t: [last_rows[t][key]] for t in frames}, index=["asof"])
        for key in _SCORE_FEATURES
    }
    score_row = compute_scores(cross, markets).iloc[0]

    snapshot_date = max(f.index[-1] for f in frames.values()).strftime("%Y-%m-%d")

    results: list[dict] = []
    for t in frames:
        score = score_row.get(t)
        if pd.isna(score):
            continue  # 流動性不足・データ不足は除外
        row = last_rows[t]
        entry = float(row["close"])
        atr_val = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
        # 出口レベルは ATR ベースで統一（表示・記録・シミュレーションで同一定義）。
        stop = round(entry - ATR_MULT * atr_val, 2) if atr_val > 0 else None
        target = round(entry + RR * ATR_MULT * atr_val, 2) if atr_val > 0 else None

        horizons: dict[int, dict] = {}
        for h in HORIZONS:
            cal = calibration.lookup(float(score), h)
            # 較正由来の ret_p25/50/75 は「統計的な期待リターン幅」（行動目標とは別）。
            horizons[h] = {
                "prob_up": cal.get("prob_up") if cal else None,
                "ret_p25": cal.get("ret_p25") if cal else None,
                "ret_p50": cal.get("ret_p50") if cal else None,
                "ret_p75": cal.get("ret_p75") if cal else None,
                "target": target,
                "hold_days": h,
            }

        warn = "unknown"
        if with_earnings:
            warn = _earnings_warn(t, snapshot_date)

        results.append(
            {
                "ticker": t,
                "name": stock_name(t),
                "market": markets[t],
                "score": round(float(score), 1),
                "entry": round(entry, 2),
                "stop": stop,
                "atr": round(atr_val, 2),
                "earnings_warn": warn,
                "reasons": _reasons(row),
                "horizons": horizons,
            }
        )

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def save_snapshot(results: list[dict], snapshot_date: str | None = None) -> None:
    """スクリーニング結果を screen_snapshots に保存する（期間ごとに1行）。"""
    import json

    snapshot_date = snapshot_date or datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in results:
        for h, hd in r["horizons"].items():
            rows.append(
                (
                    snapshot_date, r["ticker"], h, r["score"],
                    hd["prob_up"], hd["ret_p25"], hd["ret_p50"], hd["ret_p75"],
                    r["entry"], r["stop"], hd["target"], r["market"],
                    r["earnings_warn"], json.dumps(r["reasons"], ensure_ascii=False),
                    now,
                )
            )
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO screen_snapshots "
            "(snapshot_date, ticker, horizon, score, prob_up, ret_p25, ret_p50, "
            " ret_p75, entry, stop, target, market, earnings_warn, reasons, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(snapshot_date, ticker, horizon) DO UPDATE SET "
            "score=excluded.score, prob_up=excluded.prob_up, ret_p25=excluded.ret_p25, "
            "ret_p50=excluded.ret_p50, ret_p75=excluded.ret_p75, entry=excluded.entry, "
            "stop=excluded.stop, target=excluded.target, market=excluded.market, "
            "earnings_warn=excluded.earnings_warn, reasons=excluded.reasons",
            rows,
        )


def latest_snapshot() -> tuple[str | None, list[dict]]:
    """最新スナップショットを画面表示用の構造（1銘柄=1行）に組み立てて返す。"""
    import json

    with db.connect() as conn:
        row = conn.execute(
            "SELECT MAX(snapshot_date) AS d FROM screen_snapshots"
        ).fetchone()
        date = row["d"] if row else None
        if not date:
            return None, []
        rows = conn.execute(
            "SELECT * FROM screen_snapshots WHERE snapshot_date = ? "
            "ORDER BY score DESC",
            (date,),
        ).fetchall()

    by_ticker: dict[str, dict] = {}
    for r in rows:
        t = r["ticker"]
        if t not in by_ticker:
            by_ticker[t] = {
                "ticker": t,
                "name": stock_name(t),
                "market": r["market"],
                "score": r["score"],
                "entry": r["entry"],
                "stop": r["stop"],
                "earnings_warn": r["earnings_warn"],
                "reasons": json.loads(r["reasons"]) if r["reasons"] else [],
                "horizons": {},
            }
        by_ticker[t]["horizons"][r["horizon"]] = {
            "prob_up": r["prob_up"],
            "ret_p25": r["ret_p25"],
            "ret_p50": r["ret_p50"],
            "ret_p75": r["ret_p75"],
            "target": r["target"],
            "hold_days": r["horizon"],
        }

    results = sorted(by_ticker.values(), key=lambda x: (x["score"] or 0), reverse=True)
    return date, results


# --- 決算カレンダー（best-effort・1日キャッシュ） ---------------------------

_EARNINGS_KEY = "screen_earnings_cache"


def _earnings_warn(ticker: str, asof: str) -> str:
    """対象期間内（約3週間=最長2週間+余裕）に決算予定があるか。

    'yes'（決算近い）/ 'no'（当面なし）/ 'unknown'（取得不可）。
    yfinance はネットワーク依存・欠損があるため失敗は 'unknown' で握りつぶす。
    結果は settings に1日キャッシュして連打を避ける。
    """
    cache = db.get_setting(_EARNINGS_KEY, {}) or {}
    today = datetime.now().strftime("%Y-%m-%d")
    hit = cache.get(ticker)
    if hit and hit.get("fetched") == today:
        next_date = hit.get("next")
    else:
        next_date = _fetch_next_earnings(ticker)
        cache[ticker] = {"next": next_date, "fetched": today}
        db.set_setting(_EARNINGS_KEY, cache)

    if not next_date:
        return "unknown"
    try:
        base = datetime.strptime(asof, "%Y-%m-%d")
        nxt = datetime.strptime(next_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return "unknown"
    return "yes" if base <= nxt <= base + timedelta(days=21) else "no"


def _fetch_next_earnings(ticker: str) -> str | None:
    """yfinance から次回決算日（YYYY-MM-DD）を取得する。失敗時は None。"""
    try:
        import yfinance as yf

        tk = yf.Ticker(ticker)
        try:
            ed = tk.get_earnings_dates(limit=8)
        except Exception:
            ed = None
        now = pd.Timestamp.now(tz=None)
        if ed is not None and not ed.empty:
            idx = ed.index.tz_localize(None) if ed.index.tz else ed.index
            future = [d for d in idx if d >= now]
            if future:
                return min(future).strftime("%Y-%m-%d")
        cal = tk.calendar
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date") or []
            if dates:
                d = pd.Timestamp(dates[0])
                return d.strftime("%Y-%m-%d")
    except Exception:
        return None
    return None
