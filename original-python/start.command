#!/bin/sh
cd "$(dirname "$0")" || exit 1
export PYTHONDONTWRITEBYTECODE=1
python3 app.py
