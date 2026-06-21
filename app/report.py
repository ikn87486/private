"""振り返りレポート生成（フェーズ5）。

自動売買ボット（live口座）の実運用成績を、ベンチマーク（日経平均）と比較し、
確定取引の勝因敗因・保有中ポジション講評・改善提言にまとめる。
生成のたびに reports テーブルへ保存し、履歴として時系列で見返せる。

提言は「観察・提案」のみで、自動で銘柄再選定などは行わない（変更は人間が判断）。
これは投資助言ではない。
"""

from __future__ import annotations

import json
from datetime import datetime

from . import analysis, collector, db, live
from .stocks import BENCHMARK, BENCHMARK_NAME


def _benchmark_return(start: str | None, end: str | None) -> float | None:
    """運用期間 [start, end] における日経平均の単純リターン(%)。"""
    if not start or not end:
        return None
    try:
        bench = collector.load_prices(BENCHMARK)
    except Exception:
        return None
    sliced = bench.loc[start:end]
    if len(sliced) < 2:
        return None
    return round((sliced["Close"].iloc[-1] / sliced["Close"].iloc[0] - 1) * 100, 2)


def _max_drawdown(curve: list[dict]) -> float:
    """資産推移カーブ（{date, total} のリスト）から最大ドローダウン(%)を計算する。"""
    if len(curve) < 2:
        return 0.0
    peak = curve[0]["total"]
    max_dd = 0.0
    for p in curve:
        peak = max(peak, p["total"])
        if peak > 0:
            dd = (p["total"] / peak - 1) * 100
            max_dd = min(max_dd, dd)
    return round(abs(max_dd), 2)


def _headline(total_ret: float, bench_ret: float | None, n_runs: int) -> str:
    """一文の総括見出しを作る。"""
    if n_runs < 2:
        return "運用データがまだ十分に溜まっていません。手動実行か自動売買を数日回してください。"
    if bench_ret is None:
        return f"運用リターン {total_ret:+}%（ベンチマーク比較は期間不足）。"
    diff = total_ret - bench_ret
    if diff >= 0:
        return (
            f"運用リターン {total_ret:+}% が {BENCHMARK_NAME} {bench_ret:+}% を "
            f"{diff:+.1f}pt 上回り好調。"
        )
    if total_ret >= 0:
        return (
            f"運用リターン {total_ret:+}%（プラス）だが {BENCHMARK_NAME} {bench_ret:+}% に "
            f"{diff:.1f}pt 届かず。"
        )
    return (
        f"運用リターン {total_ret:+}% でベンチマーク（{BENCHMARK_NAME} {bench_ret:+}%）割れ。"
        "戦略の見直しを推奨。"
    )


def _recommendations(
    total_ret: float,
    bench_ret: float | None,
    summary: dict,
    attribution: dict,
    by_strategy: list[dict],
    max_dd: float,
    positions: list[dict],
    strategy_labels: dict,
) -> list[str]:
    """ルールベースの改善提言（観察・提案のみ）。"""
    recs: list[str] = []

    if bench_ret is not None and total_ret < bench_ret:
        recs.append(
            f"📉 ベンチマーク割れ（運用{total_ret:+}% < {BENCHMARK_NAME}{bench_ret:+}%）。"
            "「①銘柄選定を更新」で取引対象を見直すことを検討。"
        )

    # 戦略別の不調
    for s in by_strategy:
        if s["n"] >= 3 and s["pnl"] < 0:
            label = strategy_labels.get(s["strategy"], s["strategy"])
            recs.append(
                f"🔧 戦略「{label}」は確定損益マイナス（{s['n']}件・{s['pnl']:+,.0f}円・"
                f"勝率{s['win_rate']}%）。除外や対象銘柄の絞り込みを検討。"
            )

    # 敗因が下落相場に偏る
    if attribution:
        w = attribution["win"].get("uptrend_ratio")
        l = attribution["loss"].get("uptrend_ratio")
        if w is not None and l is not None and (w - l) >= 15:
            recs.append(
                f"🌧 下落相場でのエントリーが敗因の主軸（勝ち時の上昇相場率{w}% > "
                f"負け時{l}%）。地合いが悪い局面の新規建玉を控えると改善余地。"
            )

    # ドローダウン
    if max_dd >= 15:
        recs.append(
            f"⚠️ 最大ドローダウンが {max_dd}% と大きい。1銘柄あたりの建玉サイズ"
            f"（現状: 初期資金の{int(live.POSITION_SIZE_PCT * 100)}%）や"
            f"損切り幅（{int(live.STOP_LOSS * 100)}%）の見直しを検討。"
        )

    # 含み損が損切り間際のポジション
    near_stop = [p for p in positions if p["unrealized_pct"] <= live.STOP_LOSS * 100 + 2]
    if near_stop:
        names = "、".join(p["name"] for p in near_stop[:3])
        recs.append(
            f"🔻 損切りライン間際の建玉あり（{names}）。次回判定で手仕舞いの可能性。"
        )

    # サンプル不足
    if summary.get("n", 0) < 5:
        recs.append(
            "🌱 確定取引がまだ少なく評価は暫定的。運用を継続してサンプルを溜める段階。"
        )

    if not recs:
        recs.append("✅ 大きな問題は検出されませんでした。現状の運用方針を継続。")
    return recs


def generate_report(account_name: str = "live") -> dict:
    """振り返りレポートを生成し、DB に保存して返す。"""
    from . import strategies as strat

    account = db.get_or_create_account(account_name)
    aid = account["id"]
    state = live.portfolio_state(account_name)
    curve = state["equity_curve"]

    period_start = curve[0]["date"] if curve else None
    period_end = curve[-1]["date"] if curve else None
    total_ret = state["total_return_pct"]
    bench_ret = _benchmark_return(period_start, period_end)
    max_dd = _max_drawdown(curve)

    summary = analysis.summary(aid)
    attribution = analysis.attribution(aid)
    insights = analysis.insights(aid)
    by_strategy = analysis.by_strategy(aid)
    strategy_labels = {k: v["label"] for k, v in strat.STRATEGIES.items()}

    # 確定損益（実現）と含み損益（未実現）
    realized_pnl = summary.get("total_pnl", 0) if summary.get("n") else 0
    unrealized_pnl = sum(p["unrealized_pnl"] for p in state["positions"])

    headline = _headline(total_ret, bench_ret, len(curve))
    recommendations = _recommendations(
        total_ret, bench_ret, summary, attribution, by_strategy,
        max_dd, state["positions"], strategy_labels,
    )

    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "period_start": period_start,
        "period_end": period_end,
        "headline": headline,
        "performance": {
            "total_return_pct": total_ret,
            "benchmark_return_pct": bench_ret,
            "vs_benchmark_pt": (round(total_ret - bench_ret, 2)
                                if bench_ret is not None else None),
            "total_value": state["total_value"],
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "max_drawdown_pct": max_dd,
            "n_positions": len(state["positions"]),
        },
        "closed_summary": summary,
        "insights": insights,
        "by_strategy": [
            {**s, "label": strategy_labels.get(s["strategy"], s["strategy"])}
            for s in by_strategy
        ],
        "positions": state["positions"],
        "recommendations": recommendations,
    }

    with db.connect() as conn:
        conn.execute(
            "INSERT INTO reports "
            "(account_id, created_at, period_start, period_end, headline, "
            "total_return_pct, benchmark_return_pct, body_json) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (aid, report["created_at"], period_start, period_end, headline,
             total_ret, bench_ret, json.dumps(report, ensure_ascii=False)),
        )

    return report


def latest_report(account_name: str = "live") -> dict | None:
    """最新のレポートを返す（無ければ None）。"""
    account = db.get_or_create_account(account_name)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT body_json FROM reports WHERE account_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (account["id"],),
        ).fetchone()
    return json.loads(row["body_json"]) if row else None


def report_history(account_name: str = "live", limit: int = 20) -> list[dict]:
    """レポート履歴（見出し・対ベンチ）を新しい順に返す。"""
    account = db.get_or_create_account(account_name)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT created_at, period_start, period_end, headline, "
            "total_return_pct, benchmark_return_pct FROM reports "
            "WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
            (account["id"], limit),
        ).fetchall()
    return [dict(r) for r in rows]
