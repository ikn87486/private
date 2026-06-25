@echo off
REM タスクスケジューラから呼ばれるジョブ実行ラッパ（launchd の run_job.sh 相当）。
REM 使い方: run_job.bat screen | accuracy
setlocal
set "JOB=%~1"
if "%JOB%"=="" set "JOB=screen"

REM このバッチは scripts\windows\ にある。2つ上＝リポジトリ直下へ移動。
cd /d "%~dp0..\.."
if not exist logs mkdir logs
set "LOG=logs\win_%JOB%.log"

REM タスクスケジューラは最小環境で動くため uv が PATH に無いことがある。
REM PATH に無ければ既定のインストール先（%USERPROFILE%\.local\bin\uv.exe）を使う。
set "UV=uv"
where uv >nul 2>nul || set "UV=%USERPROFILE%\.local\bin\uv.exe"

echo [%date% %time%] start %JOB% (uv=%UV%)>> "%LOG%"
"%UV%" run python -m app.daily %JOB% >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo [%date% %time%] FAILED %JOB% rc=%RC%>> "%LOG%"
  REM 失敗を best-effort で通知（10秒で自動的に閉じるダイアログ。失敗しても無視）。
  powershell -NoProfile -Command "(New-Object -ComObject Wscript.Shell).Popup('stock %JOB% ジョブが失敗しました (rc=%RC%)',10,'stock 自動処理',48)" 1>nul 2>nul
) else (
  echo [%date% %time%] done %JOB%>> "%LOG%"
)

endlocal & exit /b %RC%
