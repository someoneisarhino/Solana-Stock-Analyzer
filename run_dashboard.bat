@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python virtual environment not found.
  echo Create it with: py -m venv .venv
  echo Then install Python packages with: .venv\Scripts\python -m pip install -r requirements.txt
  exit /b 1
)

if not exist "node_modules\.bin\vite.cmd" (
  echo Dashboard dependencies are not installed.
  echo Install them with: npm install
  exit /b 1
)

start "Solana Stock Analyzer API" cmd /k ""%CD%\.venv\Scripts\python.exe" -m uvicorn web_app:app --host 127.0.0.1 --port 8000"
call npm run dev
