# cleanup_logs.ps1
Write-Host "🧹 Cleaning up logs and traces..." -ForegroundColor Cyan

if (Test-Path "logs/*.log") {
    Remove-Item "logs/*.log" -Force
    Write-Host "✅ Logs cleared." -ForegroundColor Green
}

if (Test-Path "traces/*.json") {
    Remove-Item "traces/*.json" -Force
    Write-Host "✅ Traces cleared." -ForegroundColor Green
}
