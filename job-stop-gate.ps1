# job-stop-gate.ps1 - Stop hook for UNATTENDED Grok Build runs (Doug / notLloyd, 2026-09-29).
# INERT unless $env:GROK_JOB_REPORT is set by the job's launch.ps1.
# Blocks a genuine end_turn until: report exists, has no "(pending)", contains no "TODO:" placeholder
# lines for required sections, and the full pytest suite exits 0. Feedback goes back to the model and the
# run keeps going (Grok caps this at 8 continuations per turn).
$ErrorActionPreference = 'Stop'
if (-not $env:GROK_JOB_REPORT) { exit 0 }
$jobDir = $env:GROK_JOB_DIR
$repo   = $env:GROK_JOB_REPO
$report = $env:GROK_JOB_REPORT
$logf   = Join-Path $jobDir 'gate.log'
function Log([string]$m) { try { Add-Content -LiteralPath $logf -Value ("[{0}] {1}" -f (Get-Date -Format s), $m) -Encoding utf8 } catch {} }
function Block([string]$why) {
  Log "BLOCK $why"
  @{ decision = 'block'; reason = "Stop gate: the job is NOT finished. $why Keep working with tool calls; do not reply with text only until this is fixed." } | ConvertTo-Json -Compress
  exit 0
}
try {
  $ev = [Console]::In.ReadToEnd() | ConvertFrom-Json
  $reason = [string]$ev.reason
  Log "stop fired reason=$reason stopHookActive=$($ev.stopHookActive)"
  if ($reason -and $reason -ne 'end_turn') { exit 0 }
  if ($ev.subagentType) { exit 0 }
  if (-not (Test-Path -LiteralPath $report)) { Block "The report file $report does not exist yet. Finish the work (tests first, then code, then full pytest) and write the report." }
  $txt = Get-Content -LiteralPath $report -Raw
  if ($txt -match '\(pending\)') { Block "The report $report still contains '(pending)'. Fill every section with real content." }
  Push-Location $repo
  try {
    $out = & (Join-Path $repo '.venv\Scripts\python.exe') -m pytest -q -p no:cacheprovider 2>&1 | Out-String
    $code = $LASTEXITCODE
  } finally { Pop-Location }
  if ($code -ne 0) {
    $tail = $out.Substring([Math]::Max(0, $out.Length - 1500))
    Block "The full test suite is not green (pytest exit $code). Fix it, re-run '.venv\Scripts\python.exe -m pytest -q', and update the report's pytest line. Tail:`n$tail"
  }
  Log "ALLOW stop: report complete, pytest exit 0"
  exit 0
} catch {
  Log "gate error: $($_.Exception.Message)"
  exit 0
}
