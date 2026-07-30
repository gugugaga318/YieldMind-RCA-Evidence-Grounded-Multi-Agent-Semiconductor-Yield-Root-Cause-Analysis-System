$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$OutputDir = Join-Path $Root "outputs\demo"

foreach ($name in @("frontend", "backend")) {
    $pidPath = Join-Path $OutputDir "$name.pid"
    if (-not (Test-Path -LiteralPath $pidPath)) {
        Write-Host "${name}: no recorded process"
        continue
    }

    $processId = [int](Get-Content -Raw $pidPath).Trim()
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $processId -Force
        Write-Host "${name}: stopped process $processId"
    } else {
        Write-Host "${name}: process $processId is not running"
    }
    Remove-Item -LiteralPath $pidPath -Force
}
