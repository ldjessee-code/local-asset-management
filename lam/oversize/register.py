# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one oversize register: atomic write, one ``.prev`` copy."""

from __future__ import annotations

import csv
import json
import os
import uuid
from pathlib import Path

from lam.oversize.errors import OversizeError
from lam.schema_lite import validate_json
from lam.schemas.registry import (
    OVERSIZE_REGISTER_SCHEMA_ID,
    OVERSIZE_SCHEMAS,
    unsupported_message,
)

FLAG = "large, revisit for subdivision"
ENV_REGISTER = "LAM_OVERSIZE_REGISTER"

ENTRY_FIELDS = (
    "original_path",
    "sha256",
    "bytes",
    "mb",
    "width",
    "height",
    "megapixels",
    "combined",
    "combined_evidence",
    "parts_found",
    "part_paths",
    "parts_area_pct",
    "proxy_path",
    "proxy_width",
    "proxy_height",
    "zip_path",
    "zip_verified",
    "original_recycled",
    "flag",
    "status",
    "error",
    "first_seen",
    "updated",
    "paths_seen",
    "part_scope",
    "scale",
    "coverage_pct",
    "padding_px",
    "located_parts",
    "rejected_candidates",
    "duplicate_candidates",
    "missing_cells",
    "recreated_parts",
    "misnamed_parts",
    "confidence",
    "action",
)


def resolve_register_path(explicit: str | Path | None) -> Path:
    """``--register``, else ``LAM_OVERSIZE_REGISTER``, else ``%LOCALAPPDATA%\\lam\\...``."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_REGISTER)
    if env:
        return Path(env).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        local = str(Path.home() / "AppData" / "Local")
    return Path(local) / "lam" / "oversize-register.json"


def empty_register() -> dict:
    return {"schema": OVERSIZE_REGISTER_SCHEMA_ID, "entries": []}


def load_register(path: Path) -> dict:
    if not path.exists():
        return empty_register()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OversizeError(f"register is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise OversizeError(f"register must be a JSON object: {path}")
    schema_id = data.get("schema")
    entry = OVERSIZE_SCHEMAS.get(schema_id) if isinstance(schema_id, str) else None
    if entry is None or entry.status != "supported":
        raise OversizeError(unsupported_message("oversize-register", schema_id, OVERSIZE_SCHEMAS))
    errors = validate_json(data, entry.load_document())
    if errors:
        detail = "; ".join(errors[:8])
        raise OversizeError(f"register schema invalid: {detail}")
    return data


def save_register(path: Path, document: dict) -> None:
    """Write via a temp file. Keep the previous document as ``<path>.prev``."""
    schema = OVERSIZE_SCHEMAS[OVERSIZE_REGISTER_SCHEMA_ID].load_document()
    errors = validate_json(document, schema)
    if errors:
        detail = "; ".join(errors[:8])
        raise OversizeError(f"refusing to write an invalid register: {detail}")
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    prev_tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.prevtmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        if path.exists():
            prev_tmp.write_bytes(path.read_bytes())
            os.replace(prev_tmp, Path(str(path) + ".prev"))
        os.replace(tmp, path)
    except Exception:
        for item in (tmp, prev_tmp):
            if item.exists():
                try:
                    item.unlink()
                except OSError:
                    pass
        raise


def export_csv(path: Path, entries: list[dict]) -> None:
    if path.exists():
        raise OversizeError(f"refusing to overwrite existing CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ENTRY_FIELDS))
        writer.writeheader()
        for entry in entries:
            writer.writerow({key: _csv_value(entry.get(key)) for key in ENTRY_FIELDS})


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return json.dumps(value, ensure_ascii=False)
        return " | ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value
