"""ライブ仮想売買エンジン（フェーズ4）。

選定済み戦略（live_strategies）について最新シグナルを判定し、実際の現金を
増減させながら建玉・手仕舞いを行う。建玉は trades テーブルに status='open' で
持ち越し、毎日の判定で状態を更新する。

フェーズ2の履歴一括シミュレータと違い、こちらは現金制約・建玉数上限のある
本物のポートフォリオ運用を、1日ずつ前進させる形で行う（forward testing）。

誠実な前提:
- 約定はその日の終値（引け後実行を想定。イントラデイ非対応）。
- 取引コストは片道0.1%。ロングのみ。実際の発注はしない（ペーパートレード）。
"""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from . import collector, context, db, exits, selection
from . import strategies as strat
from .exits import ExitConfig
from .stocks import BENCHMARK, stock_name

POSITION_SIZE_PCT = 0.10   # 1銘柄に振り向ける資金（初期資金比）
MAX_POSITIONS = 10         # 同時保有の上限
COST = 0.001               # 片道コスト
STOP_LOSS = -0.08          # 既定の損切りライン
EXIT_CONFIG_KEY = "live_exit_config"  # settings に保存する出口設定のキー


def get_exit_config() -> ExitConfig:
    """自動売買ボットの出口設定を settings から復元する（無ければ既定）。"""
    return ExitConfig.from_dict(
        db.get_setting(EXIT_CONFIG_KEY, {"stop_loss": STOP_LOSS})
    )


def _open_positions(conn, account_id: int) -> dict[str, dict]:
    """保有中（status='open'）の建玉を {ticker: row} で返す。"""
    rows = conn.execute(
        "SELECT * FROM trades WHERE account_id = ? AND status = 'open'",
        (account_id,),
    ).fetchall()
    return {r["ticker"]: dict(r) for r in rows}


def _latest_signal(df: pd.DataFrame, strategy: str, params: dict) -> int:
    """最新バーでの望ましいポジション（1=保有すべき / 0=手仕舞うべき）を返す。"""
    pos = strat.STRATEGIES[strategy]["func"](df, **params)
    if pos.empty:
        return 0
    return int(pos.iloc[-1])


def run_daily(account_name: str = "live", as_of: str | None = None) -> dict:
    """1営業日分の判定と売買を実行する。

    Args:
        account_name: 対象口座（既定 'live'）。
        as_of: この日付までのデータで判定する（YYYY-MM-DD）。
            None なら最新まで。連続営業日のテスト用に過去日付を渡せる。

    Returns:
        その日の売買アクションと口座状態のサマリー。
    """
    account = db.get_or_create_account(account_name)
    lives = selection.get_live_strategies()
    if not lives:
        return {"ok": False, "reason": "選定済み戦略がありません。先に銘柄選定を実行してください。"}

    tickers = [s["ticker"] for s in lives]

    # 増分更新（最新まで動かす本番運用時のみ。過去日付テスト時はスキップ）
    if as_of is None:
        collector.update_latest(tickers + [BENCHMARK])

    try:
        bench_full = collector.load_prices(BENCHMARK)
    except Exception:
        bench_full = None

    config = get_exit_config()
    position_budget = account["initial_cash"] * POSITION_SIZE_PCT
    now = datetime.now().isoformat(timespec="seconds")
    buys: list[dict] = []
    sells: list[dict] = []
    run_date = as_of or ""

    with db.connect() as conn:
        cash = conn.execute(
            "SELECT cash FROM accounts WHERE id = ?", (account["id"],)
        ).fetchone()["cash"]
        positions = _open_positions(conn, account["id"])

        for s in lives:
            ticker, strategy, params = s["ticker"], s["strategy"], s["params"]
            df = collector.load_prices(ticker)
            if as_of is not None:
                df = df.loc[:as_of]
            if len(df) < 2:
                continue

            bar_date = df.index[-1]
            run_date = max(run_date, bar_date.strftime("%Y-%m-%d"))
            price = float(df["Close"].iloc[-1])
            desired = _latest_signal(df, strategy, params)
            held = ticker in positions

            # --- 手仕舞い判定（売りシグナル or 出口ルール） ---
            if held:
                pos = positions[ticker]
                # 建玉後の高値（トレーリング用）と建玉時ATR（ATR損切り用）
                since = df["Close"].loc[pos["entry_date"]:]
                peak = float(since.max()) if len(since) else price
                atr_at_entry = None
                if config.uses_atr:
                    atr_s = exits.atr(df, config.atr_period)
                    at = atr_s.loc[:pos["entry_date"]]
                    atr_at_entry = float(at.iloc[-1]) if len(at) and pd.notna(at.iloc[-1]) else None
                rule_exit, rule_reason = exits.evaluate(
                    config, price, pos["entry_price"], peak, atr_at_entry
                )
                if desired == 0 or rule_exit:
                    proceeds = pos["shares"] * price * (1 - COST)
                    cost_basis = pos["shares"] * pos["entry_price"] * (1 + COST)
                    pnl = proceeds - cost_basis
                    ret = pnl / cost_basis * 100
                    holding_days = (bar_date - pd.Timestamp(pos["entry_date"])).days
                    outcome = "win" if pnl > 0 else ("loss" if pnl < 0 else "even")
                    reason = (
                        strat.exit_reason(strategy, params) if desired == 0
                        else rule_reason
                    )
                    conn.execute(
                        "UPDATE trades SET exit_date=?, exit_price=?, exit_reason=?, "
                        "pnl=?, return_pct=?, holding_days=?, status='closed', outcome=? "
                        "WHERE id=?",
                        (bar_date.strftime("%Y-%m-%d"), round(price, 2), reason,
                         round(pnl), round(ret, 2), holding_days, outcome, pos["id"]),
                    )
                    cash += proceeds
                    del positions[ticker]
                    sells.append({"name": stock_name(ticker), "price": round(price, 1),
                                  "pnl": round(pnl), "reason": reason})
                continue

            # --- 新規建玉判定（買いシグナル） ---
            if desired == 1 and len(positions) < MAX_POSITIONS:
                shares = int(position_budget / price) if price > 0 else 0
                buy_cost = shares * price * (1 + COST)
                if shares > 0 and cash >= buy_cost:
                    feats = context.entry_features(df, bar_date, bench_full)
                    conn.execute(
                        "INSERT INTO trades ("
                        "account_id, ticker, strategy, params, shares, "
                        "entry_date, entry_price, entry_reason, status, "
                        "market_regime, trend_strength, entry_rsi, volatility, created_at"
                        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (account["id"], ticker, strategy,
                         json.dumps(params, ensure_ascii=False), shares,
                         bar_date.strftime("%Y-%m-%d"), round(price, 2),
                         strat.entry_reason(strategy, params, feats), "open",
                         feats.get("market_regime"), feats.get("trend_strength"),
                         feats.get("entry_rsi"), feats.get("volatility"), now),
                    )
                    cash -= buy_cost
                    positions[ticker] = {"ticker": ticker, "shares": shares,
                                         "entry_price": price}
                    buys.append({"name": stock_name(ticker), "price": round(price, 1),
                                 "shares": shares, "regime": feats.get("market_regime")})

        # 建玉評価額と総資産を計算
        holdings_value = 0.0
        for t, p in positions.items():
            df = collector.load_prices(t)
            if as_of is not None:
                df = df.loc[:as_of]
            if not df.empty:
                holdings_value += p["shares"] * float(df["Close"].iloc[-1])
        total_value = cash + holdings_value

        conn.execute(
            "UPDATE accounts SET cash = ? WHERE id = ?", (cash, account["id"])
        )
        conn.execute(
            "INSERT INTO daily_runs "
            "(account_id, run_date, n_buys, n_sells, cash, holdings_value, total_value, note, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(account_id, run_date) DO UPDATE SET "
            "n_buys=excluded.n_buys, n_sells=excluded.n_sells, cash=excluded.cash, "
            "holdings_value=excluded.holdings_value, total_value=excluded.total_value",
            (account["id"], run_date, len(buys), len(sells),
             round(cash), round(holdings_value), round(total_value),
             f"建玉{len(positions)}件", now),
        )

    return {
        "ok": True, "run_date": run_date,
        "buys": buys, "sells": sells,
        "n_positions": len(positions),
        "cash": round(cash), "holdings_value": round(holdings_value),
        "total_value": round(total_value),
    }


def portfolio_state(account_name: str = "live") -> dict:
    """現在の口座状態（現金・建玉・総資産・資産推移）を返す。"""
    account = db.get_or_create_account(account_name)
    aid = account["id"]

    with db.connect() as conn:
        open_rows = conn.execute(
            "SELECT * FROM trades WHERE account_id=? AND status='open' ORDER BY entry_date",
            (aid,),
        ).fetchall()
        runs = conn.execute(
            "SELECT run_date, total_value, n_buys, n_sells, cash, holdings_value "
            "FROM daily_runs WHERE account_id=? ORDER BY run_date",
            (aid,),
        ).fetchall()
        cash = conn.execute(
            "SELECT cash FROM accounts WHERE id=?", (aid,)
        ).fetchone()["cash"]

    # 建玉に最新価格で含み損益を付ける
    positions = []
    holdings_value = 0.0
    for r in open_rows:
        d = dict(r)
        try:
            last = float(collector.load_prices(d["ticker"])["Close"].iloc[-1])
        except Exception:
            last = d["entry_price"]
        value = d["shares"] * last
        holdings_value += value
        d["name"] = stock_name(d["ticker"])
        d["last_price"] = round(last, 1)
        d["unrealized_pct"] = round((last / d["entry_price"] - 1) * 100, 2)
        d["unrealized_pnl"] = round(d["shares"] * (last - d["entry_price"]))
        positions.append(d)

    total = cash + holdings_value
    return {
        "initial_cash": account["initial_cash"],
        "cash": round(cash),
        "holdings_value": round(holdings_value),
        "total_value": round(total),
        "total_return_pct": round((total / account["initial_cash"] - 1) * 100, 2),
        "positions": sorted(positions, key=lambda p: p["unrealized_pct"], reverse=True),
        "equity_curve": [
            {"date": r["run_date"], "total": r["total_value"]} for r in runs
        ],
        "runs": [dict(r) for r in reversed(runs)][:30],
    }
