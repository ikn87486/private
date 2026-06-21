"""SQLite による永続化層。

価格データ・仮想売買口座・取引ジャーナルを1つの DB ファイルに蓄積する。
標準ライブラリの sqlite3 のみを使用（追加依存なし）。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "stock.db"

SCHEMA = """
-- 価格データ（日足）。取得するたびに蓄積していく。
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (ticker, date)
);

-- 仮想売買口座（ペーパートレード）。
CREATE TABLE IF NOT EXISTS accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT UNIQUE NOT NULL,
    initial_cash REAL NOT NULL,
    cash         REAL NOT NULL,
    created_at   TEXT NOT NULL
);

-- 取引ジャーナル。1行 = 1回の往復売買（建玉〜手仕舞い）。
-- 勝因/敗因分析のため、エントリー時の相場状況も一緒に記録する。
CREATE TABLE IF NOT EXISTS trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id     INTEGER NOT NULL,
    ticker         TEXT NOT NULL,
    strategy       TEXT NOT NULL,
    params         TEXT,
    shares         INTEGER NOT NULL,
    -- エントリー
    entry_date     TEXT NOT NULL,
    entry_price    REAL NOT NULL,
    entry_reason   TEXT,
    -- イグジット
    exit_date      TEXT,
    exit_price     REAL,
    exit_reason    TEXT,
    -- 損益
    pnl            REAL,
    return_pct     REAL,
    holding_days   INTEGER,
    status         TEXT NOT NULL DEFAULT 'open',   -- 'open' / 'closed'
    outcome        TEXT,                            -- 'win' / 'loss' / 'even'
    -- エントリー時の相場コンテキスト（勝因・敗因の手がかり）
    market_regime  TEXT,     -- '上昇相場' / '下落相場'
    trend_strength REAL,     -- 終値が75日線から何%離れているか
    entry_rsi      REAL,     -- エントリー時のRSI(14)
    volatility     REAL,     -- 直近20日の日次変動率の標準偏差(%)
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_account ON trades(account_id);
CREATE INDEX IF NOT EXISTS idx_trades_status  ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_ticker  ON trades(ticker);

-- ボットが取引する「選定済み戦略」。検証◎の銘柄・戦略・パラメータを保存する。
-- 銘柄選定を実行するたびに全行を洗い替える。
CREATE TABLE IF NOT EXISTS live_strategies (
    ticker        TEXT PRIMARY KEY,
    strategy      TEXT NOT NULL,
    params        TEXT NOT NULL,
    oos_compound  REAL,    -- 検証期間の複利リターン(%)
    verdict       TEXT,    -- 判定文言
    selected_at   TEXT NOT NULL
);

-- 日次の自動実行ログ。資産推移カーブの素データも兼ねる。
CREATE TABLE IF NOT EXISTS daily_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id     INTEGER NOT NULL,
    run_date       TEXT NOT NULL,
    n_buys         INTEGER NOT NULL DEFAULT 0,
    n_sells        INTEGER NOT NULL DEFAULT 0,
    cash           REAL,
    holdings_value REAL,
    total_value    REAL,
    note           TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE(account_id, run_date)
);

-- 汎用設定（key-value）。自動売買ボットの出口ルール設定などを JSON で保存する。
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 振り返りレポート。生成のたびに1行追加し、履歴として時系列で見返す。
CREATE TABLE IF NOT EXISTS reports (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id           INTEGER NOT NULL,
    created_at           TEXT NOT NULL,
    period_start         TEXT,
    period_end           TEXT,
    headline             TEXT,
    total_return_pct     REAL,
    benchmark_return_pct REAL,
    body_json            TEXT NOT NULL
);

-- 調査スクリーナー（/screen）の評価結果スナップショット。
-- 1行 = (基準日, 銘柄, 期間) の上昇可能性・期待幅・売買レベル。後日の精度検証にも使う。
CREATE TABLE IF NOT EXISTS screen_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    horizon       INTEGER NOT NULL,   -- 対象営業日数（3 / 5 / 10）
    score         REAL,               -- 総合スコア（0..100）
    prob_up       REAL,               -- 上昇確率(%)（較正テーブル由来）
    ret_p25       REAL,               -- 期待リターン分布の25%点(%)
    ret_p50       REAL,               -- 期待リターン中央値(%)
    ret_p75       REAL,               -- 期待リターン分布の75%点(%)
    entry         REAL,               -- 想定エントリー価格（最新終値）
    stop          REAL,               -- 想定損切り価格（ATRベース）
    target        REAL,               -- 想定利確価格（期待中央値）
    market        TEXT,               -- 'JP' / 'US'
    earnings_warn TEXT,               -- 'yes' / 'no' / 'unknown'
    reasons       TEXT,               -- 根拠テキスト（JSON配列）
    created_at    TEXT NOT NULL,
    UNIQUE(snapshot_date, ticker, horizon)
);

CREATE INDEX IF NOT EXISTS idx_screen_date ON screen_snapshots(snapshot_date);

-- スコアの較正テーブル。過去データで「スコア帯ごとに実際どれだけ上がったか」を
-- 期間別に集計し、上昇確率と期待リターン分布を保存する（スクリーナーの裏取り）。
CREATE TABLE IF NOT EXISTS screen_calibration (
    horizon      INTEGER NOT NULL,    -- 対象営業日数（3 / 5 / 10）
    score_bucket INTEGER NOT NULL,    -- スコア十分位（0=最下位 .. 9=最上位）
    bucket_low   REAL,                -- このバケットのスコア下限
    bucket_high  REAL,                -- このバケットのスコア上限
    prob_up      REAL,                -- 前向きリターン>0 の割合(%)
    ret_p25      REAL,
    ret_p50      REAL,
    ret_p75      REAL,
    n            INTEGER,             -- 標本数
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (horizon, score_bucket)
);

-- 予測の実績照合（記録）。screen_snapshots の各予測について、期限到来後に
-- 実際の後続価格から損益を確定したもの。結果論を避けるため建玉時に出口を固定する。
CREATE TABLE IF NOT EXISTS screen_outcomes (
    snapshot_date TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    horizon       INTEGER NOT NULL,
    entry_fill    REAL,     -- 翌営業日 Open（実際の建玉値）
    exit_date     TEXT,
    exit_price    REAL,
    exit_reason   TEXT,     -- 'target' / 'stop' / 'timeout'
    trade_return  REAL,     -- 出口ルール込みの損益(%)
    raw_return    REAL,     -- 期限終値÷entry−1(%)（確率較正の検証用）
    hit_up        INTEGER,  -- raw_return>0 なら1
    filled_at     TEXT NOT NULL,
    PRIMARY KEY (snapshot_date, ticker, horizon)
);

-- 予測確率の精度（信頼性）。予測確率帯ごとに実績の上昇割合を集計したもの。
-- method='walkforward'（過去のポイントインタイム検証）/ 'live'（実運用の記録）。
CREATE TABLE IF NOT EXISTS screen_accuracy (
    method         TEXT NOT NULL,
    horizon        INTEGER NOT NULL,
    prob_bucket    INTEGER NOT NULL,  -- 予測確率の10%刻みバケット（0..9）
    predicted_prob REAL,              -- その帯の予測確率の代表値(%)
    realized_prob  REAL,              -- 実績の上昇割合(%)
    avg_return     REAL,              -- 実績リターン平均(%)
    n              INTEGER,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (method, horizon, prob_bucket)
);

-- フォワード・シミュレーションの実行履歴（1実行=1行）。
CREATE TABLE IF NOT EXISTS sim_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    params_json      TEXT,
    start_date       TEXT, end_date TEXT, horizon INTEGER,
    n_trades         INTEGER,
    win_rate         REAL,
    total_return     REAL,
    benchmark_return REAL,
    max_dd           REAL,
    sharpe           REAL
);

-- シミュレーションのトレード明細。
CREATE TABLE IF NOT EXISTS sim_trades (
    run_id        INTEGER NOT NULL,
    ticker        TEXT, market TEXT,
    entry_date    TEXT, entry_price REAL,
    exit_date     TEXT, exit_price REAL,
    return_pct    REAL, exit_reason TEXT, score_at_entry REAL
);

-- シミュレーションの資産推移（エクイティ曲線）。
CREATE TABLE IF NOT EXISTS sim_equity (
    run_id    INTEGER NOT NULL,
    date      TEXT NOT NULL,
    equity    REAL,
    benchmark REAL
);

CREATE INDEX IF NOT EXISTS idx_sim_trades_run ON sim_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_sim_equity_run ON sim_equity(run_id);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """DB 接続を返すコンテキストマネージャ。行は dict 風にアクセスできる。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """スキーマを作成する（既にあれば何もしない）。"""
    with connect() as conn:
        conn.executescript(SCHEMA)


def get_or_create_account(
    name: str = "paper", initial_cash: float = 1_000_000.0
) -> sqlite3.Row:
    """仮想売買口座を取得、なければ作成する（初期資金は既定100万円）。"""
    from datetime import datetime

    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE name = ?", (name,)
        ).fetchone()
        if row:
            return row
        conn.execute(
            "INSERT INTO accounts (name, initial_cash, cash, created_at) "
            "VALUES (?, ?, ?, ?)",
            (name, initial_cash, initial_cash, datetime.now().isoformat(timespec="seconds")),
        )
        return conn.execute(
            "SELECT * FROM accounts WHERE name = ?", (name,)
        ).fetchone()


def reset_account(name: str = "paper") -> None:
    """口座を初期状態に戻し、その口座の取引ジャーナルを全削除する。"""
    with connect() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE name = ?", (name,)).fetchone()
        if not row:
            return
        conn.execute("DELETE FROM trades WHERE account_id = ?", (row["id"],))
        conn.execute(
            "UPDATE accounts SET cash = initial_cash WHERE id = ?", (row["id"],)
        )


def get_setting(key: str, default=None):
    """settings から値（JSON）を取り出す。無ければ default。"""
    import json

    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value) -> None:
    """settings に値（JSON）を保存する（upsert）。"""
    import json

    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
