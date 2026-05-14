# debug_trace.ps1
Write-Host "🔍 Starting service with DEBUG_TRACE enabled..." -ForegroundColor Cyan

$env:DEBUG_TRACE = "true"
./scripts_dev/run_local.ps1
