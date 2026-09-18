# SPDX-License-Identifier: AGPL-3.0-or-later
"""One-command bootstrap after `git clone`.

Creates `.venv` if needed, installs this package, then runs `lam`.
No need to activate the venv. Default command is `serve`.

    python start.py
    python start.py --host 0.0.0.0
    python start.py scan
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
MIN_PY = (3, 11)
LAM_COMMANDS = frozenset(
    {"scan", "report", "plan", "apply", "serve", "capabilities"}
)


def resolve_lam_argv(argv: list[str]) -> list[str]:
    """Map launcher args to `python -m lam …` args. Empty → serve."""
    if not argv:
        return ["serve"]
    head = argv[0]
    if head in LAM_COMMANDS or head in {"-h", "--help", "--version"}:
        return argv
    if head.startswith("-"):
        return ["serve", *argv]
    return argv


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def interpreter_works(exe: Path) -> bool:
    """True if *exe* exists and can actually start (not a stale py-launcher path)."""
    if not exe.is_file():
        return False
    try:
        result = subprocess.run(
            [str(exe), "-c", "import sys"],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def ensure_python_version() -> None:
    if sys.version_info < MIN_PY:
        need = ".".join(str(p) for p in MIN_PY)
        have = f"{sys.version_info.major}.{sys.version_info.minor}"
        raise SystemExit(
            f"Python {need}+ is required (found {have}).\n"
            "Install it from https://www.python.org/downloads/\n"
            "On Windows, check “Add python.exe to PATH”. "
            "On macOS: brew install python. On Debian/Ubuntu: sudo apt install python3 python3-venv."
        )


def run(cmd: list[str], **kwargs) -> None:
    subprocess.run(cmd, check=True, **kwargs)


def ensure_venv() -> Path:
    py = venv_python()
    if interpreter_works(py):
        return py
    if VENV.exists():
        print("Existing .venv is broken (missing Python); recreating …", file=sys.stderr)
        shutil.rmtree(VENV, ignore_errors=True)
    else:
        print("First run: creating .venv …", file=sys.stderr)
    run([sys.executable, "-m", "venv", str(VENV)])
    if not interpreter_works(py):
        raise SystemExit(f"venv was created but {py} does not run.")
    return py


def ensure_install(py: Path) -> None:
    print("Installing Local Asset Management into .venv …", file=sys.stderr)
    run([str(py), "-m", "pip", "install", "-e", str(ROOT), "-q"])


def main(argv: list[str] | None = None) -> None:
    ensure_python_version()
    lam_argv = resolve_lam_argv(list(sys.argv[1:] if argv is None else argv))
    py = ensure_venv()
    ensure_install(py)
    os.chdir(ROOT)
    try:
        code = subprocess.call([str(py), "-m", "lam", *lam_argv])
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        raise SystemExit(0) from None
    raise SystemExit(code)


if __name__ == "__main__":
    main()
