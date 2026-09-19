<#
.SYNOPSIS
    Builds and installs hockey-analyzer so it can be run as the `hockey-analyzer` command.

.DESCRIPTION
    Installs the project in editable mode into the active Python environment,
    which registers the `hockey-analyzer` console script (defined in
    pyproject.toml's [project.scripts]) on PATH. Re-run this script after
    pulling changes that add or update dependencies; code changes themselves
    take effect immediately since the install is editable.
#>

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "python was not found on PATH."
}

Write-Host "Installing hockey-analyzer (editable) from $repoRoot ..."
python -m pip install -e $repoRoot

Write-Host ""
Write-Host "Done. Run the app with: hockey-analyzer"
