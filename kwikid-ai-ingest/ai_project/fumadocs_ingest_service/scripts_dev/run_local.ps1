# run_local.ps1
Write-Host "🚀 Starting KwikID AI Ingest Service locally..." -ForegroundColor Cyan

# Check if .env exists
if (-Not (Test-Path ".env")) {
    Write-Host "⚠️  .env file not found. Copying from .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
}

# Run uvicorn
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
