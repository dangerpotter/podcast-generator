@echo off
setlocal
title Capella Course Podcast Generator
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"

if exist "%PY%" goto run

echo.
echo  First run: the Python environment ^(.venv\^) does not exist here yet.
echo  Setup creates it and installs the app. One time only; needs internet
echo  and several minutes ^(a few GB of downloads^).
echo.
choice /C YN /M "Set it up now"
if errorlevel 2 exit /b 1

rem Find a suitable Python (3.10+; 3.12 preferred).
set "BOOTPY="
py -3.12 -c "" >nul 2>&1 && set "BOOTPY=py -3.12"
if not defined BOOTPY py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set "BOOTPY=py -3"
if not defined BOOTPY python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set "BOOTPY=python"
if not defined BOOTPY (
    echo.
    echo  Python 3.10+ was not found on this machine.
    echo  Install it from https://www.python.org/downloads/ first
    echo  ^(keep the "py launcher" option checked^), then run this again.
    echo.
    pause
    exit /b 1
)

echo.
echo  Creating .venv using "%BOOTPY%" ...
%BOOTPY% -m venv .venv
if errorlevel 1 goto setupfail
echo  Installing the app and its dependencies - this is the slow part...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto setupfail
"%PY%" -m pip install -e .
if errorlevel 1 goto setupfail
echo.
echo  Setup complete.
echo.
echo  NOTE: podcast audio needs espeak-ng. If you don't have it yet, run:
echo      winget install --id eSpeak-NG.eSpeak-NG
echo  Summaries and scripts work fine without it.
echo.
pause

:run
echo Starting the Capella Course Podcast Generator...
echo A browser tab will open at http://127.0.0.1:8765/ ^(close this window to stop^).
echo.
"%PY%" -m capella_podcast.gui %*
if errorlevel 1 (
    echo.
    echo  The app exited with an error ^(see messages above^).
    pause
)
exit /b 0

:setupfail
echo.
echo  Setup failed ^(see messages above^). Common causes: no internet
echo  connection, or antivirus blocking the install. Fix and run this
echo  launcher again - it will offer setup again.
pause
exit /b 1
