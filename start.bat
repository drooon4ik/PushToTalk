@echo off
title Push-to-Talk Dictation
cd /d "%~dp0"

echo ============================================
echo   Push-to-Talk is running!
echo ============================================
echo.
echo   Hold F5 - speak Russian  - release
echo   Hold F6 - speak English  - release
echo.
echo   Text will be pasted at the cursor.
echo   To quit - close this window.
echo ============================================
echo.

python push_to_talk.py

echo.
echo Stopped.
pause
