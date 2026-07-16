param(
    [string]$WorkOsToken,
    [string]$SecretsDirectory = ".secrets"
)

$ErrorActionPreference = "Stop"

if (-not $WorkOsToken) {
    $secureToken = Read-Host "Paste the one-time Work OS OpenClaw token" -AsSecureString
    $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    try {
        $WorkOsToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    }
}
if (-not $WorkOsToken.StartsWith("wos_oc_")) {
    throw "The supplied value is not a Work OS OpenClaw token."
}

$resolvedDirectory = [IO.Path]::GetFullPath((Join-Path (Get-Location) $SecretsDirectory))
[IO.Directory]::CreateDirectory($resolvedDirectory) | Out-Null
$encoding = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText(
    (Join-Path $resolvedDirectory "openclaw-workos-token"),
    $WorkOsToken,
    $encoding
)

$gatewayBytes = [byte[]]::new(32)
$random = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $random.GetBytes($gatewayBytes)
}
finally {
    $random.Dispose()
}
$gatewayToken = [Convert]::ToBase64String($gatewayBytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
[IO.File]::WriteAllText(
    (Join-Path $resolvedDirectory "openclaw-gateway-token"),
    $gatewayToken,
    $encoding
)

if ($IsWindows -or $env:OS -eq "Windows_NT") {
    & icacls.exe $resolvedDirectory /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" | Out-Null
}

Write-Host "OpenClaw Docker secrets were written under $resolvedDirectory."
Write-Host "Gateway token (shown once): $gatewayToken"
Write-Host "Start with: docker compose -f docker-compose.yml -f docker-compose.openclaw.yml --profile openclaw up -d"
