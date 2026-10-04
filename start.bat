@echo off
chcp 65001 >nul
title fufuView Pro

rem Console window size (cols x lines). Lower them for a smaller window.
rem Without this Windows reuses the system console Defaults, which can be huge.
rem NOTE: keep this file ASCII-only. CJK text after "chcp 65001" breaks cmd parsing.
mode con: cols=100 lines=30 >nul 2>&1

rem Use "python -m pip": pip.exe may not be on PATH even when python is.
rem Stay quiet on success, but print the real output when it fails.
python -m pip install -r requirements.txt >nul 2>&1
if errorlevel 1 (
  echo.
  echo [!] Dependency install failed. Full output below:
  echo.
  python -m pip install -r requirements.txt
  echo.
)

python server.py
pause
