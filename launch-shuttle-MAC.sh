#!/bin/bash

# Get the directory where the script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Check if venv exists, create it if not
if [ ! -d "$DIR/venv" ]; then
    echo "Virtual environment not found. Creating one..."
    # On macOS, python3 is the standard command
    python3 -m venv "$DIR/venv"
    
    if [ $? -ne 0 ]; then
        echo "Error: Failed to create virtual environment."
        exit 1
    fi

    # Activate and install requirements
    source "$DIR/venv/bin/activate"
    
    if [ -f "$DIR/requirements.txt" ]; then
        echo "Installing requirements..."
        pip install --upgrade pip
        pip install -r "$DIR/requirements.txt"
    else
        echo "Warning: requirements.txt not found. Skipping dependency installation."
    fi
else
    # Activate existing virtual environment
    source "$DIR/venv/bin/activate"
fi

# Run the application
python3 server-shuttle.py "$@"
