# sim-stop-gate.ps1 - Stop / StopCancelled hook for UNATTENDED Grok Build SIMULATION jobs (notLloyd, 2026-09-30).
# Sibling of job-stop-gate.ps1 (pytest) and research-stop-gate.ps1 (web research). INERT unless
# $env:GROK_SJOB_REPORT is set by a simulation job's launch.ps1. On a genuine end_turn it blocks the stop
# until the report exists, contains RUN FINISHED, has no "(pending)", and (if GROK_SJOB_REPORT2 is set) the
# second copy exists too. Feedback goes back to the model and the run keeps going (Grok caps this at 8
# continuations per turn). StopCancelled fires are only logged.
$ErrorActionPreference = 'Stop'
if (-not $env:GROK_SJOB_REPORT) { exit 0 }
$jobDir  = $env:GROK_SJOB_DIR
$report  = $env:GROK_SJOB_REPORT
$report2 = $env:GROK_SJOB_REPORT2
$minChars = if ($env:GROK_SJOB_MINCHARS) { [int]$env:GROK_SJOB_MINCHARS } else { 3000 }
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
  if (-not (Test-Path -LiteralPath $report)) { Block "The report file $report does not exist yet. Write it with your write tool (all sections from the brief; partial results are fine, marked incomplete), then finish." }
  $txt = Get-Content -LiteralPath $report -Raw
  if ($txt -match '\(pending\)') { Block "The report $report still contains '(pending)'. Fill every section with real content, or 'not tested: <reason>'." }
  if ($txt.Length -lt $minChars) { Block "The report $report is only $($txt.Length) characters. Write the full report (answer first, inputs table, one section per check, measured vs estimated, suggestions, sources, reproduce)." }
  if ($txt -notmatch 'RUN FINISHED') { Block "The report $report must end with the line RUN FINISHED." }
  if ($report2 -and -not (Test-Path -LiteralPath $report2)) { Block "The repo copy of the report $report2 does not exist. Write it too." }
  Log "ALLOW stop: report complete ($($txt.Length) chars)"
  exit 0
} catch {
  Log "gate error: $($_.Exception.Message)"
  exit 0
}
