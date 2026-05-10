@echo off
setlocal
cd /d "%~dp0"

echo.
echo RunLights Setup
echo ===============
echo.

:: --- Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.11+ and add it to PATH.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo Python: %%v

:: --- Virtual environment ---
if not exist ".venv\" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 ( echo ERROR: Failed to create venv. & pause & exit /b 1 )
    echo Created: %~dp0.venv
) else (
    echo Virtual environment exists: %~dp0.venv
)

:: --- Dependencies ---
echo.
echo Installing Python dependencies...
.venv\Scripts\pip install --upgrade pip --quiet
.venv\Scripts\pip install -r requirements.txt
if errorlevel 1 ( echo ERROR: pip install failed. & pause & exit /b 1 )
echo Dependencies installed.

:: --- pywin32 post-install ---
if exist ".venv\Scripts\pywin32_postinstall.py" (
    echo Running pywin32 post-install...
    .venv\Scripts\python .venv\Scripts\pywin32_postinstall.py -install
    echo pywin32 post-install done.
)

:: --- Tesseract ---
echo.
tesseract --version >nul 2>&1
if errorlevel 1 (
    echo WARNING: Tesseract not found in PATH.
    echo          OCR screen_region modes will not work.
    echo          Install from: https://github.com/UB-Mannheim/tesseract/wiki
) else (
    for /f "tokens=*" %%v in ('tesseract --version 2^>^&1') do ( echo Tesseract: %%v & goto :tessok )
    :tessok
)

:: --- Done ---
echo.
echo Setup complete.
echo Launch RunLights by double-clicking runlights.pyw
echo.
pause
