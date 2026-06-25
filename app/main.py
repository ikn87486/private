"""FastAPI + htmx アプリ本体。

画面から銘柄・戦略・期間を選び、バックテストまたはパラメータ探索を実行する。
htmx により、結果の表だけを部分的に差し替える。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import (
    analysis, calibration, collector, data, db, exits, live, outcomes, report,
    scheduler, screen_sim, screener, selection, simulator, strategies, validation,
)
from .exits import ExitConfig


def _pct_or_none(value: str) -> float | None:
    """フォームの%入力（空＝無効）を小数に変換する。例 "20" -> 0.20。"""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value) / 100
    except ValueError:
        return None


def _build_exit_config(
    stop_loss: str, take_profit: str, trailing: str, atr_mult: str
) -> ExitConfig:
    """フォーム入力から ExitConfig を組み立てる（損切りは符号を負に正規化）。"""
    sl = _pct_or_none(stop_loss)
    if sl is not None:
        sl = -abs(sl)  # 損切りは負の値で扱う
    am = (atr_mult or "").strip()
    try:
        atr_value = float(am) if am else None
    except ValueError:
        atr_value = None
    return ExitConfig(
        stop_loss=sl,
        take_profit=_pct_or_none(take_profit),
        trailing_stop=_pct_or_none(trailing),
        atr_mult=atr_value,
    )
from .backtest import optimize, run_backtest
from .stocks import HORIZONS, MARKET_BENCHMARK, STOCKS, UNIVERSE, stock_name

# スクリーナーの期間ラベル（営業日数 → 表示名）。
HORIZON_LABELS = {3: "3日", 5: "1週間", 10: "2週間"}

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """起動時に DB を用意し、自動売買スケジューラを起動する。"""
    db.init_db()
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="日本株バックテスト", lifespan=lifespan)

# 期間の選択肢
PERIODS = {
    "1y": "1年",
    "2y": "2年",
    "5y": "5年",
    "10y": "10年",
    "max": "全期間",
}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """トップ画面。フォームを表示する。"""
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "stocks": STOCKS,
            "strategies": strategies.STRATEGIES,
            "periods": PERIODS,
        },
    )


@app.post("/backtest", response_class=HTMLResponse)
def backtest_view(
    request: Request,
    ticker: str = Form(...),
    strategy: str = Form(...),
    period: str = Form("5y"),
    mode: str = Form("single"),
):
    """バックテストまたはパラメータ探索を実行し、結果の部分HTMLを返す。

    mode="single": 既定パラメータで1回だけ実行（詳細＋グラフ）。
    mode="optimize": 全パラメータ組み合わせを探索し、ランキングを表示。
    """
    try:
        df = data.fetch_history(ticker, period=period)
    except ValueError as e:
        return templates.TemplateResponse(
            request, "error.html", {"message": str(e)}
        )

    spec = strategies.STRATEGIES[strategy]

    if mode == "optimize":
        results = optimize(df, ticker, strategy)
        return templates.TemplateResponse(
            request,
            "optimize.html",
            {
                "results": results[:20],  # 上位20件
                "total": len(results),
                "ticker": ticker,
                "ticker_name": stock_name(ticker),
                "strategy_label": spec["label"],
                "period_label": PERIODS.get(period, period),
                "param_specs": spec["params"],
            },
        )

    # single モード: 既定パラメータで実行
    default_params = {name: meta[1] for name, meta in spec["params"].items()}
    result = run_backtest(df, ticker, strategy, default_params)
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "r": result,
            "ticker_name": stock_name(ticker),
            "strategy_label": spec["label"],
            "period_label": PERIODS.get(period, period),
            "param_specs": spec["params"],
        },
    )


# ----------------------------------------------------------------------------
# ペーパートレード（仮想売買 + 取引ジャーナル）
# ----------------------------------------------------------------------------


def _journal_context(request: Request) -> dict:
    """ジャーナル画面に渡すデータをまとめる。"""
    account = db.get_or_create_account("paper")
    aid = account["id"]
    return {
        "request": request,
        "account": dict(account),
        "summary": analysis.summary(aid),
        "insights": analysis.insights(aid),
        "attribution": analysis.attribution(aid),
        "by_strategy": analysis.by_strategy(aid),
        "by_exit": analysis.by_exit_reason(aid),
        "best_worst": analysis.best_worst(aid),
        "coverage": collector.coverage(),
        "stock_name": stock_name,
        "strategy_labels": {k: v["label"] for k, v in strategies.STRATEGIES.items()},
    }


@app.get("/paper", response_class=HTMLResponse)
def paper(request: Request):
    """ペーパートレードのダッシュボード。"""
    ctx = _journal_context(request)
    ctx["strategies"] = strategies.STRATEGIES
    return templates.TemplateResponse(request, "paper.html", ctx)


@app.post("/paper/collect", response_class=HTMLResponse)
def paper_collect(request: Request):
    """全ユニバースの過去データを収集して DB に蓄積する。"""
    collector.collect_all(period="max")
    return templates.TemplateResponse(request, "journal.html", _journal_context(request))


@app.post("/paper/simulate", response_class=HTMLResponse)
def paper_simulate(
    request: Request,
    strategy: str = Form(...),
    stop_loss: str = Form("8"),
    take_profit: str = Form(""),
    trailing: str = Form(""),
    atr_mult: str = Form(""),
):
    """全銘柄に戦略を適用して仮想売買し、取引ジャーナルに記録する。

    出口ルール（損切り/利確/トレール/ATR）はフォーム入力から組み立て、
    その実行だけに適用する（永続化しない実験用）。
    """
    spec = strategies.STRATEGIES[strategy]
    params = {name: meta[1] for name, meta in spec["params"].items()}
    config = _build_exit_config(stop_loss, take_profit, trailing, atr_mult)
    for ticker in STOCKS:
        simulator.simulate(ticker, strategy, params, exit_config=config)
    return templates.TemplateResponse(request, "journal.html", _journal_context(request))


@app.post("/paper/reset", response_class=HTMLResponse)
def paper_reset(request: Request):
    """口座と取引ジャーナルを初期化する。"""
    db.reset_account("paper")
    return templates.TemplateResponse(request, "journal.html", _journal_context(request))


# ----------------------------------------------------------------------------
# ウォークフォワード検証（過剰最適化の検出・戦略の選別）
# ----------------------------------------------------------------------------

# 学習/検証期間の選択肢
WF_SPLITS = {
    "3-1": ("学習3年 / 検証1年", 3, 1),
    "5-1": ("学習5年 / 検証1年", 5, 1),
    "5-2": ("学習5年 / 検証2年", 5, 2),
    "2-1": ("学習2年 / 検証1年", 2, 1),
}


@app.get("/validate", response_class=HTMLResponse)
def validate(request: Request):
    """検証ページのフォームを表示する。"""
    return templates.TemplateResponse(
        request,
        "validate.html",
        {
            "request": request,
            "strategies": strategies.STRATEGIES,
            "stocks": STOCKS,
            "splits": WF_SPLITS,
        },
    )


@app.post("/validate/run", response_class=HTMLResponse)
def validate_run(
    request: Request,
    strategy: str = Form(...),
    split: str = Form("3-1"),
    scope: str = Form("universe"),
    ticker: str = Form(""),
):
    """ウォークフォワード検証を実行する（単一銘柄 or ユニバース全体）。"""
    _, train_y, test_y = WF_SPLITS.get(split, WF_SPLITS["3-1"])
    spec = strategies.STRATEGIES[strategy]

    if scope == "single" and ticker:
        result = validation.walk_forward(ticker, strategy, train_y, test_y)
        return templates.TemplateResponse(
            request,
            "validate_single.html",
            {
                "request": request,
                "r": result,
                "ticker_name": stock_name(ticker),
                "strategy_label": spec["label"],
            },
        )

    scan = validation.scan_universe(list(STOCKS.keys()), strategy, train_y, test_y)
    return templates.TemplateResponse(
        request,
        "validate_scan.html",
        {
            "request": request,
            "scan": scan,
            "strategy_label": spec["label"],
            "stock_name": stock_name,
        },
    )


# ----------------------------------------------------------------------------
# 自動売買（ライブ仮想売買ボット）
# ----------------------------------------------------------------------------


def _live_context(request: Request) -> dict:
    """ライブ画面に渡すデータをまとめる。"""
    return {
        "request": request,
        "state": live.portfolio_state("live"),
        "lives": selection.get_live_strategies(),
        "strategy_labels": {k: v["label"] for k, v in strategies.STRATEGIES.items()},
        "next_run": scheduler.next_run_text(),
        "exit_config": live.get_exit_config(),
        "last_action": None,
    }


@app.get("/live", response_class=HTMLResponse)
def live_dashboard(request: Request):
    """自動売買のダッシュボード。"""
    return templates.TemplateResponse(request, "live.html", _live_context(request))


@app.post("/live/select", response_class=HTMLResponse)
def live_select(request: Request):
    """検証で銘柄選定を実行し、ボットが取引する戦略を更新する。"""
    selection.select_strategies(train_years=3, test_years=1)
    return templates.TemplateResponse(request, "live_panel.html", _live_context(request))


@app.post("/live/run", response_class=HTMLResponse)
def live_run(request: Request):
    """その日の判定・売買を手動で1回実行する。"""
    result = live.run_daily("live")
    ctx = _live_context(request)
    ctx["last_action"] = result
    return templates.TemplateResponse(request, "live_panel.html", ctx)


@app.post("/live/exits", response_class=HTMLResponse)
def live_exits(
    request: Request,
    stop_loss: str = Form("8"),
    take_profit: str = Form(""),
    trailing: str = Form(""),
    atr_mult: str = Form(""),
):
    """自動売買ボットの出口ルール設定を保存する（永続）。"""
    config = _build_exit_config(stop_loss, take_profit, trailing, atr_mult)
    db.set_setting(live.EXIT_CONFIG_KEY, config.to_dict())
    return templates.TemplateResponse(request, "live_panel.html", _live_context(request))


# ----------------------------------------------------------------------------
# 振り返りレポート
# ----------------------------------------------------------------------------


def _report_context(request: Request) -> dict:
    """レポート画面に渡すデータをまとめる。"""
    return {
        "request": request,
        "report": report.latest_report("live"),
        "history": report.report_history("live"),
        "next_report": scheduler.next_report_text(),
    }


@app.get("/report", response_class=HTMLResponse)
def report_dashboard(request: Request):
    """振り返りレポートのダッシュボード。"""
    return templates.TemplateResponse(request, "report.html", _report_context(request))


@app.post("/report/generate", response_class=HTMLResponse)
def report_generate(request: Request):
    """振り返りレポートを今すぐ生成する。"""
    report.generate_report("live")
    return templates.TemplateResponse(request, "report_panel.html", _report_context(request))


# ----------------------------------------------------------------------------
# 調査スクリーナー（数日〜2週間の上昇候補を期間別に一覧表示）
# ----------------------------------------------------------------------------


def _screen_context(request: Request) -> dict:
    """スクリーナー画面に渡すデータをまとめる。"""
    as_of, results = screener.latest_snapshot()
    ready = calibration.is_ready()
    return {
        "request": request,
        "as_of": as_of,
        "results": results,
        "horizons": list(HORIZONS),
        "horizon_labels": HORIZON_LABELS,
        "calibrated": ready,
        "calibrated_at": calibration.updated_at(),
        "hit_rate": calibration.hit_rate_summary() if ready else [],
        "n_universe": len(UNIVERSE),
        # 精度（記録）
        "acc_wf": calibration.accuracy_table("walkforward"),
        "acc_live": calibration.accuracy_table("live"),
        "acc_wf_at": calibration.accuracy_updated_at("walkforward"),
        "acc_live_at": calibration.accuracy_updated_at("live"),
        "outcomes_summary": outcomes.outcomes_summary(),
        # シミュレーション
        "sim": screen_sim.latest_run(),
        "sim_years": SIM_YEARS,
    }


# シミュレーションの対象期間（年）の選択肢。
SIM_YEARS = {1: "1年", 2: "2年", 3: "3年"}


@app.get("/screen", response_class=HTMLResponse)
def screen_dashboard(request: Request):
    """調査スクリーナーのダッシュボード。"""
    return templates.TemplateResponse(request, "screen.html", _screen_context(request))


@app.post("/screen/refresh", response_class=HTMLResponse)
def screen_refresh(request: Request):
    """最新データを取得し、全銘柄を再評価してスナップショットを保存する。"""
    tickers = list(UNIVERSE.keys()) + list(MARKET_BENCHMARK.values())
    existing = {c["ticker"] for c in collector.coverage()}
    new = [t for t in tickers if t not in existing]
    if new:
        collector.collect(new, period="max")  # 新規銘柄は全期間を取得
    collector.update_latest([t for t in tickers if t in existing])  # 既存は増分更新

    results = screener.screen()
    screener.save_snapshot(results)
    return templates.TemplateResponse(request, "screen_panel.html", _screen_context(request))


@app.post("/screen/calibrate", response_class=HTMLResponse)
def screen_calibrate(request: Request):
    """過去データでスコアを較正し直す（上昇確率・期待幅の裏取り）。"""
    calibration.build_calibration()
    return templates.TemplateResponse(request, "screen_panel.html", _screen_context(request))


@app.post("/screen/outcomes", response_class=HTMLResponse)
def screen_outcomes(request: Request):
    """期限到来済みの予測に実績を記入し、実運用の精度を更新する。"""
    outcomes.fill_outcomes()
    calibration.live_accuracy()
    return templates.TemplateResponse(request, "screen_accuracy.html", _screen_context(request))


@app.post("/screen/accuracy", response_class=HTMLResponse)
def screen_accuracy(request: Request):
    """較正を作り直し、ウォークフォワード／実運用の精度を再計算する。"""
    calibration.build_calibration()
    calibration.walk_forward_accuracy()
    calibration.live_accuracy()
    return templates.TemplateResponse(request, "screen_accuracy.html", _screen_context(request))


@app.get("/logs", response_class=HTMLResponse)
def logs_view(request: Request):
    """ジョブ実行ログ（win_screen.log / win_accuracy.log）を表示する。"""
    from datetime import datetime as _dt

    log_dir = BASE_DIR.parent / "logs"

    def tail(name: str, n: int = 40) -> list[str]:
        p = log_dir / name
        if not p.exists():
            return ["(まだログがありません)"]
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:] or ["(空のログファイル)"]

    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            "screen_lines": tail("win_screen.log"),
            "accuracy_lines": tail("win_accuracy.log"),
            "generated_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


@app.post("/screen/sim/run", response_class=HTMLResponse)
def screen_sim_run(
    request: Request,
    years: int = Form(2),
    horizon: int = Form(5),
    top_n: int = Form(5),
    rebalance: str = Form("weekly"),
):
    """フォワード・シミュレーションを実行する。"""
    from datetime import date

    end = date.today().isoformat()
    start = (date.today().replace(year=date.today().year - int(years))).isoformat()
    result = screen_sim.run_simulation(
        start=start, end=end, horizon=int(horizon), top_n=int(top_n), rebalance=rebalance
    )
    ctx = _screen_context(request)
    ctx["sim"] = result if result.get("ok") else ctx["sim"]
    ctx["sim_error"] = None if result.get("ok") else result.get("reason")
    return templates.TemplateResponse(request, "screen_sim.html", ctx)
