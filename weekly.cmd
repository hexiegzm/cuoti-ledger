@echo off
rem Called by scheduled task "MathMistakeWeekly" every Sunday 20:10,
rem and again at every logon (to catch up when Sunday was missed).
rem `cuoti.py weekly` is idempotent: it only generates reports that are due.
rem Keep this file ASCII-only: cmd.exe parses lines before chcp takes effect.
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs

set "PY=D:\Python\python.exe"
if not exist "%PY%" set "PY=python"

echo. >> "logs\weekly.log"
echo ----- weekly run ----- >> "logs\weekly.log"

"%PY%" cuoti.py weekly --open >> "logs\weekly.log" 2>&1
if errorlevel 1 (
  echo attempt 1 failed, retrying >> "logs\weekly.log"
  "%PY%" cuoti.py weekly --open >> "logs\weekly.log" 2>&1
)

if errorlevel 1 (
  echo exit=1 FAILED >> "logs\weekly.log"
  exit /b 1
)
echo exit=0 >> "logs\weekly.log"
exit /b 0
