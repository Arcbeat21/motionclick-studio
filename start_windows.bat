@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher not found. Install Python from https://www.python.org/downloads/windows/
    pause
    exit /b 1
)
echo Installing or checking requirements...
py -m pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency installation failed. Check the error above.
    pause
    exit /b 1
)
echo Starting MotionClick Studio...
py app.py
if errorlevel 1 (
    echo Application exited with an error.
    pause
)
endlocal
