# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSON load/store for tag documents. Temp file, then rename. No clobber."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from lam.schema_lite import validate_json
from lam.schemas.registry import (
    STATUS_DEPRECATED,
    STATUS_SUPPORTED,
    SchemaEntry,
    unsupported_message,
)
from lam.tag.errors import TagError


def local_iso_now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def mtime_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().replace(microsecond=0).isoformat()


def load_json_file(path: Path) -> Any:
    path = Path(path)
    if not path.is_file():
        raise TagError(f"file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise TagError(f"not valid JSON: {path}: {exc}") from exc
    except OSError as exc:
        raise TagError(str(exc)) from exc


def require_known_schema(data: Any, table: dict[str, SchemaEntry], kind: str) -> SchemaEntry:
    schema_id = data.get("schema") if isinstance(data, dict) else None
    entry = table.get(schema_id) if isinstance(schema_id, str) else None
    if entry is None or entry.status not in {STATUS_SUPPORTED, STATUS_DEPRECATED}:
        raise TagError(unsupported_message(kind, schema_id, table))
    errors = validate_json(data, entry.load_document())
    if errors:
        raise TagError(f"invalid {kind} document:\n  " + "\n  ".join(errors))
    if entry.status == STATUS_DEPRECATED:
        return entry
    return entry


def write_json_no_clobber(path: Path, payload: dict) -> None:
    """Write JSON via a temp file in the same directory.

    An existing file with identical text is left in place. Different bytes
    are refused. ``os.rename`` does not replace an existing destination.
    """
    path = Path(path)
    text = json.dumps(payload, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TagError(str(exc)) from exc
        if existing == text:
            return
        raise TagError(f"refusing to overwrite existing different file: {path}")
    tmp = path.with_name(f".lam-tmp-{uuid.uuid4().hex[:8]}-{path.name}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.rename(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
