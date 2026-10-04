@echo off
chcp 65001 >nul
title fufuView Pro
pip install -r requirements.txt >nul 2>&1
python server.py
pause
