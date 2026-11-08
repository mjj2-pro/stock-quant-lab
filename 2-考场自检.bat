@echo off
chcp 65001 >nul 2>&1
set "ROOT=E:\DSH-Work\stock"
set "PY=E:\Python314\python.exe"
set "PYTHONIOENCODING=utf-8"
cd /d "%ROOT%"
"%PY%" "%ROOT%\lab\bat_entry.py" selftest
echo.
pause
