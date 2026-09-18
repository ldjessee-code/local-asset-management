# SPDX-License-Identifier: AGPL-3.0-or-later
"""One background job at a time for the web UI (scan/report/plan/apply)."""

from __future__ import annotations

import threading
import traceback
from collections import deque
from collections.abc import Callable
from typing import Any

from lam.util import utc_now


class JobBusy(RuntimeError):
    pass


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: dict[str, Any] = {"state": "idle", "command": None}
        self._log: deque[str] = deque(maxlen=250)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = dict(self._job)
            data["log"] = list(self._log)
            return data

    def start(self, command: str, fn: Callable[[], None]) -> dict[str, Any]:
        with self._lock:
            if self._job.get("state") == "running":
                raise JobBusy(f"already running {self._job.get('command')}")
            self._log.clear()
            self._job = {
                "state": "running",
                "command": command,
                "started": utc_now(),
                "finished": None,
                "error": None,
                "progress": {},
            }

        def runner() -> None:
            try:
                fn()
                with self._lock:
                    self._job["state"] = "done"
                    self._job["finished"] = utc_now()
            except Exception as exc:  # noqa: BLE001
                tb = traceback.format_exc()
                with self._lock:
                    self._job["state"] = "error"
                    self._job["finished"] = utc_now()
                    self._job["error"] = str(exc)
                    self._log.append(tb)

        threading.Thread(target=runner, name=f"lam-{command}", daemon=True).start()
        return self.snapshot()

    def progress(self, phase: str, payload: dict) -> None:
        line = human_progress(phase, payload)
        with self._lock:
            self._job["progress"] = {"phase": phase, **payload}
            self._log.append(line)


def human_progress(phase: str, payload: dict) -> str:
    """One English line for the UI/console. Payload stays on the job object."""
    data = payload or {}
    inner = data.get("phase")
    if phase == "scan":
        if "seen" in data:
            src = data.get("source")
            where = f" in {src}" if src else ""
            return f"Scanning{where}: {data['seen']} files so far"
        if inner == "zip":
            return f"Listing zip archives ({data.get('archives', 0)})"
        if inner == "hash":
            return "Looking for exact duplicates"
        return "Scanning sources"
    if phase == "hash":
        if inner == "prefix":
            return f"Checking file starts ({data.get('candidates', 0)} same-size files)"
        if inner == "full":
            return f"Hashing possible duplicates ({data.get('candidates', 0)} files)"
        return "Comparing file contents"
    if phase == "done":
        return (
            f"Scan finished: {data.get('files', 0)} files, "
            f"{data.get('duplicate_groups', 0)} duplicate groups"
        )
    if phase == "plan":
        return (
            f"Plan ready: {data.get('rows', 0)} files, "
            f"{data.get('quarantine', 0)} extras to quarantine"
        )
    if phase == "apply":
        total = data.get("total")
        done = data.get("done")
        if total:
            return f"Copying {done} of {total}"
        copied = data.get("copied", 0)
        return f"Copy finished: {copied} copied, {data.get('quarantined', 0)} quarantined"
    if phase == "report":
        return "Wrote the summary report"
    bits = [f"{k} {v}" for k, v in data.items() if k != "phase"]
    return f"{phase}: " + ", ".join(bits) if bits else phase
