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

    $migrationRevision = docker compose exec -T backend python -m alembic current --check-heads
    if ($LASTEXITCODE -ne 0) {
        throw "Backend database is not at the latest Alembic revision."
    }
    Write-Host "Database migration head is active: $migrationRevision"

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

    $organizations = Invoke-RestMethod `
        -Method Get `
        -Uri "http://127.0.0.1:8000/api/organizations" `
        -Headers $headers
    $verificationOrganization = $organizations | `
        Where-Object { $_.slug -eq "docker-verification" } | `
        Select-Object -First 1
    if (-not $verificationOrganization) {
        $organizationBody = @{
            name = "Docker Verification"
            slug = "docker-verification"
        } | ConvertTo-Json
        $verificationOrganization = Invoke-RestMethod `
            -Method Post `
            -Uri "http://127.0.0.1:8000/api/organizations" `
            -Headers $headers `
            -ContentType "application/json" `
            -Body $organizationBody
    }
    $switchBody = @{
        organization_id = $verificationOrganization.organization_id
    } | ConvertTo-Json
    $tenantSession = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:8000/api/auth/switch-organization" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $switchBody
    $tenantHeaders = @{ Authorization = "Bearer $($tenantSession.access_token)" }
    $tenantDocuments = Invoke-RestMethod `
        -Method Get `
        -Uri "http://127.0.0.1:8000/api/documents/library" `
        -Headers $tenantHeaders
    $smokeDocumentId = $importJob.result.document_ids[0]
    if ($tenantDocuments.document_id -contains $smokeDocumentId) {
        throw "A default-organization document crossed the Docker tenant boundary."
    }
    $tenantPolicies = Invoke-RestMethod `
        -Method Get `
        -Uri "http://127.0.0.1:8000/api/policies" `
        -Headers $tenantHeaders
    if ($tenantPolicies.Count -lt 3) {
        throw "The Docker verification organization was not seeded with governance policies."
    }
    $refreshBody = @{
        refresh_token = $tenantSession.refresh_token
    } | ConvertTo-Json
    $rotatedSession = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:8000/api/auth/refresh" `
        -ContentType "application/json" `
        -Body $refreshBody
    if (-not $rotatedSession.access_token) {
        throw "Refresh-token rotation did not return a new access token."
    }
    try {
        Invoke-RestMethod `
            -Method Post `
            -Uri "http://127.0.0.1:8000/api/auth/refresh" `
            -ContentType "application/json" `
            -Body $refreshBody | Out-Null
        throw "A used refresh token was accepted a second time."
    }
    catch {
        if ($_.Exception.Response.StatusCode.value__ -ne 401) {
            throw
        }
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

    $taskBody = @{
        tool_name = "create_task"
        arguments = @{
            title = "Verify Docker Security MCP"
            description = "Created by the repeatable stack verification."
        }
    } | ConvertTo-Json -Depth 4
    $taskExecution = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:8000/api/mcp/executions" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $taskBody
    if ($taskExecution.status -ne "completed" -or -not $taskExecution.result.task_id) {
        throw "Security MCP task execution did not persist successfully."
    }

    $emailBody = @{
        tool_name = "send_email"
        arguments = @{
            to = "client@example.com"
            subject = "Docker Security MCP verification"
            body = "This verification message must never be sent."
        }
    } | ConvertTo-Json -Depth 4
    $emailExecution = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:8000/api/mcp/executions" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $emailBody
    if ($emailExecution.status -ne "pending_approval" -or -not $emailExecution.approval_id) {
        throw "Security MCP email execution did not enter approval state."
    }

    $managerLoginBody = @{
        email = "manager@demo.local"
        password = "demo-password"
    } | ConvertTo-Json
    $managerLogin = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:8000/api/auth/login" `
        -ContentType "application/json" `
        -Body $managerLoginBody
    $managerHeaders = @{ Authorization = "Bearer $($managerLogin.access_token)" }
    Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:8000/api/approvals/$($emailExecution.approval_id)/decision" `
        -Headers $managerHeaders `
        -ContentType "application/json" `
        -Body '{"approved":false}' | Out-Null
    $approvedExecution = Invoke-RestMethod `
        -Method Get `
        -Uri "http://127.0.0.1:8000/api/mcp/executions/$($emailExecution.execution_id)" `
        -Headers $managerHeaders
    if (
        $approvedExecution.status -ne "rejected" -or
        $approvedExecution.result.delivery_mode
    ) {
        throw "Security MCP rejection did not prevent the real provider email action."
    }

    $workflowBody = @{
        prompt = "Find the Acme renewal policy and create a verification task"
    } | ConvertTo-Json
    $workflow = Invoke-RestMethod `
        -Method Post `
        -Uri "http://127.0.0.1:8000/api/agent/workflows" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body $workflowBody
    if (
        $workflow.status -ne "completed" -or
        ($workflow.actions | Where-Object { $_.tool_name -eq "send_email" })
    ) {
        throw "Agent workflow did not complete its provider-free safe actions."
    }

    Write-Host "Backend API, tenant isolation, session rotation, Postgres, Redis worker, RAG, governed provider actions, and workflow smoke tests passed."
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
