#!/bin/bash
# Disco Reaper — unified launcher

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# ── Virtualenv setup ─────────────────────────────────────────────────────────
if [ ! -d "$DIR/venv" ]; then
    echo "Virtual environment not found. Creating..."
    python3 -m venv "$DIR/venv"
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create virtual environment."
        exit 1
    fi
    source "$DIR/venv/bin/activate"
    if [ -f "$DIR/requirements.txt" ]; then
        echo "Installing requirements..."
        pip install --upgrade pip -q
        pip install -r "$DIR/requirements.txt" -q
    fi
else
    source "$DIR/venv/bin/activate"
fi

# ── Launch ───────────────────────────────────────────────────────────────────
python3 "$DIR/disco-reaper.py" "$@"
