# Smart Query Router - Windows Backend Launcher
# Run from the project root: .\start.ps1
# Backend starts at http://127.0.0.1:8000

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $ProjectRoot "backend"

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  Smart Query Router - Backend" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  URL:    http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "  Docs:   http://127.0.0.1:8000/docs" -ForegroundColor Green
Write-Host "  Health: http://127.0.0.1:8000/health" -ForegroundColor Green
Write-Host ""
Write-Host "  Press Ctrl+C to stop." -ForegroundColor Yellow
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

Set-Location $BackendDir
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
