#!/bin/bash

# Get the directory where the script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Check if venv exists
if [ -d "$DIR/venv" ]; then
    # Activate virtual environment
    source "$DIR/venv/bin/activate"
    # Run the application
    python3 fluxer-reaper.py "$@"
else
    echo "Error: Virtual environment not found in $DIR/venv"
    echo "Please ensure you have installed the requirements into a 'venv' folder."
    exit 1
fi
