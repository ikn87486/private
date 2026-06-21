"""取引ジャーナルの分析（勝因・敗因の自動抽出）。

蓄積した取引を「勝ち」と「負け」に分け、エントリー時の相場コンテキストを
比較して、何が勝ち/負けに効いていたかを言葉で導き出す。
"""

from __future__ import annotations

from statistics import mean

from . import db
from .stocks import stock_name


def _trades(account_id: int) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE account_id = ? AND status = 'closed' "
            "ORDER BY exit_date",
            (account_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def summary(account_id: int) -> dict:
    """口座全体の成績サマリーを返す。"""
    trades = _trades(account_id)
    if not trades:
        return {"n": 0}

    wins = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    total_pnl = sum(t["pnl"] for t in trades)
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)

    avg_win = mean(t["return_pct"] for t in wins) if wins else 0
    avg_loss = mean(t["return_pct"] for t in losses) if losses else 0
    # 期待値 = 勝率×平均利益 + 敗率×平均損失（1トレードあたり期待リターン%）
    win_rate = len(wins) / len(trades)
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

    return {
        "n": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate * 100, 1),
        "total_pnl": round(total_pnl),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        # プロフィットファクター = 総利益 / 総損失（1超で利益体質）
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "expectancy_pct": round(expectancy, 2),
        "avg_holding_days": round(mean(t["holding_days"] for t in trades), 1),
    }


def _avg(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(mean(vals), 2) if vals else None


def attribution(account_id: int) -> dict:
    """勝ちトレードと負けトレードのコンテキストを比較する。"""
    trades = _trades(account_id)
    wins = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    if not wins or not losses:
        return {}

    def regime_ratio(rows: list[dict]) -> float | None:
        tagged = [r for r in rows if r["market_regime"] in ("上昇相場", "下落相場")]
        if not tagged:
            return None
        up = [r for r in tagged if r["market_regime"] == "上昇相場"]
        return round(len(up) / len(tagged) * 100, 1)

    return {
        "win": {
            "n": len(wins),
            "uptrend_ratio": regime_ratio(wins),
            "trend_strength": _avg(wins, "trend_strength"),
            "entry_rsi": _avg(wins, "entry_rsi"),
            "volatility": _avg(wins, "volatility"),
            "holding_days": _avg(wins, "holding_days"),
        },
        "loss": {
            "n": len(losses),
            "uptrend_ratio": regime_ratio(losses),
            "trend_strength": _avg(losses, "trend_strength"),
            "entry_rsi": _avg(losses, "entry_rsi"),
            "volatility": _avg(losses, "volatility"),
            "holding_days": _avg(losses, "holding_days"),
        },
    }


def insights(account_id: int) -> list[str]:
    """勝因・敗因を自然な日本語の気づきに変換する（自動の振り返り）。"""
    attr = attribution(account_id)
    if not attr:
        return ["まだ十分な取引データがありません。シミュレーションを実行して取引を溜めてください。"]

    w, l = attr["win"], attr["loss"]
    out: list[str] = []

    # 地合い
    if w["uptrend_ratio"] is not None and l["uptrend_ratio"] is not None:
        diff = w["uptrend_ratio"] - l["uptrend_ratio"]
        if abs(diff) >= 15:
            if diff > 0:
                out.append(
                    f"📈 地合いが効いている：勝ちトレードは上昇相場での建玉が{w['uptrend_ratio']}%、"
                    f"負けは{l['uptrend_ratio']}%。下落相場でのエントリーを減らすと改善しそう。"
                )
            else:
                out.append(
                    f"🔄 逆張りが効いている：負けトレードほど上昇相場で建てている"
                    f"（勝ち{w['uptrend_ratio']}% vs 負け{l['uptrend_ratio']}%）。"
                )

    # トレンドの強さ
    if w["trend_strength"] is not None and l["trend_strength"] is not None:
        if abs(w["trend_strength"] - l["trend_strength"]) >= 2:
            out.append(
                f"📊 エントリー時の75日線からの乖離：勝ち {w['trend_strength']}% / "
                f"負け {l['trend_strength']}%。"
                + ("上昇基調で入るほど勝ちやすい傾向。" if w["trend_strength"] > l["trend_strength"]
                   else "上がりすぎてから入ると負けやすい傾向（高値掴み）。")
            )

    # RSI
    if w["entry_rsi"] is not None and l["entry_rsi"] is not None:
        if abs(w["entry_rsi"] - l["entry_rsi"]) >= 5:
            out.append(
                f"🌡 エントリー時RSI：勝ち {w['entry_rsi']} / 負け {l['entry_rsi']}。"
                + ("過熱した状態で入ると負けやすい。" if l["entry_rsi"] > w["entry_rsi"]
                   else "売られすぎで入った方が勝ちやすい。")
            )

    # ボラティリティ
    if w["volatility"] is not None and l["volatility"] is not None:
        if abs(w["volatility"] - l["volatility"]) >= 0.5:
            out.append(
                f"💥 値動きの荒さ（直近20日）：勝ち {w['volatility']}% / 負け {l['volatility']}%。"
                + ("変動が大きい局面は負けやすい。" if l["volatility"] > w["volatility"]
                   else "変動が大きい局面の方が利益を伸ばせている。")
            )

    # 保有期間
    if w["holding_days"] is not None and l["holding_days"] is not None:
        if abs(w["holding_days"] - l["holding_days"]) >= 5:
            out.append(
                f"⏱ 平均保有日数：勝ち {w['holding_days']}日 / 負け {l['holding_days']}日。"
                + ("勝ちは伸ばし、負けは早く切れている（理想的）。" if w["holding_days"] > l["holding_days"]
                   else "負けを長く持ちすぎている可能性（損切りを早く）。")
            )

    if not out:
        out.append("勝ち・負けの間にはっきりした傾向差はまだ出ていません。取引数を増やすと見えてきます。")
    return out


def by_strategy(account_id: int) -> list[dict]:
    """戦略ごとの成績集計。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT strategy, COUNT(*) AS n, "
            "SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins, "
            "ROUND(SUM(pnl)) AS pnl, ROUND(AVG(return_pct),2) AS avg_ret "
            "FROM trades WHERE account_id = ? AND status='closed' "
            "GROUP BY strategy ORDER BY pnl DESC",
            (account_id,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["win_rate"] = round(d["wins"] / d["n"] * 100, 1) if d["n"] else 0
        result.append(d)
    return result


def best_worst(account_id: int, n: int = 5) -> dict:
    """最も勝った/負けた取引を返す。"""
    trades = _trades(account_id)
    for t in trades:
        t["name"] = stock_name(t["ticker"])
    ranked = sorted(trades, key=lambda t: t["return_pct"], reverse=True)
    return {"best": ranked[:n], "worst": ranked[-n:][::-1] if len(ranked) >= n else ranked[::-1]}


def _exit_category(reason: str | None) -> str:
    """出口理由テキストを種類に分類する（どの出口が効いたかの集計用）。"""
    r = reason or ""
    if "利確" in r:
        return "利確"
    if "トレーリング" in r:
        return "トレーリングストップ"
    if "ATR" in r:
        return "ATR損切り"
    if "損切り" in r:
        return "固定損切り"
    return "売りシグナル"


def by_exit_reason(account_id: int) -> list[dict]:
    """出口の種類ごとの成績（件数・勝率・損益）を集計する。"""
    trades = _trades(account_id)
    buckets: dict[str, list[dict]] = {}
    for t in trades:
        buckets.setdefault(_exit_category(t["exit_reason"]), []).append(t)

    result = []
    for cat, rows in buckets.items():
        wins = sum(1 for r in rows if r["outcome"] == "win")
        pnl = sum(r["pnl"] for r in rows)
        result.append({
            "category": cat,
            "n": len(rows),
            "win_rate": round(wins / len(rows) * 100, 1),
            "pnl": round(pnl),
            "avg_ret": round(mean(r["return_pct"] for r in rows), 2),
        })
    result.sort(key=lambda x: x["pnl"], reverse=True)
    return result
