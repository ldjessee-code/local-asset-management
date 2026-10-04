# sim-guard.ps1 - PreToolUse guard for UNATTENDED Grok Build SIMULATION jobs (notLloyd, 2026-09-30).
# Sibling of job-guard.ps1 (repo code jobs, pytest-only shell) and research-guard.ps1 (web research, no shell).
# Registered globally in C:\Users\DougJ\.grok\hooks\unattended-job-gate.json, but INERT unless the launching
# process sets $env:GROK_SJOB_DIR (only a simulation job's launch.ps1 does that, process-scoped).
# Policy:
#   * Shell: python (scripts that live in the job folder, or python -c), read-only git, read-only listing,
#     mkdir / delete / move ONLY inside the job folder. No installs, no network tools, no git writes,
#     no Start-Process. Python code (the -c text, the entry script and every .py in the job folder) is
#     scanned for network / subprocess / install / git use and for writes that name a Turquenish path.
#   * File tools: writes only inside the job folder, plus the one report file $env:GROK_SJOB_REPORT.
#     Anything else under $env:GROK_SJOB_RO (semicolon-separated read-only roots) is read-only.
#   * Web search/fetch allowed (docs lookups). MCP/connector tools denied.
# Denials are NON-FATAL (the model is told and keeps going), unlike dontAsk declines. Best-effort, not a sandbox.
$ErrorActionPreference = 'Stop'
if (-not $env:GROK_SJOB_DIR) { exit 0 }
$jobDir = [IO.Path]::GetFullPath($env:GROK_SJOB_DIR).TrimEnd('\')
$repo   = if ($env:GROK_SJOB_REPO) { [IO.Path]::GetFullPath($env:GROK_SJOB_REPO).TrimEnd('\') } else { $jobDir }
$report = if ($env:GROK_SJOB_REPORT) { [IO.Path]::GetFullPath($env:GROK_SJOB_REPORT) } else { '' }
$roRoots = @(($env:GROK_SJOB_RO -as [string]) -split ';' | Where-Object { $_.Trim() } | ForEach-Object { [IO.Path]::GetFullPath($_.Trim()).TrimEnd('\') })
$logf   = Join-Path $jobDir 'guard.log'
function Log([string]$m) { try { Add-Content -LiteralPath $logf -Value ("[{0}] {1}" -f (Get-Date -Format s), $m) -Encoding utf8 } catch {} }
function Deny([string]$why) {
  Log "DENY $why"
  $reason = "Blocked by the unattended-job guard (this is NOT fatal - keep working and make your next tool call). $why"
  @{ decision = 'deny'; reason = $reason; hookSpecificOutput = @{ hookEventName = 'PreToolUse'; permissionDecision = 'deny'; permissionDecisionReason = $reason } } | ConvertTo-Json -Compress -Depth 5
  exit 0
}
function Inside([string]$full, [string]$root) { return $root -and ($full.Equals($root, [StringComparison]::OrdinalIgnoreCase) -or $full.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) }
function Resolve-ToolPath([string]$p, [string]$base) {
  $p = $p.Trim().Trim('"').Trim("'").Replace('/', '\')
  if ($p.StartsWith('~')) { $p = Join-Path $HOME $p.Substring(1).TrimStart('\') }
  if ([IO.Path]::IsPathRooted($p)) { return [IO.Path]::GetFullPath($p) }
  return [IO.Path]::GetFullPath((Join-Path $base $p))
}
# Patterns that are never allowed in shell text or python code.
$netBad = @('invoke-webrequest','invoke-restmethod','curl','wget','start-bitstransfer','bitsadmin','certutil','urllib','requests.','import requests','httpx','aiohttp','socket','http.client','http.server','ftplib','smtplib','paramiko','webbrowser')
$procBad = @('subprocess','os.system','os.popen','os.spawn','os.exec','ctypes','start-process','set-executionpolicy','schtasks','new-service','format-volume','stop-process','taskkill')
$pkgBad = @('pip install','pip.exe install','pip3 install','pip uninstall','ensurepip','uv pip','uv add','conda ','npm','npx','winget','choco','scoop ')
$gitBad = @('git add','git commit','git push','git checkout','git switch','git reset','git restore','git stash','git clean','git rm','git mv','git merge','git rebase','git pull','git fetch','git tag','git config','git apply','git cherry-pick','git worktree','git branch -','git init','git remote')
$secretBad = @('.env','.ssh','.grok\','.grok/','cookie','id_rsa','credentials')
function Check-PyText([string]$text, [string]$label) {
  $low = $text.ToLowerInvariant()
  foreach ($b in ($netBad + $procBad + $pkgBad + $gitBad)) { if ($low.Contains($b)) { Deny "Python code in $label contains a forbidden pattern ('$b'). No network, subprocess, installs or git from python. Remove it and retry." } }
  $writes = '(?i)open\s*\([^,)]*,\s*(?:mode\s*=\s*)?[rf]?[''"][rbt]*[wax+][rwxabt+]*[''"]|mode\s*=\s*[''"][rbt]*[wax+]|\.open\(\s*[''"][rbt]*[wax+]|write_text|write_bytes|savefig|shutil\.|os\.remove|os\.unlink|\.unlink\(|rmtree|os\.rename|os\.replace|\.rename\(|\.replace\(\s*[''"]?[a-z]:|to_csv|np\.save|savez|\.save\('
  if ($low.Contains('turquenish') -and $text -match $writes) { Deny "$label names a Turquenish path and also writes files. Turquenish is read-only for python: read the setting doc with your read_file tool (or a read-only script), and write the Dropbox report copy with your write tool, not python." }
  if ($text -match '(?i)os\.remove|os\.unlink|\.unlink\(|rmtree|os\.rename|os\.replace|shutil\.move') {
    foreach ($m in [regex]::Matches($text, '(?i)[a-z]:[\\/]{1,2}[^''"\r\n]*')) {
      $pp = $m.Value.Replace('\\', '\')
      try { $full = [IO.Path]::GetFullPath($pp) } catch { continue }
      if (-not (Inside $full $jobDir)) { Deny "$label deletes/moves files and names a path outside the job folder ($full). Deleting or moving is allowed only inside $jobDir." }
    }
  }
}
function Split-Segments([string]$cmd) {
  # Split on ; && || | (and newlines) outside single/double quotes.
  $out = New-Object System.Collections.Generic.List[string]; $sb = New-Object System.Text.StringBuilder; $q = [char]0
  for ($i = 0; $i -lt $cmd.Length; $i++) {
    $ch = $cmd[$i]
    if ($q -ne [char]0) { [void]$sb.Append($ch); if ($ch -eq $q) { $q = [char]0 }; continue }
    if ($ch -eq '"' -or $ch -eq "'") { $q = $ch; [void]$sb.Append($ch); continue }
    if ($ch -eq ';' -or $ch -eq "`n" -or $ch -eq "`r" -or $ch -eq '|' -or ($ch -eq '&' -and $i + 1 -lt $cmd.Length -and $cmd[$i + 1] -eq '&')) {
      if ($ch -eq '&' -or ($ch -eq '|' -and $i + 1 -lt $cmd.Length -and $cmd[$i + 1] -eq '|')) { $i++ }
      if ($sb.ToString().Trim()) { $out.Add($sb.ToString()) }; [void]$sb.Clear(); continue
    }
    [void]$sb.Append($ch)
  }
  if ($sb.ToString().Trim()) { $out.Add($sb.ToString()) }
  return ,$out.ToArray()
}
try {
  $raw = [Console]::In.ReadToEnd()
  $ev = $raw | ConvertFrom-Json
  $tool = [string]$ev.toolName
  $in = $ev.toolInput
  $cwd = if ($ev.cwd) { [string]$ev.cwd } else { $repo }
  $short = ($in | ConvertTo-Json -Compress -Depth 4); if ($short.Length -gt 300) { $short = $short.Substring(0,300) + '...' }
  Log "$tool $short"

  # 1. MCP / connector tools: denied (no messages, no account actions).
  if ($tool -match '__' -or $tool -match '(?i)^(use_tool|CallMcpTool|mcp)$') { Deny "MCP/connector tools are not allowed in this job." }
  # 2. Web: allowed (REBOUND docs etc.).
  if ($tool -match '(?i)web_search|web_fetch|WebSearch|WebFetch') { exit 0 }

  # 3. Shell
  if ($tool -match '(?i)^(run_terminal_command|bash|shell|execute|run_command|powershell|terminal)$') {
    $cmd = [string]($in.command ?? $in.cmd ?? '')
    $low = $cmd.ToLowerInvariant()
    foreach ($b in ($netBad + $procBad + $pkgBad + $gitBad + $secretBad)) { if ($low.Contains($b)) { Deny "Shell command contains a forbidden pattern ('$b'). No installs (rebound/matplotlib are already installed), no network tools, no git writes, no Start-Process. Allowed: python <script in the job folder>, python -c, read-only git (status/diff/log/show), Get-ChildItem/Get-Content/Select-String, and mkdir/delete/move inside $jobDir only." } }
    if ($low -match '(^|[\s;&|(])(iwr|irm|reg|ssh|scp|sftp|ftp)(\s|$)') { Deny "Shell command uses a forbidden verb (network/registry)." }
    $segs = Split-Segments $cmd
    $pyRe   = '^(?:python|py|python3)(?:\.exe)?(?=\s|$)|^(?:[a-z]:\\users\\dougj\\appdata\\local\\programs\\python\\python313\\python\.exe)(?=\s|$)'
    $roRe   = '^git\s+(?:status|diff|log|show|ls-files|blame|rev-parse)\b|^(?:rg|Get-Content|gc|cat|type|Select-String|sls|Get-ChildItem|gci|ls|dir|Test-Path|Resolve-Path|Get-Item|Get-Location|pwd|Set-Location|cd|echo|Write-Output|Write-Host|Select-Object|Out-String|Format-Table|Format-List|Measure-Object|Sort-Object|Where-Object|ForEach-Object|Get-Date|Get-FileHash|Out-Null|Get-Process|Start-Sleep|Measure-Command|where\.exe|where)(?=\s|$)'
    $mutRe  = '^(?:Remove-Item|ri|rm|rmdir|rd|del|erase|Move-Item|mv|move|mi|Rename-Item|ren|rni|New-Item|ni|mkdir|md|Copy-Item|cp|copy|cpi|Clear-Content|clc)(?=\s|$)'
    foreach ($s in $segs) {
      $t = $s.Trim().TrimStart('&').Trim()
      $tq = $t.Trim('"').Trim("'")
      if ($tq -match $pyRe) {
        $rest = ($tq -replace $pyRe, '').Trim()
        if ($rest -match '^-c\s') { Check-PyText $rest 'the python -c code'; continue }
        if ($rest -match '^-m\s+(\S+)') {
          $mod = $Matches[1]
          if ($rest -match '^-m\s+pip\s+(list|show|freeze)\b' -or $mod -in @('pytest','timeit','json.tool','cProfile','pstats')) { continue }
          Deny "python -m $mod is not allowed. Allowed: python <script>.py in $jobDir, python -c, python -m pip list/show/freeze, python -m pytest."
        }
        if ($rest -match '^(?:-[uOBIX]\S*\s+)*("[^"]+\.py"|''[^'']+\.py''|\S+\.py)') {
          $script = Resolve-ToolPath $Matches[1] $cwd
          if (-not (Inside $script $jobDir)) { Deny "Only scripts inside $jobDir may be run (got $script). Write your script there first." }
          if (Test-Path -LiteralPath $script) { Check-PyText (Get-Content -LiteralPath $script -Raw) $script }
          foreach ($f in Get-ChildItem -LiteralPath $jobDir -Filter *.py -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Length -lt 2MB }) {
            if ($f.FullName -ne $script) { Check-PyText (Get-Content -LiteralPath $f.FullName -Raw) $f.FullName }
          }
          continue
        }
        if ($rest -match '^(--version|-V)\s*$') { continue }
        Deny "Unrecognised python invocation '$t'. Use: python <script>.py (script in $jobDir) or python -c ""..."". "
      }
      if ($tq -match $roRe) {
        if ($tq -match '(?i)\s(>|>>|\*>|2>)\s*[^&\s]' -or $tq -match '(?i)(Set-Content|Add-Content|Out-File|Tee-Object)') {
          Deny "Output redirection/writing from shell is not allowed. Use your write tool for files."
        }
        continue
      }
      if ($tq -match $mutRe) {
        $toks = [regex]::Matches(($tq -replace $mutRe, ''), '"[^"]*"|''[^'']*''|\S+') | ForEach-Object { $_.Value }
        $pathToks = @($toks | Where-Object { $_ -notmatch '^-' -and $_ -notmatch '^(?i)(directory|file|\$true|\$false)$' })
        if ($pathToks.Count -eq 0) { Deny "Could not tell which path '$t' acts on. Give explicit paths inside $jobDir." }
        foreach ($p in $pathToks) {
          $full = Resolve-ToolPath $p $cwd
          if (-not (Inside $full $jobDir) -or $full.Equals($jobDir, [StringComparison]::OrdinalIgnoreCase)) { Deny "File-changing shell commands are allowed only on paths inside $jobDir (got $full). Nothing outside it may be created, deleted or moved." }
        }
        continue
      }
      Deny "Shell segment not on the allowlist: '$t'. Allowed: python <script in $jobDir>.py, python -c ""..."", read-only git (status/diff/log/show), Get-ChildItem/Get-Content/Select-String/Test-Path, Start-Sleep, and mkdir/Remove-Item/Move-Item inside $jobDir only. Prefer your read/write tools for files."
    }
    exit 0
  }

  # Collect any paths the tool names.
  $paths = @()
  foreach ($k in 'file_path','path','target_file','filePath','target','file','new_path','destination','target_directory','directory','dir','notebook_path') { if ($in.$k) { $paths += [string]$in.$k } }
  $patchText = $null
  if ($in.patch -or $in.input) { $patchText = [string]($in.patch ?? $in.input); foreach ($m in [regex]::Matches($patchText, '\*\*\* (?:Add|Update|Delete|Move to) File: *(.+)')) { $paths += $m.Groups[1].Value.Trim() } }

  # 4. Writes / edits / deletes
  $isWrite = ($tool -notmatch '(?i)^todo_write$') -and ($tool -match '(?i)^(search_replace|edit|write|multiedit|multi_edit|create_file|write_file|str_replace|str_replace_editor|apply_patch|notebook_edit|edit_notebook|delete_file|rename_file|move_file)$')
  if ($isWrite) {
    $isDelete = ($tool -match '(?i)delete|rename|move') -or ($patchText -and $patchText -match '\*\*\* (Delete File|Move to):')
    if ($paths.Count -eq 0) { Deny "Could not tell which file this write targets. Use an explicit absolute path inside $jobDir (or the report path $report)." }
    foreach ($p in $paths) {
      $full = Resolve-ToolPath $p $cwd
      if ($full -match '(?i)\\\.git(\\|$)') { Deny "Writing inside .git is forbidden." }
      if (Inside $full $jobDir) { continue }
      if ($report -and $full.Equals($report, [StringComparison]::OrdinalIgnoreCase) -and -not $isDelete) { continue }
      foreach ($r in $roRoots) { if (Inside $full $r) { Deny "$r is read-only (source docs). The only file you may write there is the report copy $report." } }
      Deny "Writes are allowed only inside $jobDir and to the report file $report (got $full). Existing repo files must not be changed."
    }
    exit 0
  }

  # 5. Reads: anything except secrets.
  if ($tool -match '(?i)^(read_file|read|grep|glob|list_dir|ls|search_files|file_search|codebase_search)$') {
    foreach ($p in $paths) { if ($p -match '(?i)\\\.git\\|[\\/]\.env|\.ssh|[\\/]\.grok[\\/]|cookie|credentials|id_rsa') { Deny "Reading secrets/.git internals/.grok config is forbidden." } }
    exit 0
  }
  exit 0
} catch {
  Log "guard error: $($_.Exception.Message)"
  exit 0
}
