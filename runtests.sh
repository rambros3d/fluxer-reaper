#!/bin/bash
# Convenient script to run the Discord Reaper test suite.
# Automatically handles VENV activation and PYTHONPATH.

# Change to the project root directory (where the script is located)
cd "$(dirname "$0")"

# Activate the virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Set PYTHONPATH to the current directory so src is discoverable
export PYTHONPATH=.

# Run pytest with any arguments passed to this script, defaulting to tests/
if [ $# -eq 0 ]; then
    pytest -v -s -p no:warnings tests/
else
    pytest -s "$@"
fi
