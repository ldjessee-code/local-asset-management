# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registry of file-action plan and results schema ids.

A new version is a new schema file plus an entry here. An id stays
``supported`` until it is marked ``deprecated`` (with a removal note), then
``removed`` in a later release. ``lam.actions`` dispatches validation on the
document's ``schema`` field. Unknown and removed ids fail before anything runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent

PLAN_SCHEMA_ID = "file-action-plan/v1"
RESULTS_SCHEMA_ID = "file-action-results/v1"

STATUS_SUPPORTED = "supported"
STATUS_DEPRECATED = "deprecated"
STATUS_REMOVED = "removed"


@dataclass(frozen=True)
class SchemaEntry:
    """One schema id. ``document`` overrides the file for tests only."""

    schema_id: str
    kind: str
    status: str
    filename: str | None = None
    removal_note: str | None = None
    document: dict[str, Any] | None = None

    def load_document(self) -> dict[str, Any]:
        if self.document is not None:
            return self.document
        if self.status == STATUS_REMOVED or not self.filename:
            raise ValueError(f"schema {self.schema_id} has no document ({self.status})")
        path = SCHEMA_DIR / self.filename
        return json.loads(path.read_text(encoding="utf-8"))


PLAN_SCHEMAS: dict[str, SchemaEntry] = {
    PLAN_SCHEMA_ID: SchemaEntry(
        schema_id=PLAN_SCHEMA_ID,
        kind="plan",
        status=STATUS_SUPPORTED,
        filename="file-action-plan.v1.schema.json",
    ),
}

RESULTS_SCHEMAS: dict[str, SchemaEntry] = {
    RESULTS_SCHEMA_ID: SchemaEntry(
        schema_id=RESULTS_SCHEMA_ID,
        kind="results",
        status=STATUS_SUPPORTED,
        filename="file-action-results.v1.schema.json",
    ),
}


def ids_with_status(table: dict[str, SchemaEntry], status: str) -> list[str]:
    return [entry.schema_id for entry in table.values() if entry.status == status]


def status_groups(table: dict[str, SchemaEntry]) -> dict[str, list[str]]:
    """Supported and deprecated ids, in registry order. Removed ids are omitted."""
    return {
        "supported": ids_with_status(table, STATUS_SUPPORTED),
        "deprecated": ids_with_status(table, STATUS_DEPRECATED),
    }


def unsupported_message(kind: str, schema_id: object, table: dict[str, SchemaEntry]) -> str:
    """Exact failure text for an unknown or removed schema id."""
    label = "plan" if kind == "plan" else "results"
    shown = schema_id if isinstance(schema_id, str) else repr(schema_id)
    supported = ", ".join(ids_with_status(table, STATUS_SUPPORTED)) or "(none)"
    message = f"unsupported {label} schema {shown}; supported: {supported}"
    deprecated = ids_with_status(table, STATUS_DEPRECATED)
    if deprecated:
        message += "; deprecated: " + ", ".join(deprecated)
    return message


def deprecation_warning(entry: SchemaEntry) -> str:
    note = entry.removal_note or "it will be removed in a later release"
    label = "plan" if entry.kind == "plan" else "results"
    return f"{label} schema {entry.schema_id} is deprecated; {note}"
