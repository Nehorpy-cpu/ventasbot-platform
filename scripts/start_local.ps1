param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [switch]$SeedDemo
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot ".venv-clean\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "No existe .venv-clean. Crealo e instalá requirements-dev.txt antes de iniciar."
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot ".env") -PathType Leaf)) {
    throw "Falta .env. Copiá .env.example y definí la configuración local."
}

Push-Location $ProjectRoot
try {
    & $Python -m alembic upgrade head
    if ($SeedDemo) {
        $env:SEED_DEMO = "1"
        & $Python -m app.seed
        if ($LASTEXITCODE -ne 0) { throw "No se pudo preparar la demo." }
    }
    Write-Host "VentasBot local: http://127.0.0.1:$Port/panel/"
    Write-Host "Salud: http://127.0.0.1:$Port/salud"
    & $Python -m uvicorn app.main:app --reload --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}
