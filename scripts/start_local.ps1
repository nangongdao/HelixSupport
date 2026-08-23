param(
    [int]$PreferredPort = 8000
)

$ErrorActionPreference = 'Stop'
$workspace = (Get-Location).Path
$port = $PreferredPort
$existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $port += 1
}

$python = (Get-Command python -ErrorAction Stop).Source
$arguments = @(
    '-m'
    'uvicorn'
    'app.main:app'
    '--host'
    '127.0.0.1'
    '--port'
    [string]$port
    '--no-access-log'
)
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $workspace -WindowStyle Hidden -PassThru

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 300
    try {
        $health = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/health/ready" -f $port) -TimeoutSec 2
        if ($health.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        # The server may still be importing the application.
    }
}

if (-not $ready) {
    throw "Helix Support did not become ready on port $port"
}

[ordered]@{
    pid = $process.Id
    port = $port
    url = "http://127.0.0.1:$port"
    ready = $true
} | ConvertTo-Json -Compress
