param(
    [string]$DatabaseUrl = $env:YIELD_RCA_DATABASE_URL,
    [ValidateSet("golden_case", "multi_case", "spc_case")]
    [string]$Dataset = "golden_case",
    [switch]$SkipGenerate,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$FrontendDir = Join-Path $Root "frontend"
$OutputDir = Join-Path $Root "outputs\demo"
$SeedDir = Join-Path $Root "data\seeds\$Dataset"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Generator = switch ($Dataset) {
    "multi_case" { Join-Path $Root "scripts\generate_synthetic_multi_case_data.py" }
    "spc_case" { Join-Path $Root "scripts\generate_synthetic_spc_data.py" }
    default { Join-Path $Root "scripts\generate_synthetic_fab_data.py" }
}

if (-not $DatabaseUrl) {
    $DatabaseUrl = $env:TEST_DATABASE_URL
}
if (-not $DatabaseUrl) {
    throw "Database URL required. Set YIELD_RCA_DATABASE_URL or TEST_DATABASE_URL."
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project Python environment not found: $Python"
}

$PnpmCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
if (-not $PnpmCommand) {
    $PnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue
}
if (-not $PnpmCommand) {
    throw "pnpm is required to run the React dashboard."
}
$Pnpm = $PnpmCommand.Source

function Assert-PortFree {
    param([int]$Port)

    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($listener) {
        throw "Port $Port is already in use. Run scripts\stop_demo.ps1 or stop the existing service."
    }
}

function Wait-Http {
    param(
        [string]$Uri,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 300
        }
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for $Uri"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

if (-not $SkipGenerate) {
    Write-Host "[1/4] Generating offline $Dataset dataset"
    & $Python $Generator `
        --output-dir $SeedDir
    if ($LASTEXITCODE -ne 0) {
        throw "$Dataset dataset generation failed."
    }
} else {
    Write-Host "[1/4] Reusing existing $Dataset dataset"
}

Write-Host "[2/4] Seeding PostgreSQL"
& $Python (Join-Path $Root "scripts\seed_database.py") `
    --database-url $DatabaseUrl `
    --seed-dir $SeedDir `
    --reset-schema
if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL seed failed."
}

if (-not $SkipBuild) {
    Write-Host "[preflight] Building React dashboard"
    Push-Location $FrontendDir
    try {
        & $Pnpm run build
        if ($LASTEXITCODE -ne 0) {
            throw "React production build failed."
        }
    } finally {
        Pop-Location
    }
}

Assert-PortFree -Port 8000
Assert-PortFree -Port 5173

Write-Host "[3/4] Starting FastAPI with PostgreSQL data source"
$previousDatabaseUrl = $env:YIELD_RCA_DATABASE_URL
$env:YIELD_RCA_DATABASE_URL = $DatabaseUrl
try {
    $backend = Start-Process `
        -FilePath $Python `
        -ArgumentList @(
            "-m", "uvicorn", "yield_rca_api.app:app",
            "--app-dir", "backend",
            "--host", "127.0.0.1",
            "--port", "8000"
        ) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $OutputDir "backend.stdout.log") `
        -RedirectStandardError (Join-Path $OutputDir "backend.stderr.log") `
        -PassThru
} finally {
    $env:YIELD_RCA_DATABASE_URL = $previousDatabaseUrl
}
$backend.Id | Set-Content -Encoding ascii (Join-Path $OutputDir "backend.pid")

try {
    Wait-Http -Uri "http://127.0.0.1:8000/docs"

    Write-Host "[4/4] Starting React production preview"
    $frontend = Start-Process `
        -FilePath $Pnpm `
        -ArgumentList @("preview", "--host", "127.0.0.1", "--port", "5173") `
        -WorkingDirectory $FrontendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $OutputDir "frontend.stdout.log") `
        -RedirectStandardError (Join-Path $OutputDir "frontend.stderr.log") `
        -PassThru
    $frontend.Id | Set-Content -Encoding ascii (Join-Path $OutputDir "frontend.pid")
    Wait-Http -Uri "http://127.0.0.1:5173"
} catch {
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    throw
}

Write-Host ""
Write-Host "Yield RCA demo is ready."
Write-Host "Dataset:   $Dataset"
Write-Host "Dashboard: http://127.0.0.1:5173"
Write-Host "API docs:  http://127.0.0.1:8000/docs"
Write-Host "Query:     Analyze the 40N_SOC yield drop from 2026-07-01 to 2026-07-31."
if ($Dataset -eq "multi_case") {
    Write-Host "Cases:     docs\multi-case-validation.md"
} elseif ($Dataset -eq "spc_case") {
    Write-Host "SPC:       docs\advanced-spc.md"
}
Write-Host "Stop:      .\scripts\stop_demo.ps1"
