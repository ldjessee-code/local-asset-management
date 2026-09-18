# SPDX-License-Identifier: AGPL-3.0-or-later
# PowerShell launcher. If scripts are blocked, use start.cmd instead.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$StartArgs = @($args)

function Test-Python {
    param([string]$Exe, [string[]]$Prefix = @())
    if (-not $Exe) { return $false }
    if (($Exe.Contains("\") -or $Exe.Contains("/")) -and -not (Test-Path -LiteralPath $Exe)) {
        return $false
    }
    try {
        & $Exe @Prefix -c "import sys" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Invoke-Start {
    param([string]$Exe, [string[]]$Prefix = @())
    & $Exe @Prefix (Join-Path $PSScriptRoot "start.py") @StartArgs
    exit $LASTEXITCODE
}

$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Python $venvPy) { Invoke-Start $venvPy }

foreach ($name in @("python", "python3")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Python $cmd.Source)) { Invoke-Start $cmd.Source }
}

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) {
    foreach ($ver in @("3.13", "3.12", "3.11", "3.14")) {
        if (Test-Python $py.Source @("-$ver")) { Invoke-Start $py.Source @("-$ver") }
    }
}

Write-Error @"
Could not find a working Python 3.11+.
The py launcher may point at a version that is no longer installed (try: py -0p).
Install Python from https://www.python.org/downloads/ and check 'Add python.exe to PATH'.
"@
exit 1
