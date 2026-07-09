@echo off
echo AI Council Enterprise - install
if not exist .venv python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if not exist .env copy .env.example .env
echo Done. Edit .env and run run.bat
pause
