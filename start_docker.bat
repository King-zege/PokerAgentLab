@echo off
setlocal

cd /d "%~dp0"

where docker >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Docker command not found. Please install and start Docker Desktop first.
  pause
  exit /b 1
)

echo [1/3] Checking Docker Desktop...
docker info >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Docker Desktop is not running or current user has no Docker permission.
  echo Open Docker Desktop, wait until the engine is running, then run this script again.
  pause
  exit /b 1
)

echo [2/3] Building and starting PokerAgentLab...
docker compose up --build -d
if errorlevel 1 (
  echo [ERROR] docker compose up failed.
  pause
  exit /b 1
)

echo [3/3] Current containers:
docker compose ps

echo.
echo PokerAgentLab is starting.
echo Frontend: http://127.0.0.1:5174/
echo API docs: http://127.0.0.1:8000/docs
echo Health:   http://127.0.0.1:5174/api/health
echo.

start "" "http://127.0.0.1:5174/"

pause
