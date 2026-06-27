@echo off
title AI WarKing — Launcher
color 0A
cls

echo.
echo  ============================================================
echo   AI WarKing  ^|  Copilot Local Bridge
echo   Sve radi lokalno  ^|  127.0.0.1:5000
echo  ============================================================
echo.

:: ── Python provjera ──────────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    color 0C
    echo  [GRESKA] Python nije pronadjen u PATH-u!
    echo  Instaliraj Python 3.10+ sa python.org i dodaj u PATH.
    pause & exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PV=%%v
echo  [OK] Python %PV%

:: ── Flask provjera ────────────────────────────────────────────────────────────
pip show flask >nul 2>&1
if errorlevel 1 (
    echo  [INFO] Instaliram Flask...
    pip install flask --quiet
    echo  [OK] Flask instaliran.
) else (
    echo  [OK] Flask dostupan.
)

:: ── sessions folder ───────────────────────────────────────────────────────────
if not exist "sessions\" mkdir sessions

echo.
echo  [INFO] Pokrecem komponente...
echo.

:: ── 1. Overlay (transparentni kontroler) ──────────────────────────────────────
echo  [1/2] Overlay kontroler...
start "" /B pythonw overlay.py
timeout /t 1 /nobreak >nul

:: ── 2. Glavni server + GUI ────────────────────────────────────────────────────
echo  [2/2] AI WarKing server + GUI...
echo.
echo  ============================================================
echo   API endpoint:  http://127.0.0.1:5000/copilot_api
echo   Klijent:       python copilot_client.py
echo  ============================================================
echo.

python server.py

:: Crash handler
color 0C
echo.
echo  [GRESKA] server.py se ugasio ili crashnuo.
echo  Provjeri gresku iznad.
echo.
pause
