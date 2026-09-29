# SPDX-License-Identifier: AGPL-3.0-or-later
"""Secrets file writer (temp + rename) and optional User env export."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from lam.token.errors import TokenUsageError, gate
from lam.token.secret import Secret

EnvSetter = Callable[[str, str], None]

# Tests inject a fake. The real Windows registry setter is never used in tests.
_env_setter: EnvSetter | None = None


def default_set_user_env(name: str, value: str) -> None:
    """Set a User environment variable on Windows (HKCU Environment)."""
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    HWND_BROADCAST = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    SMTO_ABORTIFHUNG = 0x0002
    ctypes.windll.user32.SendMessageTimeoutW(
        HWND_BROADCAST,
        WM_SETTINGCHANGE,
        0,
        "Environment",
        SMTO_ABORTIFHUNG,
        5000,
        None,
    )


def set_user_env_var(name: str, value: Secret) -> None:
    setter = _env_setter if _env_setter is not None else default_set_user_env
    setter(name, value.reveal())


def write_text_atomic(path: Path, content: str, *, replace_own: bool) -> None:
    """Write ``content`` via a temp file in the same directory, then rename.

    Replacing an existing file is allowed only when *replace_own* is true
    (the site's own secrets or meta file). An unrelated existing path is
    refused (exit 2).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace_own:
        raise TokenUsageError(gate("path", f"refusing to overwrite existing file: {path}"))
    line = content if content.endswith("\n") else content + "\n"
    tmp = path.with_name(f".lam-tmp-{uuid.uuid4().hex[:8]}-{path.name}")
    try:
        tmp.write_text(line, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def write_secrets_file(path: Path, header: Secret, *, replace_own: bool) -> None:
    write_text_atomic(path, header.reveal(), replace_own=replace_own)


def write_meta_file(path: Path, payload: dict[str, Any], *, replace_own: bool = True) -> None:
    write_text_atomic(path, json.dumps(payload, indent=2), replace_own=replace_own)


def read_meta_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def secrets_mtime_iso(path: Path) -> str | None:
    if not path.is_file():
        return None
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts).astimezone().replace(microsecond=0).isoformat()
