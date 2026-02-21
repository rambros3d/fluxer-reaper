@echo off
setlocal enabledelayedexpansion

echo --- Fluxer Reaper Windows Build Script ---

:: Check for venv
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo Error: Failed to create venv. Make sure Python is installed.
        exit /b 1
    )
)

echo Activating virtual environment...
call venv\Scripts\activate

echo Ensuring build dependencies are up to date...
python -m pip install --upgrade pip --quiet
pip install pyinstaller --quiet
pip install -r requirements.txt --quiet

echo Cleaning previous build artifacts...
if exist "build" rd /s /q build
if exist "dist" rd /s /q dist

echo Starting PyInstaller build...
pyinstaller --clean fluxer-reaper.spec

echo Generating Launch-Reaper.bat launcher...
(
echo @echo off
echo cd /d "%%~dp0"
echo if exist "fluxer-reaper.exe" ^(
echo     fluxer-reaper.exe
echo ^) else ^(
echo     echo Error: fluxer-reaper.exe not found!
echo     pause
echo ^)
) > dist\Launch-Reaper.bat

echo Packaging release: fluxer-reaper-windows.zip...
copy config.example.yaml dist\
powershell -Command "Compress-Archive -Path 'dist\fluxer-reaper.exe', 'dist\Launch-Reaper.bat', 'dist\config.example.yaml' -DestinationPath 'dist\fluxer-reaper-windows.zip' -Force"

echo -----------------------------------
echo Build complete!
echo Standalone executable: dist\fluxer-reaper.exe
echo Launcher script:       dist\Launch-Reaper.bat
echo Release Package:       dist\fluxer-reaper-windows.zip
echo ---
pause
