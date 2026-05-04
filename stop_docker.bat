@echo off
setlocal

cd /d "%~dp0"

where docker >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Docker command not found.
  pause
  exit /b 1
)

echo Stopping PokerAgentLab containers...
docker compose down
if errorlevel 1 (
  echo [ERROR] docker compose down failed.
  pause
  exit /b 1
)

echo PokerAgentLab containers stopped.
pause
