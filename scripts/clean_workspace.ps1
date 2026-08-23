# Remove local build artifacts, caches, and test databases.
# Usage: powershell.exe -File scripts/clean_workspace.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$paths = @(
  ".coverage",
  ".ruff_cache",
  "dist",
  "build",
  "helix_support.egg-info",
  "artifacts"
)

foreach ($relative in $paths) {
  $target = Join-Path $root $relative
  if (Test-Path $target) {
    Remove-Item -Recurse -Force $target
    Write-Host "removed $relative"
  }
}

Get-ChildItem -Path $root -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
  ForEach-Object {
    Remove-Item -Recurse -Force $_.FullName
    Write-Host "removed $($_.FullName.Substring($root.Length + 1))"
  }

Write-Host "workspace clean"
