#!/usr/bin/env sh
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "Preparando o programa pela primeira vez..."
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python app.py
