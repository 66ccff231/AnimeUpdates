@echo off
REM ---------------------------------------------------------------
REM  Launcher for jp_anime_daily.py
REM  NOTE: This file is intentionally pure ASCII. cmd.exe parses
REM  batch files using the console's ANSI codepage (GBK on Chinese
REM  Windows), so non-ASCII text here can swallow line breaks and
REM  merge commands together. All Chinese output comes from Python.
REM ---------------------------------------------------------------
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM find a usable interpreter: prefer "python", fall back to the py launcher
set "PY=python"
where python >nul 2>nul || set "PY=py -3"

echo ==============================================================
echo   Japanese anime daily schedule  ^|  source: AniList
echo   Window: yesterday + next 6 days
echo ==============================================================
echo.

%PY% jp_anime_daily.py --before 1 --after 6
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [FAILED] exit code %RC%
  echo If the network is blocked, try:  %PY% jp_anime_daily.py --offline
) else (
  echo [OK] Web page: jp_anime_daily.html
)
echo.
pause
