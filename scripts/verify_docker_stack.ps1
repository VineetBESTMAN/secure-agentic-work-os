param(
    [switch]$DownAfter
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Require-Command {
    param(
        [string]$Name,
        [string]$InstallHint
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found. $InstallHint"
    }
}

function Wait-Http {
    param(
        [string]$Url,
        [string]$Name,
        [int]$TimeoutSeconds = 120
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                Write-Host "$Name is reachable at $Url"
                return
            }
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }

    throw "$Name did not become reachable at $Url within $TimeoutSeconds seconds."
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $repoRoot
$composeStarted = $false

try {
    Require-Command "docker" "Install Docker Desktop, start it, then rerun this script."

    docker compose version | Out-Null

    Write-Host "Building and starting Docker Compose services..."
    docker compose up --build -d
    $composeStarted = $true

    Wait-Http -Url "http://127.0.0.1:8000/health" -Name "Backend health"

    $loginBody = @{
        email = "admin@demo.local"
        password = "demo-password"
    } | ConvertTo-Json

    $login = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:8000/api/auth/login" `
        -ContentType "application/json" `
        -Body $loginBody

    if (-not $login.access_token) {
        throw "Login smoke test did not return an access token."
    }

    $headers = @{ Authorization = "Bearer $($login.access_token)" }
    $importBody = @{
        provider = "google"
        items = @(
            @{
                filename = "docker-smoke-note.txt"
                content = "Docker smoke note: Acme renewal requires manager approval before any external contract summary is sent."
                mime_type = "text/plain"
                classification = "internal"
                owner_team = "platform"
            }
        )
    } | ConvertTo-Json -Depth 5

    $queuedImport = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:8000/api/connectors/import/async" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $importBody

    $importJob = $queuedImport.job
    $jobDeadline = (Get-Date).AddSeconds(120)
    while ($importJob.status -in @("queued", "running") -and (Get-Date) -lt $jobDeadline) {
        Start-Sleep -Seconds 1
        $importJob = Invoke-RestMethod `
            -Method Get `
            -Uri "http://127.0.0.1:8000/api/jobs/$($importJob.job_id)" `
            -Headers $headers
    }

    if ($importJob.status -ne "completed" -or $importJob.result.imported_documents -lt 1) {
        throw "Async connector import smoke test did not complete successfully: $($importJob.status)"
    }

    $queryBody = @{
        question = "What approval is required for the Acme renewal?"
    } | ConvertTo-Json

    $query = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:8000/api/documents/query" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $queryBody

    if ($query.citations.Count -lt 1) {
        throw "RAG smoke test did not return a citation."
    }

    Write-Host "Backend API, Postgres persistence, Redis worker import, and RAG smoke tests passed."
    Wait-Http -Url "http://127.0.0.1:5173" -Name "Frontend preview"
    Write-Host "Docker stack verification passed."
    Write-Host "Open http://127.0.0.1:5173 and sign in with admin@demo.local / demo-password."
}
finally {
    if (-not $composeStarted) {
        Write-Host "Docker Compose services were not started."
    }
    elseif ($DownAfter) {
        Write-Host "Stopping Docker Compose services because -DownAfter was supplied..."
        docker compose down
    }
    else {
        Write-Host "Services are still running. Use 'docker compose down' when you want to stop them."
    }

    Pop-Location
}
