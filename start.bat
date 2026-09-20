@echo off
rem quill launcher
rem
rem Double-click this file: installs dependencies (first run only), starts the
rem server, and opens the browser.
rem
rem Usage: start.bat [--port 9000]
rem
rem KEEP THIS FILE ASCII-ONLY WITH CRLF LINE ENDINGS. cmd.exe parses batch files
rem with the current code page and is picky about line endings: a UTF-8 / LF file
rem can make the window flash shut before you can read any error.
cd /d "%~dp0"

rem UTF-8 for uv/python messages. Harmless if it fails.
chcp 65001 >nul 2>nul

where uv >nul 2>nul
if errorlevel 1 goto no_uv

echo Starting quill ...
echo The first run downloads Python and dependencies; this can take a minute.
echo.

uv run quill serve --open-browser %*
set "code=%errorlevel%"

rem Always pause: when double-clicked, the window would otherwise close before
rem you can read whatever went wrong.
echo.
echo quill stopped (exit code %code%).
pause
exit /b %code%

:no_uv
echo.
echo [uv not found]
echo.
echo quill uses uv to install and run; uv also installs the required Python.
echo Install it from PowerShell:
echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 ^| iex"
echo.
echo Then close this window and double-click start.bat again.
echo.
pause
exit /b 1
