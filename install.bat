@echo off
title Install Push-to-Talk
cd /d "%~dp0"

echo ============================================
echo   Push-to-Talk Dictation - Installer
echo ============================================
echo.
echo This will install everything needed:
echo   - Python
echo   - dependencies (pyaudio, pynput)
echo   - whisper.cpp GPU build (NVIDIA / CUDA)
echo   - speech model (~1.5 GB)
echo.
echo NOTE: the NVIDIA GPU build is a big download (~650 MB).
echo It may take a while. Do not close this window.
echo.
pause

powershell -ExecutionPolicy Bypass -File "install_windows.ps1" -AutoStart

echo.
echo Starting the app now (hidden in the background)...

REM Start the app immediately, hidden (no console window).
REM Prefer pythonw.exe (no window); fall back to python.exe minimized.
start "" /min pythonw "%~dp0push_to_talk.py" 2>nul
if errorlevel 1 start "" /min python "%~dp0push_to_talk.py"

echo.
echo ============================================
echo   Done! The app is running.
echo ============================================
echo.
echo   Hold F5 = Russian, F6 = English. Release to paste.
echo.
echo   It will also start automatically every time
echo   you log in to Windows.
echo.
pause
