# Windows 常駐運用ガイド

常時起動の Windows PC でこのシステムを動かし、自動更新（スクリーニング・較正）を回すための手順。
macOS の launchd の代わりに **Windows タスクスケジューラ**を使う。アプリ本体（Python/uv）はそのまま動く。

> 重要:
> - アプリには**認証がありません**。**公開インターネットへポート開放しないでください**（Tailscale 等で安全に）。
> - Mac と Windows の**二重運用は避ける**（実績DBが分裂します）。Windows を正にしたら Mac 側の launchd を停止
>   （Mac で `bash scripts/uninstall_launchd.sh`）。
> - PC のタイムゾーンは **JST** に（スケジュール時刻が JST 前提）。
> - 研究室PCの**利用規程**（常駐サーバ・リモートアクセスの可否）を必ず確認。

---

## 1. 準備（インストール）
1. **Git for Windows**: <https://git-scm.com/download/win>
2. **uv**（Python パッケージ管理）: PowerShell で
   ```powershell
   winget install --id=astral-sh.uv -e
   ```
   または <https://docs.astral.sh/uv/> の手順。インストール後 `uv --version` が通ること。

## 2. コードを取得
```powershell
git clone git@github.com:ikn87486/private.git stock
cd stock
uv sync
```
- SSH 鍵が無ければ HTTPS+PAT でも可。
- 動作確認: `uv run python -c "import app.main"` がエラーなく通る。

## 3. データの引き継ぎ（任意）
- `data\stock.db` は **clone に含まれません**（.gitignore）。
- これまでの蓄積を引き継ぐ: Mac の `data/stock.db` を USB/共有でこの PC の `data\` にコピー。
- 新規で始める: 何もしなくてよい（手順5で再生成される）。

## 4. 自動更新を登録（タスクスケジューラ）
管理者 PowerShell で:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1
```
登録されるタスク（JST）:
| タスク | 時刻 | 内容 |
|---|---|---|
| stock_screen_morning | 平日 07:30 | 最新データ取得→予測保存→実績記入（前日の米国終値反映） |
| stock_screen_evening | 平日 16:45 | 同上（当日の日本株終値反映） |
| stock_accuracy | 土 18:00 | 較正＋精度（ウォークフォワード/実運用）＋確率補正の再計算 |

- 手動テスト: `Start-ScheduledTask -TaskName stock_screen_evening` → `logs\win_screen.log` に出力。
- 解除: `powershell -ExecutionPolicy Bypass -File scripts\windows\unregister_tasks.ps1`
- 既定は「ユーザーログオン時に実行」。**自動ログイン**を設定しておくと再起動後も確実。
  ログオフ状態でも動かしたい場合はタスクのプロパティで「ユーザーがログオンしているかどうかにかかわらず実行」に変更（資格情報の入力が必要）。

## 5. 初回データ生成（一度だけ）
```powershell
uv run python -m app.daily accuracy
uv run python -m app.daily screen
```
全212銘柄の取得＋較正で数分かかる。

## 6. Web 画面（UI）を常駐させる
見るときだけでよいが、常駐させる場合は次のいずれか。

**簡単: スタートアップに登録**
- `scripts\windows\start_server.bat` のショートカットを `shell:startup`（ファイル名を指定して実行で `shell:startup`）に置く。

**堅牢: サービス化（NSSM・任意）**
1. NSSM を入手 <https://nssm.cc/>。
2. 管理者 PowerShell:
   ```powershell
   nssm install stock-web "C:\path\to\stock\scripts\windows\start_server.bat"
   nssm set stock-web AppDirectory "C:\path\to\stock"
   nssm start stock-web
   ```
   クラッシュ時の自動再起動・ログオフでも稼働。

起動後、`http://<このPCのIP>:8000/screen` で閲覧。

## 7. 常時起動の設定
- 電源オプション: **スリープ＝なし**（ディスプレイのみオフは可）。
- BIOS/UEFI: 停電復帰後に自動起動（"Restore on AC Power Loss" 等）。
- Windows Update の自動再起動後に備え、手順6のスタートアップ/サービス化を推奨。

## 8. リモートアクセス（推奨構成）
- **Tailscale**（推奨）: この PC と手元の Mac/スマホに入れるだけ。ポート開放なしで
  `http://<tailscaleのIP>:8000` に安全アクセス。画面操作が要るときは RDP/Chrome リモートをこの上で。
- RDP は Windows **Pro** が必要。手軽さ優先なら Chrome リモートデスクトップ/AnyDesk。
- いずれも**公開インターネットに直接さらさない**こと（無認証アプリのため）。

## トラブルシュート
- `uv` が見つからない: PowerShell を開き直す（PATH 反映）。タスクが失敗する場合は `run_job.bat` の
  `uv` をフルパス（例 `%USERPROFILE%\.local\bin\uv.exe`）に変更。
- タスクが動かない: タスクスケジューラ GUI で履歴を確認、`logs\win_*.log` を確認。
- データが古い: ネットワーク/プロキシで yfinance 取得失敗の可能性。`uv run python -m app.daily screen` を手動実行してログ確認。
