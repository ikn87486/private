# Worklog（クロスマシン記憶）

このファイルがプロジェクトの「記憶」。**新しい記録ほど上**に、日付つきで追記する。
各エントリは「やったこと／決めたこと／次の一手」を簡潔に。詳細はコミット・コードを正とする。

---

## 2026-06-25(2) — データ鮮度チェックスクリプトを追加
- **やったこと**: `scripts/check_data.py`（Mac/Windows共通）を追加。直近の予測スナップショット・価格・
  較正・実績の鮮度を表示し、直近3営業日のスナップショット有無を判定する。
- **メモ**: `data/stock.db` は機種ローカル（git非同期）。Mac側は6/22で停止済み（自動収集はWindows機が正）。
  直近データの確認は**研究室PCで** `uv run python scripts/check_data.py` を実行して行う。
- **次の一手**: 研究室PCで `git pull` → `check_data.py` 実行し、6/23〜25が揃っているか確認。

## 2026-06-25 — 2台運用の同期と .claude 共有方針の確定
- **やったこと**:
  - 研究室Windows機側の変更を Mac に取り込み（`9b7da78`）: `/logs` ログ表示ページ、`scripts/windows/check.ps1`（診断）。
  - 研究室PC側が `.gitignore` に `.claude/` を追加していたのを、共有方針に合わせて
    **`.claude/settings.local.json` のみ無視**へ修正（`.claude/settings.json` は2台共有のため追跡継続）。
- **決めたこと**:
  - `.claude/settings.json`（Stopフック＋language）は**2台で共有**（A案）。個人用は `settings.local.json` に置く。
- **次の一手**:
  - 研究室PCで `git pull` して .gitignore 修正を反映。`/logs` は nav 未登録なので必要なら追加検討。
  - 別マシンのセッションでも作業後は worklog 追記を徹底（今回 lab PC 側は worklog 未更新だった）。

## 2026-06-22 — Windows移行キット＋クロスマシン記憶の整備
- **やったこと**:
  - 研究室Windows機向けの運用キットを追加（`scripts/windows/`: `run_job.bat`, `register_tasks.ps1`,
    `unregister_tasks.ps1`, `start_server.bat`, `README.md`）。launchd のタスクスケジューラ版。
  - Mac 側の launchd を解除し、開発サーバも停止（二重運用回避）。
  - クロスマシンで文脈を共有するため `CLAUDE.md` と本 `docs/worklog.md` を新設。
- **決めたこと**:
  - 常時起動機は**研究室Windows機を正**とする。Mac の自動実行は止めたまま。
  - Claude の記憶は `~/.claude` や会話ログに頼らず、**リポジトリ内 `CLAUDE.md` / `docs/worklog.md` を git で同期**。
  - 変更・判断のたびに worklog を更新する運用（フックで補強）。
- **次の一手**:
  - 研究室PCで `git pull` → `scripts/windows/README.md` 手順で稼働確認（タスク手動実行＋ログ確認）。
  - 改善ロードマップ（フェーズA: 生存バイアスの正直化・リスク管理強化・運用堅牢化／フェーズB: 改善ループ自動化）。
  - 数週間後、「精度（記録）」タブの live 実績を確認（現状 live_rows=0、これから蓄積）。

## 〜2026-06-21 — スクリーナー本体（フェーズ1〜3）の構築（要約）
- **フェーズ1**: `/screen` 上昇候補スクリーナー。日米212銘柄、総合スコア、較正で上昇確率・期待幅。
- **フェーズ2**: 記録（`outcomes.py`）＋フォワード・シミュレーション（`screen_sim.py`）＋精度検証
  （ウォークフォワード／実運用）。結果論を排除（翌バー約定・固定出口・決定論）。
- **フェーズ3**: 確率のOOS補正（PAV）、CLI自動化（`daily.py` screen/accuracy）、launchd、本番データ初期化、
  GitHub private へ push（remote=SSH）。
- 既知の正直な注意: 生存バイアス／ペーパー専用／短期予測の難しさ。詳細は `CLAUDE.md` 参照。
