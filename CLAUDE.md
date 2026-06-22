# CLAUDE.md — 日米株スクリーナー／自動運用システム

このファイルは Claude Code がプロジェクト開始時に自動で読み込む。**2台（Mac・研究室Windows）で同じ
理解を共有するため、文脈はすべてリポジトリ内に置き git で同期する**のが本プロジェクトの方針。

## 応答ルール
- 日本語で応答する。

## 記憶（クロスマシン同期）— 最重要
- **`docs/worklog.md` をこのプロジェクトの“記憶”とする。** セッション開始時に必ず読むこと。
- **意味のある変更・判断・調査をしたら、その都度 `docs/worklog.md` の先頭付近に日付つきで追記する**
  （やったこと／決めたこと／次の一手）。可能なら同じコミットに含める。
- 機種ローカルの記憶（`~/.claude/...`）や会話ログには頼らない（OS/パス差で同期できないため）。
- 作業の流れ: 開始前に `git pull` → 作業 → `worklog.md` 追記 → `git commit && git push`。

@docs/worklog.md

## システム概要
- 数日〜2週間で上がりやすい日米株を調べる調査システム（FastAPI + htmx + Chart.js + yfinance + SQLite, uv管理）。
- 既存の日本株バックテスト/ペーパートレード/自動売買/週次レポートアプリに `/screen` を追加する形。

## 主要モジュール（`app/`）
- `stocks.py` … 日米統合ユニバース（`UNIVERSE` 約212）、`market_of`/`benchmark_for`/`HORIZONS=(3,5,10)`。
- `screener.py` … 因果的な総合スコア（`feature_frame`/`compute_scores`/`build_score_panel`）、`screen`/`save_snapshot`、ATRベース出口。
- `calibration.py` … 較正（`build_calibration`）＋精度検証（`walk_forward_accuracy`/`live_accuracy`）＋確率のOOS補正（PAV単調回帰、`lookup`が補正済み確率を返す）。
- `outcomes.py` … 予測の実績照合（`forward_exit`/`fill_outcomes`）。結果論を避ける前向き出口判定の唯一実装。
- `screen_sim.py` … フォワード・シミュレーション（決定論・翌バー約定・固定出口）。
- `daily.py` … CLI 実行口（`trade`/`report`/`screen`/`accuracy`）。サーバ非起動でも回せる。
- `main.py` … FastAPI ルーティング（`/`,`/screen`,`/paper`,`/validate`,`/live`,`/report`）。

## 設計の鉄則（結果論＝後出しを禁止）
- スコアは因果的（rolling/ewm のみ）。クロスセクション正規化は同一日内のみ。
- 約定は**翌営業日始値**。出口（損切り/利確/期限）は建玉時に固定し前向きに判定。
- 較正もポイントインタイム（フォールド開始前のデータだけで学習）。

## 運用・自動化
- 自動更新は **PC起動中**に走る（Webサーバ起動は不要）。
  - macOS: `scripts/install_launchd.sh`（解除 `uninstall_launchd.sh`）。
  - Windows: `scripts/windows/`（タスクスケジューラ。手順 `scripts/windows/README.md`）。
- **二重運用禁止**: 常時起動機を1台に決め、もう一方の自動実行は止める（`data/stock.db` 分裂回避）。
- スケジュール（JST）: 平日 07:30/16:45=screen、土 18:00=accuracy。

## リポジトリ運用の注意
- 専用リポジトリ: `/Users/yuki/Prog/stock`（remote=SSH `git@github.com:ikn87486/private.git`, private）。
- **ホーム `/Users/yuki` 自体も別の git リポジトリ**（無関係）。コミットは必ずこの専用リポジトリで。ホームから `git add -A` 禁止。
- `data/`・`.venv/`・`*.log` は .gitignore 済み（DB・秘匿物は push しない）。データDBは clone に含まれないので必要なら手動コピー。

## 正直な前提（過信しない）
- ユニバースは現在の主力銘柄＝**生存バイアス**あり。過去シミュは上振れする。本当の実力は「精度（記録）」タブの実運用トラックレコードで見る。
- 本システムはリサーチ/ペーパー用。実発注機能は無い。
- 短期予測は本質的に難しく、的中率は50%台に収れんしがち。確率はOOS補正済みだが保証ではない。
