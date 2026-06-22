# 登録した stock の自動処理タスクを削除する。
# 実行: powershell -ExecutionPolicy Bypass -File scripts\windows\unregister_tasks.ps1
$ErrorActionPreference = "SilentlyContinue"

foreach ($name in 'stock_screen_morning','stock_screen_evening','stock_accuracy') {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "removed: $name"
}
Write-Host "完了。"
