"""スコアの較正（信頼性の裏取り）。

スクリーナーの総合スコアは、それ単体では「相対的な強さの順位」でしかない。
そこで過去データに照らし、「このスコア帯の銘柄は、その後N営業日で実際に何%上がったか」を
集計する。これにより今日のスコアを **上昇確率(%) と 期待リターン分布** に変換できる。

  スコアが高い帯ほど上昇確率が高い → スコアが機能している裏付け
  スコアと将来リターンが無相関 → スコアは当てにならない（画面でそれも分かる）

過去全期間で screener.compute_scores を再現し（screen と同一定義）、スコアを十分位で
区切って前向きリターンの分布を測り、screen_calibration テーブルへ保存する。
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from . import collector, db, screener
from .stocks import HORIZONS, UNIVERSE, market_of

N_BUCKETS = 10  # スコアの十分位（decile）


def build_calibration(
    tickers: list[str] | None = None,
    horizons: tuple[int, ...] = HORIZONS,
    lookback_years: int = 5,
) -> dict:
    """過去データでスコアを較正し、screen_calibration テーブルに保存する。

    Returns:
        サマリ {"ok", "n_tickers", "n_samples", "horizons"}。
    """
    tickers = tickers or list(UNIVERSE.keys())

    # 全期間のスコア行列（screen・シミュレーションと同一の定義）。
    score, frames, markets = screener.build_score_panel(tickers)
    if score is None:
        return {"ok": False, "reason": "対象データがありません。"}

    # 前向き N営業日リターン（feature_frame の close を流用）。
    fwd: dict[int, dict[str, pd.Series]] = {h: {} for h in horizons}
    for t, f in frames.items():
        close = f["close"]
        for h in horizons:
            fwd[h][t] = (close.shift(-h) / close - 1) * 100

    if not frames:
        return {"ok": False, "reason": "対象データがありません。"}

    # 直近 lookback_years に限定。
    cutoff = score.index.max() - pd.DateOffset(years=lookback_years)
    score = score[score.index >= cutoff]

    # 十分位の境界（プール全体のスコアから）。
    flat = score.values.flatten()
    flat = flat[~np.isnan(flat)]
    if flat.size < N_BUCKETS * 10:
        return {"ok": False, "reason": "較正に十分な標本がありません。"}
    edges = np.percentile(flat, [10, 20, 30, 40, 50, 60, 70, 80, 90])

    now = datetime.now().isoformat(timespec="seconds")
    rows: list[tuple] = []
    total_samples = 0

    for h in horizons:
        fpanel = pd.DataFrame(fwd[h]).reindex(index=score.index, columns=score.columns)
        s = score.values.flatten()
        f = fpanel.values.flatten()
        mask = ~np.isnan(s) & ~np.isnan(f)
        s, f = s[mask], f[mask]
        total_samples += int(s.size)
        bucket = np.digitize(s, edges)  # 0..9
        for b in range(N_BUCKETS):
            sel = f[bucket == b]
            if sel.size == 0:
                continue
            prob_up = float((sel > 0).mean() * 100)
            p25, p50, p75 = (float(x) for x in np.percentile(sel, [25, 50, 75]))
            low = float(edges[b - 1]) if b > 0 else 0.0
            high = float(edges[b]) if b < N_BUCKETS - 1 else 100.0
            rows.append(
                (h, b, low, high, round(prob_up, 1),
                 round(p25, 2), round(p50, 2), round(p75, 2), int(sel.size), now)
            )

    with db.connect() as conn:
        conn.execute("DELETE FROM screen_calibration")
        conn.executemany(
            "INSERT INTO screen_calibration "
            "(horizon, score_bucket, bucket_low, bucket_high, prob_up, "
            " ret_p25, ret_p50, ret_p75, n, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )

    _CACHE.clear()
    return {
        "ok": True,
        "n_tickers": len(frames),
        "n_samples": total_samples,
        "horizons": list(horizons),
        "updated_at": now,
    }


# --- 参照（キャッシュ付き） -------------------------------------------------

_CACHE: dict[int, list[dict]] = {}


def _get_buckets(horizon: int) -> list[dict]:
    """ある期間のバケット一覧（スコア昇順）を返す。DBから1度だけ読みキャッシュ。"""
    if horizon in _CACHE:
        return _CACHE[horizon]
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM screen_calibration WHERE horizon = ? ORDER BY score_bucket",
            (horizon,),
        ).fetchall()
    buckets = [dict(r) for r in rows]
    _CACHE[horizon] = buckets
    return buckets


def lookup(score: float, horizon: int) -> dict:
    """スコアの属するバケットから {prob_up, ret_p25, ret_p50, ret_p75} を返す。

    較正未生成なら空dict。スコアが範囲外なら最寄りの端のバケットを使う。
    `prob_up` はウォークフォワード実績で補正した値（補正未生成なら生値）。生値は `prob_up_raw`。
    """
    buckets = _get_buckets(horizon)
    if not buckets:
        return {}
    chosen = None
    for bk in buckets:
        if bk["bucket_low"] <= score < bk["bucket_high"]:
            chosen = bk
            break
    if chosen is None:
        chosen = buckets[-1] if score >= buckets[-1]["bucket_low"] else buckets[0]

    result = dict(chosen)  # キャッシュを汚さないようコピー
    raw = result.get("prob_up")
    if raw is not None:
        result["prob_up_raw"] = raw
        result["prob_up"] = calibrated_prob(raw, horizon)
    return result


def is_ready() -> bool:
    """較正テーブルが生成済みか。"""
    with db.connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM screen_calibration").fetchone()
    return bool(row and row["n"] > 0)


def updated_at() -> str | None:
    """較正テーブルの最終更新時刻。"""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT MAX(updated_at) AS t FROM screen_calibration"
        ).fetchone()
    return row["t"] if row else None


# --- 予測確率の精度検証（信頼性） -------------------------------------------


def _calibrate_in_memory(score_train: pd.DataFrame, fwd_train: pd.DataFrame):
    """学習部分のスコアと前向きリターンから (十分位境界, 各帯の上昇確率) を作る。"""
    flat = score_train.values.flatten()
    flat = flat[~np.isnan(flat)]
    if flat.size < 100:
        return None, None
    edges = np.percentile(flat, [10, 20, 30, 40, 50, 60, 70, 80, 90])
    s = score_train.values.flatten()
    fr = fwd_train.values.flatten()
    m = ~np.isnan(s) & ~np.isnan(fr)
    s, fr = s[m], fr[m]
    if s.size == 0:
        return None, None
    dec = np.digitize(s, edges)
    overall = (fr > 0).mean() * 100
    bucket_prob = np.full(N_BUCKETS, np.nan)
    for b in range(N_BUCKETS):
        sub = fr[dec == b]
        if sub.size > 0:
            bucket_prob[b] = (sub > 0).mean() * 100
    bucket_prob = np.where(np.isnan(bucket_prob), overall, bucket_prob)
    return edges, bucket_prob


def _fwd_panel(frames: dict[str, pd.DataFrame], h: int, index) -> pd.DataFrame:
    """前向き N営業日リターン(%)の [日付 × 銘柄] パネル。"""
    return pd.DataFrame(
        {t: (f["close"].shift(-h) / f["close"] - 1) * 100 for t, f in frames.items()}
    ).reindex(index=index)


def walk_forward_accuracy(
    horizons: tuple[int, ...] = HORIZONS, n_folds: int = 5,
    tickers: list[str] | None = None,
) -> dict:
    """ポイントインタイム検証で「予測確率 vs 実績の上昇割合」を測る。

    各フォールドは**開始より前のデータだけ**で較正を作り、フォールド内のスコアを確率に変換、
    実績（前向きリターン>0）と照合する。未来情報の混入が無い＝結果論ではない。
    """
    score, frames, _ = screener.build_score_panel(tickers)
    if score is None:
        return {"ok": False, "reason": "対象データがありません。"}

    index = score.index
    folds = np.array_split(np.arange(len(index)), n_folds)
    now = datetime.now().isoformat(timespec="seconds")
    rows: list[tuple] = []

    for h in horizons:
        fwd = _fwd_panel(frames, h, index)
        preds, reals, rets = [], [], []
        for fi in range(1, n_folds):  # 最初のフォールドは学習データが無いので除外
            test_pos = folds[fi]
            if len(test_pos) == 0:
                continue
            train_end = int(test_pos[0])
            edges, bucket_prob = _calibrate_in_memory(
                score.iloc[:train_end], fwd.iloc[:train_end]
            )
            if edges is None:
                continue
            s = score.iloc[test_pos].values.flatten()
            fr = fwd.iloc[test_pos].values.flatten()
            m = ~np.isnan(s) & ~np.isnan(fr)
            s, fr = s[m], fr[m]
            if s.size == 0:
                continue
            pred = bucket_prob[np.digitize(s, edges)]
            preds.append(pred)
            reals.append((fr > 0).astype(float) * 100)
            rets.append(fr)
        if not preds:
            continue
        pred = np.concatenate(preds)
        real = np.concatenate(reals)
        ret = np.concatenate(rets)
        pb = np.clip((pred // 10).astype(int), 0, N_BUCKETS - 1)
        for b in range(N_BUCKETS):
            sel = pb == b
            if sel.sum() == 0:
                continue
            rows.append(
                ("walkforward", h, b, round(float(pred[sel].mean()), 1),
                 round(float(real[sel].mean()), 1), round(float(ret[sel].mean()), 2),
                 int(sel.sum()), now)
            )

    _save_accuracy("walkforward", rows)
    build_probability_correction(horizons)  # 実績に合わせて表示確率の補正を更新
    return {"ok": True, "n_rows": len(rows), "updated_at": now}


# --- 確率のアウトオブサンプル補正（単調回帰） -------------------------------

_CORRECTION_KEY = "prob_correction"
_CORR_CACHE: dict | None = None


def _pav(values: list[float], weights: list[float]) -> list[float]:
    """重み付き単調回帰（pool-adjacent-violators）。非減少にフィットした値を返す。

    入力は説明変数（予測確率）で昇順に並んでいる前提。新規依存なしのnumpy/純Python実装。
    """
    blocks: list[list[float]] = []  # [value, weight, count]
    for v, w in zip(values, weights):
        blocks.append([v, w, 1])
        while len(blocks) >= 2 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, c2 = blocks.pop()
            v1, w1, c1 = blocks.pop()
            nw = w1 + w2
            blocks.append([(v1 * w1 + v2 * w2) / nw if nw else v1, nw, c1 + c2])
    out: list[float] = []
    for v, _w, c in blocks:
        out.extend([v] * int(c))
    return out


def build_probability_correction(horizons: tuple[int, ...] = HORIZONS) -> dict:
    """ウォークフォワードの (予測確率→実績) を単調回帰し、表示確率の補正カーブを保存する。

    高スコア帯の過信などを実績側へ写像する。補正は settings(JSON) に保存し、lookup が使う。
    """
    correction: dict[str, dict] = {}
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT horizon, predicted_prob, realized_prob, n FROM screen_accuracy "
            "WHERE method = 'walkforward' ORDER BY horizon, predicted_prob"
        ).fetchall()

    by_h: dict[int, list] = {}
    for r in rows:
        by_h.setdefault(r["horizon"], []).append(r)
    for h in horizons:
        items = by_h.get(h, [])
        if len(items) < 2:
            continue
        xs = [float(i["predicted_prob"]) for i in items]
        ys = [float(i["realized_prob"]) for i in items]
        ws = [max(int(i["n"]), 1) for i in items]
        fitted = _pav(ys, ws)  # 予測確率の昇順に並んだ実績を単調化
        correction[str(h)] = {"x": xs, "y": fitted}

    db.set_setting(_CORRECTION_KEY, correction)
    global _CORR_CACHE
    _CORR_CACHE = correction
    return {"ok": True, "horizons": list(correction.keys())}


def calibrated_prob(raw_prob: float, horizon: int) -> float:
    """生の予測確率を補正カーブで写像する。補正が無ければ生値をそのまま返す。"""
    global _CORR_CACHE
    if _CORR_CACHE is None:
        _CORR_CACHE = db.get_setting(_CORRECTION_KEY, {}) or {}
    curve = _CORR_CACHE.get(str(horizon))
    if not curve or len(curve.get("x", [])) < 2:
        return round(raw_prob, 1)
    y = float(np.interp(raw_prob, curve["x"], curve["y"]))  # 端点でクランプ
    return round(y, 1)


def live_accuracy(horizons: tuple[int, ...] = HORIZONS) -> dict:
    """実運用の記録（screen_outcomes）から予測確率 vs 実績を集計する。"""
    now = datetime.now().isoformat(timespec="seconds")
    with db.connect() as conn:
        data = conn.execute(
            "SELECT s.horizon AS horizon, s.prob_up AS prob_up, "
            "       o.hit_up AS hit_up, o.raw_return AS raw_return "
            "FROM screen_outcomes o "
            "JOIN screen_snapshots s "
            "  ON s.snapshot_date = o.snapshot_date AND s.ticker = o.ticker "
            "     AND s.horizon = o.horizon "
            "WHERE s.prob_up IS NOT NULL"
        ).fetchall()

    by: dict[tuple[int, int], list] = {}
    for r in data:
        b = min(int((r["prob_up"] or 0) // 10), N_BUCKETS - 1)
        by.setdefault((r["horizon"], b), []).append(r)

    rows: list[tuple] = []
    for (h, b), items in by.items():
        n = len(items)
        pred = sum(i["prob_up"] for i in items) / n
        real = sum(i["hit_up"] for i in items) / n * 100
        avg_ret = sum(i["raw_return"] for i in items) / n
        rows.append(("live", h, b, round(pred, 1), round(real, 1), round(avg_ret, 2), n, now))

    _save_accuracy("live", rows)
    return {"ok": True, "n_rows": len(rows), "updated_at": now}


def _save_accuracy(method: str, rows: list[tuple]) -> None:
    """screen_accuracy の指定 method を入れ替え保存する。"""
    with db.connect() as conn:
        conn.execute("DELETE FROM screen_accuracy WHERE method = ?", (method,))
        if rows:
            conn.executemany(
                "INSERT INTO screen_accuracy "
                "(method, horizon, prob_bucket, predicted_prob, realized_prob, "
                " avg_return, n, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )


def accuracy_table(method: str, horizons: tuple[int, ...] = HORIZONS) -> list[dict]:
    """画面表示用。予測確率帯ごとの (予測, 実績, 件数) を期間別に並べる（予測確率の高い順）。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM screen_accuracy WHERE method = ? ORDER BY prob_bucket DESC",
            (method,),
        ).fetchall()
    by_bucket: dict[int, dict] = {}
    for r in rows:
        b = r["prob_bucket"]
        entry = by_bucket.setdefault(
            b, {"bucket": b, "range": f"{b*10}〜{b*10+10}%", "by_h": {}}
        )
        entry["by_h"][r["horizon"]] = {
            "predicted": r["predicted_prob"],
            "realized": r["realized_prob"],
            "avg_return": r["avg_return"],
            "n": r["n"],
        }
    return [by_bucket[b] for b in sorted(by_bucket, reverse=True)]


def accuracy_updated_at(method: str) -> str | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT MAX(updated_at) AS t FROM screen_accuracy WHERE method = ?", (method,)
        ).fetchone()
    return row["t"] if row else None


def hit_rate_summary(horizons: tuple[int, ...] = HORIZONS) -> list[dict]:
    """画面下部表示用。スコア帯ごとの上昇確率・期待中央値を期間別に並べる。

    返す各行: {"bucket", "range", "by_h": {h: {prob_up, ret_p50, n}}}。
    バケット番号の降順（高スコア帯が上）で返す。
    """
    summary: dict[int, dict] = {}
    for h in horizons:
        for bk in _get_buckets(h):
            b = bk["score_bucket"]
            entry = summary.setdefault(
                b,
                {
                    "bucket": b,
                    "range": f"{bk['bucket_low']:.0f}〜{bk['bucket_high']:.0f}",
                    "by_h": {},
                },
            )
            entry["by_h"][h] = {
                "prob_up": bk["prob_up"],
                "ret_p50": bk["ret_p50"],
                "n": bk["n"],
            }
    return [summary[b] for b in sorted(summary, reverse=True)]
