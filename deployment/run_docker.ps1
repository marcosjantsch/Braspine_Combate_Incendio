$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\docker-compose.yml")) {
    Write-Error "Execute este script na raiz do projeto."
}

docker compose up --build
