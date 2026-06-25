@echo off
REM Job runner called by Task Scheduler. Usage: run_job.bat screen|accuracy
setlocal
set "JOB=%~1"
if "%JOB%"=="" set "JOB=screen"

REM cd to repo root (2 levels up from scripts\windows\)
cd /d "%~dp0..\.."
if not exist logs mkdir logs
set "LOG=logs\win_%JOB%.log"

REM Make Python emit UTF-8 so Japanese log text is not garbled (cp932) on Windows.
set "PYTHONUTF8=1"

REM uv may not be in PATH in scheduler environment; fall back to install location
set "UV=uv"
where uv >nul 2>nul || set "UV=%USERPROFILE%\.local\bin\uv.exe"

echo [%date% %time%] start %JOB% (uv=%UV%)>> "%LOG%"
"%UV%" run python -m app.daily %JOB% >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo [%date% %time%] FAILED %JOB% rc=%RC%>> "%LOG%"
  powershell -NoProfile -Command "(New-Object -ComObject Wscript.Shell).Popup('stock %JOB% failed rc=%RC%',10,'stock',48)" 1>nul 2>nul
) else (
  echo [%date% %time%] done %JOB%>> "%LOG%"
)

endlocal & exit /b %RC%
