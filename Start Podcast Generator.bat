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

echo  Upgrading pip...
"%PY%" -m pip install --upgrade pip --quiet
if errorlevel 1 goto setupfail

rem llama-cpp-python must be pre-installed with a prebuilt binary wheel before
rem the main package install.  Without this step pip may try to compile it from
rem C++ source code, which requires Visual Studio Build Tools and CMake.
rem We try two routes: (1) the prebuilt-wheel index maintained by the library
rem author, (2) standard PyPI (covers platforms where a wheel is published there).
echo  Installing llama-cpp-python ^(prebuilt CPU wheel - avoids compiler requirement^)...
"%PY%" -m pip install "llama-cpp-python>=0.3.0" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu --quiet
if not errorlevel 1 goto llama_ok

echo  Prebuilt CPU wheel not available for this Python version.
echo  Trying standard PyPI ^(works if a binary wheel is published there^)...
"%PY%" -m pip install "llama-cpp-python>=0.3.0" --quiet
if not errorlevel 1 goto llama_ok

echo.
echo  llama-cpp-python could not be installed automatically.
echo.
echo  Common solutions:
echo    A^) Install Visual Studio Build Tools ^(free, ~3-4 GB^):
echo        https://aka.ms/vs/17/release/vs_BuildTools.exe
echo        Select the "Desktop development with C++" workload, then
echo        re-run this launcher.
echo.
echo    B^) NVIDIA GPU users: download a CUDA-enabled wheel from
echo        https://github.com/abetlen/llama-cpp-python/releases
echo        then install it manually:
echo          .venv\Scripts\pip install ^<downloaded-wheel^>.whl
echo        then re-run this launcher ^(setup will skip this step^).
echo.
echo    C^) For more detail run:  .venv\Scripts\capella-podcast doctor
echo.
goto setupfail

:llama_ok
echo  Installing remaining packages...
"%PY%" -m pip install -e . --quiet
if errorlevel 1 goto setupfail
echo.
echo  Setup complete.

rem ---- Check espeak-ng (needed only for podcast/MP3 generation) -----------
set "ESPEAK_FOUND=0"
where espeak-ng >nul 2>&1
if not errorlevel 1 set "ESPEAK_FOUND=1"
if "%ESPEAK_FOUND%"=="0" if exist "C:\Program Files\eSpeak NG\libespeak-ng.dll" set "ESPEAK_FOUND=1"
if "%ESPEAK_FOUND%"=="0" if exist "%~dp0.tools\espeak-ng\eSpeak NG\libespeak-ng.dll" set "ESPEAK_FOUND=1"

if "%ESPEAK_FOUND%"=="1" (
    echo  espeak-ng is installed - podcast ^(MP3^) generation is ready.
    echo.
    pause
    goto run
)

echo.
echo  espeak-ng is NOT installed. Summaries and scripts will work fine,
echo  but podcast ^(MP3^) generation needs it.
echo.

where winget >nul 2>&1
if errorlevel 1 goto espeak_manual

choice /C YN /M "  Install espeak-ng now via winget"
if errorlevel 2 goto espeak_skip

winget install --id eSpeak-NG.eSpeak-NG
if errorlevel 1 (
    echo  Auto-install failed. See manual instructions below.
    goto espeak_manual
)
echo  espeak-ng installed successfully.
echo.
pause
goto run

:espeak_skip
echo  Skipped. To install later:  winget install --id eSpeak-NG.eSpeak-NG
echo.
pause
goto run

:espeak_manual
echo  Install manually from https://github.com/espeak-ng/espeak-ng/releases
echo  No admin rights? Extract it locally instead:
echo    msiexec /a espeak-ng.msi /qn TARGETDIR="%~dp0.tools\espeak-ng"
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
