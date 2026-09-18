@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Prefer a working .venv so we never ask the py launcher (it may
rem default to a Python version that is registered but not installed).
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    "%~dp0.venv\Scripts\python.exe" "%~dp0start.py" %*
    exit /b %ERRORLEVEL%
  )
)

where python >nul 2>&1
if not errorlevel 1 (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    python "%~dp0start.py" %*
    exit /b %ERRORLEVEL%
  )
)

where python3 >nul 2>&1
if not errorlevel 1 (
  python3 -c "import sys" >nul 2>&1
  if not errorlevel 1 (
    python3 "%~dp0start.py" %*
    exit /b %ERRORLEVEL%
  )
)

where py >nul 2>&1
if not errorlevel 1 (
  for %%V in (3.13 3.12 3.11 3.14) do (
    py -%%V -c "import sys" >nul 2>&1
    if not errorlevel 1 (
      py -%%V "%~dp0start.py" %*
      exit /b %ERRORLEVEL%
    )
  )
)

echo Could not find a working Python 3.11+.
echo The "py" launcher may point at a version that is no longer installed.
echo Install Python from https://www.python.org/downloads/ and check "Add python.exe to PATH".
echo Or run:  py -0p
exit /b 1
