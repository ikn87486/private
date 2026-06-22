# stock の自動処理を Windows タスクスケジューラに登録する（launchd の代替）。
#   平日 07:30 / 16:45 -> screen（予測保存＋実績記入）
#   土   18:00        -> accuracy（較正＋精度＋確率補正の再計算）
# 実行: PowerShell で  powershell -ExecutionPolicy Bypass -File scripts\windows\register_tasks.ps1
$ErrorActionPreference = "Stop"

$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$bat  = Join-Path $root "scripts\windows\run_job.bat"
if (-not (Test-Path $bat)) { throw "run_job.bat が見つかりません: $bat" }

# スリープ復帰時にも実行・取りこぼし時に後追い実行・電池でも止めない。
$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

function Register-StockTask {
    param([string]$Name, [string]$Arg, $Trigger)
    $action = New-ScheduledTaskAction -Execute $bat -Argument $Arg -WorkingDirectory $root
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger `
        -Settings $settings -Description "stock $Arg job" -Force | Out-Null
    Write-Host "registered: $Name"
}

$weekdays = @('Monday','Tuesday','Wednesday','Thursday','Friday')
Register-StockTask "stock_screen_morning" "screen"   (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 7:30am)
Register-StockTask "stock_screen_evening" "screen"   (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 4:45pm)
Register-StockTask "stock_accuracy"       "accuracy" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 6:00pm)

Write-Host "`n--- 登録済みタスク ---"
Get-ScheduledTask | Where-Object { $_.TaskName -like 'stock_*' } | Select-Object TaskName, State | Format-Table -AutoSize
Write-Host "手動テスト:  Start-ScheduledTask -TaskName stock_screen_evening"
Write-Host "ログ:        logs\win_screen.log / logs\win_accuracy.log"
