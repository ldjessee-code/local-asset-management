# research-stop-gate.ps1 - Stop / StopCancelled hook for UNATTENDED Grok Build RESEARCH runs (notLloyd, 2026-09-30).
# Sibling of job-stop-gate.ps1 (repo jobs; runs pytest). INERT unless $env:GROK_RJOB_REPORT is set by a
# research job's launch.ps1. On a genuine end_turn it blocks the stop until the report exists, has no
# "(pending)", is not a stub, and has the required markers. Feedback goes back to the model and the run
# keeps going (Grok caps this at 8 continuations per turn). StopCancelled fires are only logged.
$ErrorActionPreference = 'Stop'
if (-not $env:GROK_RJOB_REPORT) { exit 0 }
$jobDir = $env:GROK_RJOB_DIR
$report = $env:GROK_RJOB_REPORT
$minChars = if ($env:GROK_RJOB_MINCHARS) { [int]$env:GROK_RJOB_MINCHARS } else { 20000 }
$logf   = Join-Path $jobDir 'gate.log'
function Log([string]$m) { try { Add-Content -LiteralPath $logf -Value ("[{0}] {1}" -f (Get-Date -Format s), $m) -Encoding utf8 } catch {} }
function Block([string]$why) {
  Log "BLOCK $why"
  @{ decision = 'block'; reason = "Stop gate: the job is NOT finished. $why Keep working with tool calls; do not reply with text only until this is fixed." } | ConvertTo-Json -Compress
  exit 0
}
try {
  $ev = [Console]::In.ReadToEnd() | ConvertFrom-Json
  $evName = [string]($ev.hook_event_name ?? $ev.hookEventName)
  $reason = [string]$ev.reason
  if ($evName -match '(?i)cancel') { Log "StopCancelled reason=$reason by=$($ev.cancelledBy) details=$($ev.reasonDetails)"; exit 0 }
  Log "stop fired reason=$reason stopHookActive=$($ev.stopHookActive)"
  if ($reason -and $reason -ne 'end_turn') { exit 0 }
  if ($ev.subagentType) { exit 0 }
  if (-not (Test-Path -LiteralPath $report)) { Block "The report file $report does not exist yet. Write it (all sections from the BRIEF), then finish." }
  $txt = Get-Content -LiteralPath $report -Raw
  if ($txt -match '\(pending\)') { Block "The report $report still contains '(pending)'. Fill every section with real content (or 'searched, nothing found')." }
  if ($txt.Length -lt $minChars) { Block "The report is only $($txt.Length) characters; the BRIEF asks for a detailed per-item reference for all 20 checklist items plus Q2-Q5. Finish the missing sections." }
  $missing = @()
  foreach ($m in 'Opened and verified','Search-indexed only','RUN FINISHED') { if ($txt -notmatch [regex]::Escape($m)) { $missing += $m } }
  if ($missing.Count) { Block ("The report is missing required parts: " + ($missing -join ', ') + ". Add the sources split ('Opened and verified' / 'Search-indexed only') and end the file with the line RUN FINISHED.") }
  Log "ALLOW stop: report complete ($($txt.Length) chars)"
  exit 0
} catch {
  Log "gate error: $($_.Exception.Message)"
  exit 0
}
