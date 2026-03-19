@echo off
setlocal
cd /d "%~dp0"

echo Disco Reaper -- unified Windows launcher

IF NOT EXIST "venv" (
    echo Virtual environment not found. Creating...
    python -m venv venv
    IF ERRORLEVEL 1 (
        echo Error: Failed to create virtual environment. Ensure Python is installed and added to PATH.
        pause
        exit /b 1
    )
    call venv\Scripts\activate.bat
    IF EXIST "requirements.txt" (
        echo Installing requirements...
        python -m pip install --upgrade pip -q
        pip install -r requirements.txt -q
    )
) ELSE (
    call venv\Scripts\activate.bat
)

python disco-reaper.py %*
