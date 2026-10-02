param(
    [ValidateSet('up', 'stop', 'status')]
    [string]$Action = 'up'
)
$ErrorActionPreference = 'Stop'
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerPath = if ($dockerCommand) { $dockerCommand.Source } else {
    Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/resources/bin/docker.exe'
}
if (-not (Test-Path -LiteralPath $dockerPath)) {
    throw 'Docker Desktop executable was not found. Start/install Docker first.'
}
$composePath = Join-Path $PSScriptRoot 'searxng.compose.yml'
$previousSecret = $env:SEARXNG_SECRET
try {
    # Ephemeral instance secret, never printed or written to a tracked file.
    $secretBytes = [byte[]]::new(32)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($secretBytes) } finally { $rng.Dispose() }
    $env:SEARXNG_SECRET = [Convert]::ToBase64String($secretBytes)
    switch ($Action) {
        'up' { & $dockerPath compose -f $composePath up -d }
        'stop' { & $dockerPath compose -f $composePath stop }
        'status' { & $dockerPath compose -f $composePath ps }
    }
    if ($LASTEXITCODE -ne 0) { throw "Docker compose failed (exit $LASTEXITCODE)." }
} finally {
    $env:SEARXNG_SECRET = $previousSecret
}
