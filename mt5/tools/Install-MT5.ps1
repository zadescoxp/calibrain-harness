param(
    [string]$TerminalDataPath
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceExperts = Join-Path $projectRoot "Experts\CalibrainAgents"
$sourceInclude = Join-Path $projectRoot "Include\Calibrain"

if (-not (Test-Path (Join-Path $sourceExperts "CalibrainAgents.mq5"))) {
    throw "Expert Advisor source is missing: $sourceExperts"
}
if (-not (Test-Path (Join-Path $sourceInclude "Agents\Nova.mqh"))) {
    throw "Agent include sources are missing: $sourceInclude"
}

if (-not $TerminalDataPath) {
    $terminalRoot = Join-Path $env:APPDATA "MetaQuotes\Terminal"
    if (-not (Test-Path $terminalRoot)) {
        throw "MT5 terminal data folder was not found. Pass -TerminalDataPath from MT5 File > Open Data Folder."
    }
    $candidates = @(
        Get-ChildItem -LiteralPath $terminalRoot -Directory |
            Where-Object { Test-Path (Join-Path $_.FullName "MQL5") }
    )
    if ($candidates.Count -ne 1) {
        throw "Found $($candidates.Count) MT5 data folders. Pass the intended one with -TerminalDataPath."
    }
    $TerminalDataPath = $candidates[0].FullName
}

$mql5Path = Join-Path $TerminalDataPath "MQL5"
if (-not (Test-Path $mql5Path)) {
    throw "MQL5 folder not found at '$mql5Path'. Use the path shown by MT5 File > Open Data Folder."
}

$destinationExperts = Join-Path $mql5Path "Experts\CalibrainAgents"
$destinationInclude = Join-Path $mql5Path "Include\Calibrain"
New-Item -ItemType Directory -Force -Path $destinationExperts | Out-Null
New-Item -ItemType Directory -Force -Path $destinationInclude | Out-Null
Copy-Item -Path (Join-Path $sourceExperts "*") -Destination $destinationExperts -Recurse -Force
Copy-Item -Path (Join-Path $sourceInclude "*") -Destination $destinationInclude -Recurse -Force

Write-Host "Installed CalibrainAgents into '$mql5Path'."
Write-Host "Compile Experts\CalibrainAgents\CalibrainAgents.mq5 in MetaEditor."
