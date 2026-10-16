# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ring PASS or FAIL through court-run. Tests pass ``run`` and never start pwsh."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

DEFAULT_SCRIPT = Path(r"C:\Users\DougJ\Documents\GitHub\court-local-stack\scripts\court-run.ps1")
Runner = Callable[..., object]


def ring(
    status: str,
    *,
    name: str,
    requester: str,
    script: Path | None = None,
    run: Runner = subprocess.run,
) -> None:
    """Ask court-run to write a FINAL file. PASS exits 0. FAIL exits 1.

    Production does not pass ``-JobsRoot`` or ``-RunsDir``. The live watcher
    reads the default jobs tree. A failed ring is the caller's to log.
    """
    if status not in {"PASS", "FAIL"}:
        raise ValueError(f"doorbell status must be PASS or FAIL, got {status!r}")
    script_path = script
    if script_path is None:
        raw = os.environ.get("LAM_COURT_RUN")
        script_path = Path(raw) if raw else DEFAULT_SCRIPT
    command = "exit 0" if status == "PASS" else "exit 1"
    argv = [
        "pwsh",
        "-NoProfile",
        "-File",
        str(script_path),
        "-Name",
        name,
        "-Requester",
        requester,
        "--",
        "pwsh",
        "-NoProfile",
        "-Command",
        command,
    ]
    completed = run(argv, check=False)
    code = getattr(completed, "returncode", 0)
    if code not in (0, None):
        raise RuntimeError(f"court-run exit {code}")
