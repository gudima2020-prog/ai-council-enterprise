@echo off
if not exist .venv (
  echo Virtual environment not found. Run install.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe main.py
pause
