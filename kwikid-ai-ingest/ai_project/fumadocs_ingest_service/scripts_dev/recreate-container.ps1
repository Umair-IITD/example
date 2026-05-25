param(
    [switch]$Logs
)

$ErrorActionPreference = "Stop"

Write-Host "Recreating fumadocs-api container with latest code..." -ForegroundColor Cyan

docker compose down
docker compose up -d --build --force-recreate

Write-Host "Container recreated successfully." -ForegroundColor Green
Write-Host "API health: http://localhost:8000/health" -ForegroundColor Yellow

if ($Logs) {
    docker compose logs -f fumadocs-api
}
