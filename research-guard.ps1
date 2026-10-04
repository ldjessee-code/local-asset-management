# research-guard.ps1 - PreToolUse guard for UNATTENDED Grok Build RESEARCH runs (notLloyd, 2026-09-30).
# Sibling of job-guard.ps1 (which is for repo jobs and blocks the web). Registered globally in
# C:\Users\DougJ\.grok\hooks\unattended-job-gate.json, but INERT unless the launching process sets
# $env:GROK_RJOB_DIR (only a research job's launch.ps1 does that, process-scoped).
# Policy: web search/fetch allowed; ALL shell denied; MCP tools denied; file writes only inside the job
# folder or $env:GROK_RJOB_OUTDIR; reads only inside those two folders; no deletes.
# Denials are NON-FATAL (the model is told and keeps going), unlike dontAsk declines.
$ErrorActionPreference = 'Stop'
if (-not $env:GROK_RJOB_DIR) { exit 0 }
$jobDir = $env:GROK_RJOB_DIR.TrimEnd('\')
$outDir = ($env:GROK_RJOB_OUTDIR -as [string]).TrimEnd('\')
$logf   = Join-Path $jobDir 'guard.log'
function Log([string]$m) { try { Add-Content -LiteralPath $logf -Value ("[{0}] {1}" -f (Get-Date -Format s), $m) -Encoding utf8 } catch {} }
function Deny([string]$why) {
  Log "DENY $why"
  $reason = "Blocked by the unattended research guard (this is NOT fatal - keep working and make your next tool call). $why"
  @{ decision = 'deny'; reason = $reason; hookSpecificOutput = @{ hookEventName = 'PreToolUse'; permissionDecision = 'deny'; permissionDecisionReason = $reason } } | ConvertTo-Json -Compress -Depth 5
  exit 0
}
function Inside([string]$full, [string]$root) { return $root -and ($full.Equals($root, [StringComparison]::OrdinalIgnoreCase) -or $full.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) }
function Resolve-ToolPath([string]$p, $ev) {
  $p = $p.Trim().Trim('"').Trim("'")
  if ($p.StartsWith('~')) { $p = Join-Path $HOME $p.Substring(1).TrimStart('\','/') }
  if ([IO.Path]::IsPathRooted($p)) { return [IO.Path]::GetFullPath($p) }
  $base = if ($ev.cwd) { [string]$ev.cwd } else { $jobDir }
  return [IO.Path]::GetFullPath((Join-Path $base $p))
}
try {
  $raw = [Console]::In.ReadToEnd()
  $ev = $raw | ConvertFrom-Json
  $tool = [string]$ev.toolName
  $in = $ev.toolInput
  $short = ($in | ConvertTo-Json -Compress -Depth 4); if ($short.Length -gt 300) { $short = $short.Substring(0,300) + '...' }
  Log "$tool $short"

  # 1. Shell: denied outright.
  if ($tool -match '(?i)^(run_terminal_command|bash|shell|execute|run_command|powershell|terminal)$') {
    Deny "Shell commands are not allowed in this research job. Use web_search / web_fetch for sources and your file write/edit tools for the report."
  }
  # 2. MCP tools (server__tool or dispatchers): denied (no messages, no account actions).
  if ($tool -match '__' -or $tool -match '(?i)^(use_tool|CallMcpTool|mcp)$') { Deny "MCP/connector tools are not allowed in this research job." }
  # 3. Web: allowed.
  if ($tool -match '(?i)web_search|web_fetch|WebSearch|WebFetch') { exit 0 }

  # Collect any paths the tool names.
  $paths = @()
  foreach ($k in 'file_path','path','target_file','filePath','target','file','new_path','destination','target_directory','directory','dir') { if ($in.$k) { $paths += [string]$in.$k } }
  if ($in.patch -or $in.input) { $patchText = [string]($in.patch ?? $in.input); foreach ($m in [regex]::Matches($patchText, '\*\*\* (?:Add|Update|Delete|Move to) File: *(.+)')) { $paths += $m.Groups[1].Value.Trim() } }

  # 4. Writes / edits: only inside the job folder or the output folder; never delete.
  $isWrite = ($tool -notmatch '(?i)^todo_write$') -and ($tool -match '(?i)^(search_replace|edit|write|multiedit|multi_edit|create_file|write_file|str_replace|str_replace_editor|apply_patch|notebook_edit|edit_notebook|delete_file|rename_file|move_file)$')
  if ($isWrite) {
    if ($tool -match '(?i)delete' -or ($patchText -and $patchText -match '\*\*\* Delete File:')) { Deny "Deleting files is not allowed (Doug's rule: nothing permanently deleted). Leave the file and note it in the report." }
    if ($paths.Count -eq 0) { Deny "Could not tell which file this write targets. Use a write/edit tool with an explicit absolute path inside $jobDir or $outDir." }
    foreach ($p in $paths) {
      $full = Resolve-ToolPath $p $ev
      if (-not ((Inside $full $jobDir) -or (Inside $full $outDir))) { Deny "Writes are allowed only inside $jobDir or $outDir (got $full)." }
    }
    exit 0
  }

  # 5. Reads / listing / search: only inside the job folder or the output folder.
  if ($tool -match '(?i)^(read_file|read|grep|glob|list_dir|ls|search_files|file_search|codebase_search)$') {
    foreach ($p in $paths) {
      $full = Resolve-ToolPath $p $ev
      if (-not ((Inside $full $jobDir) -or (Inside $full $outDir))) { Deny "Reads are allowed only inside $jobDir or $outDir (got $full). Everything you need is there or on the web." }
    }
    exit 0
  }
  exit 0
} catch {
  Log "guard error: $($_.Exception.Message)"
  exit 0
}
