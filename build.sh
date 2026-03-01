#!/bin/bash

# Exit on error
set -e

echo "--- Server Shuttle Cross-Distro Build Script ---"
echo "NOTE: For maximum compatibility (glibc), run this on your oldest target Linux distribution."

# Check for venv
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv || {
        echo "Error: Failed to create venv. You might need to install 'python3-venv'."
        echo "Try: sudo apt install python3-venv"
        exit 1
    }
fi

echo "Activating virtual environment..."
source venv/bin/activate

# Self-healing pip check
if ! python3 -m pip --version > /dev/null 2>&1; then
    echo "Warning: pip is missing or broken in venv. Attempting repair..."
    python3 -m ensurepip --default-pip || {
        echo "Error: Failed to repair pip automatically."
        echo "Try recreating the venv: rm -rf venv && python3 -m venv venv"
        exit 1
    }
fi

echo "Ensuring build dependencies are up to date..."
pip install --upgrade pip --quiet
pip install pyinstaller --quiet
pip install -r requirements.txt --quiet

echo "Cleaning previous build artifacts..."
rm -rf build/ dist/

echo "Starting PyInstaller build..."
pyinstaller --clean server-shuttle.spec

echo "Generating Launch-Shuttle.sh launcher..."
cat << 'EOF' > dist/Launch-Shuttle.sh
#!/bin/bash
# Convenient launcher for Server Shuttle
# Ensures it runs in a terminal if possible, but the binary also has internal auto-terminal logic.

BASEDIR=$(dirname "$0")
cd "$BASEDIR"

if [ -f "./server-shuttle" ]; then
    ./server-shuttle
else
    echo "Error: server-shuttle binary not found in $(pwd)"
    read -p "Press enter to exit..."
fi
EOF
chmod +x dist/Launch-Shuttle.sh

echo "Packaging release: server-shuttle-linux.zip..."
cp config.example.yaml dist/
cd dist
if command -v zip >/dev/null 2>&1; then
    zip -q server-shuttle-linux.zip server-shuttle Launch-Shuttle.sh config.example.yaml
    echo "Package created: dist/server-shuttle-linux.zip"
else
    echo "Warning: 'zip' command not found. Skipping zip creation."
    echo "Files available in dist/ directory."
fi
cd ..

echo "-----------------------------------"
echo "Build complete!"
echo "Standalone executable: dist/server-shuttle"
echo "Launcher script:       dist/Launch-Shuttle.sh"
echo "Release Package:       dist/server-shuttle-linux.zip"
echo "---"
