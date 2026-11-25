@echo off
rem Double-click launcher: opens the interactive mistake page in the background.
rem Uses pythonw.exe so no console window stays open.
rem Keep this file ASCII-only.
chcp 65001 >nul
cd /d "%~dp0"
set "PYW=D:\Python\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
if not exist logs mkdir logs
start "" "%PYW%" cuoti.py app --open
