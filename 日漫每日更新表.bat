@echo off
REM ---------------------------------------------------------------
REM  Desktop app launcher for jp_anime_daily_gui.py
REM
REM  Pure ASCII on purpose: cmd.exe parses batch files using the
REM  console ANSI codepage (GBK on Chinese Windows). Non-ASCII text
REM  here can swallow line breaks and merge commands together, which
REM  looks like "python is broken" but is really a parsing failure.
REM  All Chinese output comes from Python instead.
REM ---------------------------------------------------------------
cd /d "%~dp0"

REM prefer pythonw.exe so no black console window stays open
set "PYW=pythonw"
where pythonw >nul 2>nul || set "PYW=python"

start "" %PYW% "%~dp0jp_anime_daily_gui.py"
