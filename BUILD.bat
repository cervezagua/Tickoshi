@echo off
title Tickoshi - Build Script
echo.
echo  ============================================
echo  Tickoshi Live Bitcoin Price Widget - Builder
echo  ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Install Python 3.10+ from python.org
    pause & exit /b 1
)

echo  Closing Tickoshi if running...
taskkill /f /im Tickoshi.exe >nul 2>&1
echo.

echo  [1/3] Checking dependencies...
pip show pyinstaller >nul 2>&1 || pip install pyinstaller
pip show pillow >nul 2>&1 || pip install pillow


echo  [2/3] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo  [3/3] Building EXE...
echo.

set ICON_ARG=
if exist Tickoshi.ico set ICON_ARG=--icon Tickoshi.ico

pyinstaller ^
  --onefile ^
  --windowed ^
  --name "Tickoshi" ^
  %ICON_ARG% ^
  --exclude-module numpy ^
  --exclude-module pandas ^
  --exclude-module matplotlib ^
  --exclude-module scipy ^
  --exclude-module IPython ^
  --exclude-module notebook ^
  --exclude-module docutils ^
  --exclude-module setuptools ^
  --exclude-module pkg_resources ^
  --exclude-module xml ^
  --exclude-module xmlrpc ^
  --exclude-module unittest ^
  --exclude-module http.server ^
  --exclude-module ftplib ^
  --exclude-module imaplib ^
  --exclude-module poplib ^
  --exclude-module smtplib ^
  --exclude-module telnetlib ^
  Tickoshi.py

echo.
if exist "dist\Tickoshi.exe" (
    echo  =========================================
    echo    SUCCESS!  dist\Tickoshi.exe is ready
    echo  =========================================
    echo.
    for %%I in ("dist\Tickoshi.exe") do echo    Size: %%~zI bytes
    echo.
    echo  Config saved to: %%APPDATA%%\Tickoshi\
    echo.
) else (
    echo  [ERROR] Build failed. Check output above.
)
pause
