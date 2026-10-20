@echo off
cd /d "%~dp0"
if not exist .venv (
    echo Preparando o programa pela primeira vez...
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements.txt
)
start "" http://localhost:5000
.venv\Scripts\python app.py
pause
