@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m app.main
) else (
  python -m app.main
)
if errorlevel 1 pause
