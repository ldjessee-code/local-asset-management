# SPDX-License-Identifier: AGPL-3.0-or-later
"""One JSONL file and one stderr FINAL line per run.

Stdout stays the machine-readable document the command already prints.
``LAM_LOG_LEVEL`` (debug, info, warning, error) sets the file floor. The
default floor is info.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}
_REDACT_NAMES = frozenset({"cookie", "authorization", "secret", "set-cookie", "header"})


@dataclass(frozen=True)
class RunLog:
    run_id: str
    command: str
    path: Path


def _floor() -> int:
    name = os.environ.get("LAM_LOG_LEVEL", "info").strip().lower()
    return _LEVELS.get(name, _LEVELS["info"])


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _redact(value: object) -> object:
    if isinstance(value, dict):
        cleaned: dict = {}
        for key, item in value.items():
            if str(key).casefold() in _REDACT_NAMES:
                cleaned[key] = "<redacted>"
            else:
                cleaned[key] = _redact(item)
        return cleaned
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str) and "session_id=" in value:
        return "<redacted>"
    return value


def start_run(command: str, log_dir: Path) -> RunLog:
    """Create ``{command}-{stamp}-{run_id}.jsonl`` and write the start line."""
    run_id = uuid.uuid4().hex[:12]
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{command}-{stamp}-{run_id}.jsonl"
    run = RunLog(run_id=run_id, command=command, path=path)
    log_event(run, "info", "start")
    return run


def log_event(run: RunLog, level: str, event: str, **fields: object) -> None:
    """Append one JSON object. Fields named like secrets are stored as ``<redacted>``."""
    name = str(level).lower()
    if _LEVELS.get(name, _LEVELS["info"]) < _floor():
        return
    payload: dict = {
        "ts": _now(),
        "level": name,
        "run_id": run.run_id,
        "command": run.command,
        "event": event,
    }
    payload.update(fields)
    line = json.dumps(_redact(payload), ensure_ascii=False, default=str)
    with run.path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()


def log_file_event(
    run: RunLog,
    level: str,
    action: str,
    path: Path | str,
    *,
    size: int | None = None,
    ok: bool | None = None,
    error: BaseException | None = None,
    func: str = "",
) -> None:
    """One ``event=file`` line. ``msg`` is ``Type: text`` with no traceback."""
    fields: dict = {"action": action, "path": str(path), "func": func}
    if size is not None:
        fields["size"] = int(size)
    if ok is not None:
        fields["ok"] = bool(ok)
    if error is not None:
        fields["ok"] = False
        fields["exc_type"] = type(error).__name__
        fields["msg"] = f"{type(error).__name__}: {error}"
    log_event(run, level, "file", **fields)


def finish_run(run: RunLog, *, ok: int, skipped: int, failed: int) -> str:
    """Append ``event=summary`` and print the one stderr FINAL line."""
    log_event(
        run,
        "info",
        "summary",
        ok=int(ok),
        skipped=int(skipped),
        failed=int(failed),
    )
    line = (
        f"FINAL run={run.run_id} command={run.command} "
        f"ok={int(ok)} skipped={int(skipped)} failed={int(failed)} "
        f"log={run.path.resolve()}"
    )
    print(line, file=sys.stderr)
    return line
