@echo off
SET "DIR=%~dp0"
cd /d "%DIR%"

:: Check if venv exists, create it if not
if not exist "%DIR%venv" (
    echo Virtual environment not found. Creating one...
    python -m venv venv
    
    if %ERRORLEVEL% neq 0 (
        echo Error: Failed to create virtual environment.
        pause
        exit /b 1
    )

    :: Activate and install requirements
    call venv\Scripts\activate
    
    if exist "requirements.txt" (
        echo Installing requirements...
        python -m pip install --upgrade pip
        pip install -r requirements.txt
    ) else (
        echo Warning: requirements.txt not found. Skipping dependency installation.
    )
) else (
    :: Activate existing virtual environment
    call venv\Scripts\activate
)

:: Run the application
python server-shuttle.py %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo application exited with error code %ERRORLEVEL%
    pause
)
