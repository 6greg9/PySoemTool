@echo off
rem Runs the prerequisite check without changing the PowerShell execution policy.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_prerequisites.ps1" %*
set RC=%ERRORLEVEL%
echo.
if not defined NOPAUSE pause
exit /b %RC%
