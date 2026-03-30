@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo --- Disco-Reaper Windows Build Script ---

REM Check for venv
IF NOT EXIST "venv" (
    echo Creating virtual environment...
    python -m venv venv
    IF ERRORLEVEL 1 (
        echo Error: Failed to create virtual environment.
        echo Ensure Python is installed and added to PATH.
        IF NOT DEFINED AUTO_BUILD pause
        exit /b 1
    )
)

echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Self-healing pip check
python -m pip --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo Warning: pip is missing or broken in venv. Attempting repair...
    python -m ensurepip --default-pip
    IF ERRORLEVEL 1 (
        echo Error: Failed to repair pip automatically.
        echo Try recreating the venv: rmdir /s /q venv ^&^& python -m venv venv
        IF NOT DEFINED AUTO_BUILD pause
        exit /b 1
    )
)

echo Ensuring build dependencies are up to date...
REM python -m pip install --upgrade pip --quiet
python -m pip install pyinstaller --quiet
python -m pip install -r requirements.txt --quiet

echo Cleaning previous build artifacts...
IF EXIST "build" rmdir /s /q build
IF EXIST "dist" rmdir /s /q dist

echo Starting PyInstaller build...
REM Get git version tag
set "GIT_VERSION=Unknown"
for /f "tokens=*" %%i in ('git describe --tags --abbrev^=0 2^>nul') do set "GIT_VERSION=%%i"
echo Baking version: %GIT_VERSION%
echo __version__ = "%GIT_VERSION%"> src\core\_baked_version.py

python -m PyInstaller --clean disco-reaper.spec
IF ERRORLEVEL 1 (
    echo Error: PyInstaller build failed.
    del /f src\core\_baked_version.py 2>nul
    IF NOT DEFINED AUTO_BUILD pause
    exit /b 1
)

echo Cleaning up baked version file...
del /f src\core\_baked_version.py 2>nul

echo Packaging release: disco-reaper-windows.zip...
cd dist
powershell -Command "Compress-Archive -Path 'DiscoReaper.exe' -DestinationPath 'disco-reaper-windows.zip' -Force" 2>nul
IF ERRORLEVEL 1 (
    echo Warning: Failed to create zip. Files are available in dist\ directory.
) ELSE (
    echo Package created: dist\disco-reaper-windows.zip
)
cd ..

echo -----------------------------------
echo Build complete!
echo Standalone executable: dist\DiscoReaper.exe
echo Release Package:       dist\disco-reaper-windows.zip
echo ---
IF NOT DEFINED AUTO_BUILD pause
