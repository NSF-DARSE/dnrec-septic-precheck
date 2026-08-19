@echo off
setlocal enabledelayedexpansion
title DNREC septic pre-check console

rem Double click this file to start the reviewer console.
rem It runs the app from the project virtual environment, binds it to every
rem network interface so another machine on the same network can reach it, and
rem prints the address to hand out before the browser opens.

cd /d "%~dp0"

set "PORT=8501"
set "PY=%~dp0.venv\Scripts\python.exe"

echo.
echo   DNREC septic pre-check
echo   ======================
echo.

if not exist "%PY%" (
    echo   The virtual environment is missing.
    echo   Expected: %PY%
    echo.
    echo   Create it once with:
    echo       python -m venv .venv
    echo       .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo       .venv\Scripts\python.exe -m pip install -e .
    echo.
    pause
    exit /b 1
)

rem Optional local settings, one KEY=VALUE per line, lines starting with # ignored.
rem .env.local is gitignored, so credentials for the reviewer chatbot go there
rem rather than into this file.
if exist "%~dp0.env.local" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%~dp0.env.local") do (
        if not "%%~a"=="" set "%%~a=%%~b"
    )
    echo   Loaded settings from .env.local
)

rem Is something already listening on the port? Reuse it rather than starting a
rem second server that fails or silently picks another port.
set "INUSE="
for /f "tokens=*" %%p in ('netstat -ano ^| findstr /r /c:"LISTENING" ^| findstr /c:":%PORT% "') do set "INUSE=1"

rem The address of this machine on the local network, from the interface that
rem holds the default route. See scripts/lan_address.py for why not ipconfig.
set "LANIP="
rem Relative paths on purpose: cmd strips the outer quotes off a backquoted
rem command, so two quoted paths on one line do not survive. The working
rem directory is this folder, set above, and neither path below has a space.
for /f "usebackq delims=" %%i in (`.venv\Scripts\python.exe scripts\lan_address.py`) do set "LANIP=%%i"

echo   On this machine:      http://localhost:%PORT%
if not "%LANIP%"=="" (
    echo   On this network:      http://%LANIP%:%PORT%
) else (
    echo   On this network:      address not found, use localhost
)
echo.
echo   Upload a packet, or use the three under out\examples.
echo   Close this window to stop the server.
echo.

if defined INUSE (
    echo   A server is already running on port %PORT%. Opening it instead.
    echo.
    start "" "http://localhost:%PORT%"
    pause
    exit /b 0
)

start "" "http://localhost:%PORT%"

"%PY%" -m streamlit run app.py ^
    --server.address=0.0.0.0 ^
    --server.port=%PORT% ^
    --server.headless=true ^
    --browser.gatherUsageStats=false

echo.
echo   The server stopped.
pause
