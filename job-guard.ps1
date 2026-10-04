# job-guard.ps1 - PreToolUse guard for UNATTENDED Grok Build runs (Doug / notLloyd, 2026-09-29).
# Registered globally in C:\Users\DougJ\.grok\hooks\unattended-job-gate.json, but INERT unless the
# launching process sets $env:GROK_JOB_DIR (only the job's launch.ps1 does that).
# Why: under --permission-mode dontAsk an unapproved call is "declined" and the headless turn ends
# (stopReason=cancelled, exit 0). A hook "deny" is instead reported to the model and the run continues.
# So runs use --always-approve + hard --deny rules, and this hook enforces the allowlist non-fatally.
$ErrorActionPreference = 'Stop'
if (-not $env:GROK_JOB_DIR) { exit 0 }
$jobDir = $env:GROK_JOB_DIR.TrimEnd('\')
$repo   = ($env:GROK_JOB_REPO  -as [string]).TrimEnd('\')
$logf   = Join-Path $jobDir 'guard.log'
function Log([string]$m) { try { Add-Content -LiteralPath $logf -Value ("[{0}] {1}" -f (Get-Date -Format s), $m) -Encoding utf8 } catch {} }
function Deny([string]$why) {
  Log "DENY $why"
  $reason = "Blocked by the unattended-job guard (this is NOT fatal - keep working and make your next tool call). $why"
  @{ decision = 'deny'; reason = $reason; hookSpecificOutput = @{ hookEventName = 'PreToolUse'; permissionDecision = 'deny'; permissionDecisionReason = $reason } } | ConvertTo-Json -Compress -Depth 5
  exit 0
}
try {
  $raw = [Console]::In.ReadToEnd()
  $ev = $raw | ConvertFrom-Json
  $tool = [string]$ev.toolName
  $in = $ev.toolInput
  $short = ($in | ConvertTo-Json -Compress -Depth 4); if ($short.Length -gt 300) { $short = $short.Substring(0,300) + '...' }
  Log "$tool $short"

  $isShell = $tool -match '^(run_terminal_command|Bash|bash|shell|execute|Execute|run_command)$'
  if ($isShell) {
    $cmd = [string]($in.command ?? $in.cmd ?? '')
    $low = $cmd.ToLowerInvariant()
    # Hard blocks (network, patreon, git writes, deletion, installs)
    $bad = @('patreon.com','lam token','lam patreon','-m lam','lam.exe','invoke-webrequest','invoke-restmethod','curl','wget','start-bitstransfer',
             'urllib','requests.','httpx.get','httpx.post','httpx.client(','httpx.stream','socket','http.client',
             'remove-item','os.remove','os.unlink','.unlink(','rmtree','shutil.move','clear-content','format-volume',
             'git add','git commit','git push','git checkout','git switch','git reset','git restore','git stash','git clean','git rm','git mv','git merge','git rebase','git pull','git fetch','git tag','git config','git apply','git cherry-pick','git worktree','git branch -',
             'pip install','pip.exe install','uv pip','npm','npx','winget','choco','ollama','start-process','--apply','.patreon-dl','patreon_cookie','_cookie.txt','browser-profiles','lam\secrets','lam/secrets','.env','set-executionpolicy','new-service','schtasks')
    foreach ($b in $bad) { if ($low.Contains($b)) { Deny "Shell command contains a forbidden pattern ('$b'). Allowed shell: '.venv\Scripts\python.exe -m pytest ...', read-only git (git status/diff/log/show), and read-only listing/reading commands. Use your Read/Search/Edit tools for files." } }
    if ($low -match '(^|[\s;&|(])(rm|rmdir|del|erase|rd|ri|iwr|irm|reg|mv|move|ren)\s') { Deny "Shell command uses a forbidden verb (delete/move/network/registry). Use your Edit tools for file changes; nothing may be deleted." }
    # Allowlist, per segment
    $segs = [regex]::Split($cmd, '\s*(?:;|&&|\|\||\|)\s*') | Where-Object { $_.Trim() -ne '' }
    $ok = '^(?:\.\\|\./)?\.venv[\\/]Scripts[\\/](?:python(?:\.exe)?\s+-m\s+pytest|pytest(?:\.exe)?)\b' + '|' +
          '^(?:\.\\|\./)?\.venv[\\/]Scripts[\\/]python(?:\.exe)?\s+-c\s' + '|' +
          '^(?:python|py)(?:\.exe)?\s+-m\s+pytest\b' + '|' +
          '^git\s+(?:status|diff|log|show|ls-files|blame)\b' + '|' +
          '^(?:rg|Get-Content|gc|cat|type|Select-String|sls|Get-ChildItem|gci|ls|dir|Test-Path|Resolve-Path|Get-Item|Get-Location|pwd|Set-Location|cd|echo|Write-Output|Write-Host|Select-Object|Out-String|Format-Table|Format-List|Measure-Object|Sort-Object|Where-Object|ForEach-Object|Get-Date|Get-FileHash|Out-Null)\b'
    foreach ($s in $segs) {
      $t = $s.Trim().TrimStart('&').Trim().Trim('"').Trim("'")
      if ($t -notmatch $ok) { Deny "Shell segment not on the allowlist: '$t'. Use plain commands (no if/else wrappers). Allowed: '.venv\Scripts\python.exe -m pytest -q', '.venv\Scripts\python.exe -c ""...""' (read-only, no network/deletes), git status|diff|log|show, rg, Get-Content, Get-ChildItem, Select-String. Prefer your Read/Search tools." }
    }
    exit 0
  }

  # File writes: only inside the repo (not .git) or the job folder.
  if ($tool -match '(?i)^(search_replace|edit|write|multiedit|create_file|write_file|str_replace|apply_patch|delete_file|rename_file|move_file)$') {
    $paths = @()
    foreach ($k in 'file_path','path','target_file','filePath','target','file','new_path','destination') { if ($in.$k) { $paths += [string]$in.$k } }
    if ($tool -match '(?i)delete') { Deny "Deleting files is not allowed in this job (Doug's rule: nothing permanently deleted). Leave the file and note it in the report." }
    foreach ($p in $paths) {
      $full = if ([IO.Path]::IsPathRooted($p)) { [IO.Path]::GetFullPath($p) } else { [IO.Path]::GetFullPath((Join-Path ($ev.cwd ?? $repo) $p)) }
      $inRepo = $repo -and $full.StartsWith($repo + '\', [StringComparison]::OrdinalIgnoreCase)
      $inJob  = $full.StartsWith($jobDir + '\', [StringComparison]::OrdinalIgnoreCase)
      if ($full -match '(?i)\\\.git(\\|$)') { Deny "Writing inside .git is forbidden." }
      if ($full -match '(?i)\\\.env|patreon_cookie|_cookie\.txt|browser-profiles|\\lam\\secrets\\') { Deny "Writing secrets/cookie/profile paths is forbidden." }
      if (-not ($inRepo -or $inJob)) { Deny "Writes are allowed only inside $repo or $jobDir (got $full)." }
    }
    exit 0
  }

  # Reads of secrets
  if ($tool -match '(?i)^(read_file|read|grep|list_dir)$') {
    $p = [string]($in.file_path ?? $in.path ?? $in.target_file ?? '')
    if ($p -match '(?i)\\\.git\\|\.env|patreon_cookie|_cookie\.txt|lam[\\/]secrets|browser-profiles|\.patreon-dl') { Deny "Reading secrets/cookies/profiles/.git internals is forbidden." }
    exit 0
  }
  if ($tool -match '(?i)web_search|web_fetch|WebSearch|WebFetch|browse') { Deny "Network tools are forbidden in this job. Use the saved evidence files and repo fixtures." }
  exit 0
} catch {
  Log "guard error: $($_.Exception.Message)"
  exit 0
}
