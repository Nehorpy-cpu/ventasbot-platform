param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$BaseUrl = "http://127.0.0.1:$Port"
$Health = Invoke-RestMethod -Uri "$BaseUrl/salud" -TimeoutSec 5
if (-not $Health.database) { throw "El health check no pudo acceder a la base de datos." }
$Panel = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/panel/" -TimeoutSec 5
if ($Panel.StatusCode -ne 200 -or $Panel.Content -notmatch "VentasBot") {
    throw "El panel no respondió correctamente."
}
Write-Host "OK: API y panel local responden en $BaseUrl"
