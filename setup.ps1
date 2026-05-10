# RunLights setup — creates a venv and installs all Python dependencies.
# Run from the project folder: .\setup.ps1

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $here

Write-Host "`nRunLights Setup" -ForegroundColor Cyan
Write-Host "===============`n"

# --- Python ---
try {
    $pyver = & python --version 2>&1
    Write-Host "Python: $pyver" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Python not found. Install Python 3.11+ and add it to PATH." -ForegroundColor Red
    exit 1
}

# --- Virtual environment ---
$venvDir = Join-Path $here ".venv"
if (-not (Test-Path $venvDir)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv $venvDir
    Write-Host "Created: $venvDir" -ForegroundColor Green
} else {
    Write-Host "Virtual environment exists: $venvDir" -ForegroundColor Green
}

$pip = Join-Path $venvDir "Scripts\pip.exe"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"

# --- Pip dependencies ---
Write-Host "`nInstalling Python dependencies..." -ForegroundColor Yellow
& $pip install --upgrade pip --quiet
& $pip install -r (Join-Path $here "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install failed." -ForegroundColor Red
    exit 1
}
Write-Host "Dependencies installed." -ForegroundColor Green

# --- pywin32 post-install (required on some systems) ---
$pywin32post = Join-Path $venvDir "Scripts\pywin32_postinstall.py"
if (Test-Path $pywin32post) {
    Write-Host "Running pywin32 post-install..." -ForegroundColor Yellow
    & $pythonExe $pywin32post -install 2>&1 | Out-Null
    Write-Host "pywin32 post-install done." -ForegroundColor Green
}

# --- Tesseract (required for OCR screen_region modes) ---
Write-Host ""
$tessPath = Get-Command tesseract -ErrorAction SilentlyContinue
if ($tessPath) {
    $tessVer = & tesseract --version 2>&1 | Select-Object -First 1
    Write-Host "Tesseract: $tessVer" -ForegroundColor Green
} else {
    Write-Host "WARNING: Tesseract not found in PATH." -ForegroundColor Yellow
    Write-Host "         OCR screen_region modes will not work." -ForegroundColor Yellow
    Write-Host "         Install from: https://github.com/UB-Mannheim/tesseract/wiki" -ForegroundColor Yellow
}

# --- Summary ---
Write-Host "`nSetup complete." -ForegroundColor Cyan
Write-Host "To launch RunLights, run: & '$pythonExe' '$here\runlights.pyw'"
Write-Host "Or double-click runlights.pyw after activating the venv.`n"
