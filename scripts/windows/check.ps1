# stock system check script
# Usage: powershell -ExecutionPolicy Bypass -File scripts\windows\check.ps1

$root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $root

$ok = 0; $ng = 0; $warn = 0

function Show-OK   { param($msg) Write-Host "[OK]   $msg" -ForegroundColor Green;  $script:ok++   }
function Show-NG   { param($msg) Write-Host "[NG]   $msg" -ForegroundColor Red;    $script:ng++   }
function Show-WARN { param($msg) Write-Host "[WARN] $msg" -ForegroundColor Yellow; $script:warn++ }

Write-Host ""
Write-Host "==== stock " -NoNewline -ForegroundColor Cyan
Write-Host "システム 動作診断" -NoNewline
Write-Host " ====" -ForegroundColor Cyan
Write-Host ""

# 1. uv が使えるか
$uvPath = "$env:USERPROFILE\.local\bin\uv.exe"
if (Test-Path $uvPath) {
    $ver = (& $uvPath --version 2>&1)
    Show-OK "uv OK ($ver)"
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    $ver = (uv --version 2>&1)
    Show-OK "uv OK ($ver)"
} else {
    Show-NG "uv が見つかりません: https://docs.astral.sh/uv/ からインストールしてください"
}

# 2. .venv が存在するか
if (Test-Path "$root\.venv\Scripts\python.exe") {
    Show-OK ".venv が存在する"
} else {
    Show-NG ".venv がない -- 'uv sync' を実行してください"
}

# 3. タスクスケジューラの3タスクが Ready か
$taskNames = @("stock_screen_morning", "stock_screen_evening", "stock_accuracy")
foreach ($name in $taskNames) {
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($null -eq $t) {
        Show-NG "タスク未登録: $name -- register_tasks.ps1 を実行してください"
    } elseif ($t.State -eq "Ready" -or $t.State -eq "Running") {
        Show-OK "タスク: $name ($($t.State))"
    } else {
        Show-NG "タスク状態が異常: $name ($($t.State))"
    }
}

# 4. DB ファイルが存在するか
$dbPath = "$root\data\stock.db"
if (Test-Path $dbPath) {
    $size = [math]::Round((Get-Item $dbPath).Length / 1KB, 1)
    Show-OK "DB ファイルあり (data\stock.db, ${size}KB)"
} else {
    Show-NG "DB ファイルなし -- 'uv run python -m app.daily screen' を一度実行してください"
}

# 5. Python モジュールが正常に import できるか
$uvExe = if (Test-Path $uvPath) { $uvPath } else { "uv" }
$res = (& $uvExe run python -c "from app import db; db.init_db(); print('ok')" 2>&1)
if ($LASTEXITCODE -eq 0 -and "$res" -match "ok") {
    Show-OK "Python モジュール import OK"
} else {
    Show-NG "Python import エラー: $res"
}

# 6. screen ログ (7日以内の更新があるか)
$screenLog = "$root\logs\win_screen.log"
if (Test-Path $screenLog) {
    $age = (Get-Date) - (Get-Item $screenLog).LastWriteTime
    $last = (Get-Content $screenLog -Tail 1)
    if ($age.TotalDays -le 7) {
        Show-OK "screen ログ: $([math]::Round($age.TotalHours,1))h前に更新 -- $last"
    } else {
        Show-NG "screen ログが $([math]::Round($age.TotalDays,0))日間更新なし（タスクが動いていない可能性）"
    }
} else {
    Show-WARN "screen ログ未作成（初回実行待ち -- タスク時刻になれば自動生成されます）"
}

# 7. accuracy ログ (8日以内の更新があるか)
$accLog = "$root\logs\win_accuracy.log"
if (Test-Path $accLog) {
    $age = (Get-Date) - (Get-Item $accLog).LastWriteTime
    $last = (Get-Content $accLog -Tail 1)
    if ($age.TotalDays -le 8) {
        Show-OK "accuracy ログ: $([math]::Round($age.TotalHours,1))h前に更新 -- $last"
    } else {
        Show-NG "accuracy ログが $([math]::Round($age.TotalDays,0))日間更新なし（毎週土曜実行）"
    }
} else {
    Show-WARN "accuracy ログ未作成（初回の土曜18:00まで待ちください）"
}

# サマリー
Write-Host ""
Write-Host "==== 結果: $ok OK / $ng NG / $warn WARN ====" -ForegroundColor Cyan
if ($ng -eq 0) {
    Write-Host "問題なし。タスクスケジューラが時刻になれば自動実行されます。" -ForegroundColor Green
} else {
    Write-Host "問題あり: $ng 件の NG を確認してください。" -ForegroundColor Red
}
Write-Host ""