# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registry of versioned JSON schema ids.

A new version is a new schema file plus an entry here. An id stays
``supported`` until it is marked ``deprecated`` (with a removal note), then
``removed`` in a later release. Dispatchers validate on the document's
``schema`` field. Unknown and removed ids fail before anything runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent

PLAN_SCHEMA_ID = "file-action-plan/v1"
RESULTS_SCHEMA_ID = "file-action-results/v1"
SITE_PROFILES_SCHEMA_ID = "lam-site-profiles/v1"
PATREON_CREATORS_SCHEMA_ID = "patreon-creators/v1"
PATREON_MANIFEST_SCHEMA_ID = "patreon-drop-manifest/v1"
PATREON_INDEX_SCHEMA_ID = "patreon-index/v1"
PATREON_POST_LIST_SCHEMA_ID = "patreon-post-list/v1"
TAGS_SCHEMA_ID = "lam-tags/v1"
SUBBINS_SCHEMA_ID = "lam-subbins/v1"
TAG_SIDECAR_SCHEMA_ID = "lam-tag-sidecar/v1"
TAG_REVIEW_SCHEMA_ID = "lam-tag-review/v1"
OVERSIZE_REGISTER_SCHEMA_ID = "lam-oversize-register/v1"

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

SITE_PROFILES_SCHEMAS: dict[str, SchemaEntry] = {
    SITE_PROFILES_SCHEMA_ID: SchemaEntry(
        schema_id=SITE_PROFILES_SCHEMA_ID,
        kind="site-profiles",
        status=STATUS_SUPPORTED,
        filename="site-profiles.v1.schema.json",
    ),
}

PATREON_CREATORS_SCHEMAS: dict[str, SchemaEntry] = {
    PATREON_CREATORS_SCHEMA_ID: SchemaEntry(
        schema_id=PATREON_CREATORS_SCHEMA_ID,
        kind="patreon-creators",
        status=STATUS_SUPPORTED,
        filename="patreon-creators.v1.schema.json",
    ),
}

PATREON_MANIFEST_SCHEMAS: dict[str, SchemaEntry] = {
    PATREON_MANIFEST_SCHEMA_ID: SchemaEntry(
        schema_id=PATREON_MANIFEST_SCHEMA_ID,
        kind="patreon-drop-manifest",
        status=STATUS_SUPPORTED,
        filename="patreon-drop-manifest.v1.schema.json",
    ),
}

PATREON_INDEX_SCHEMAS: dict[str, SchemaEntry] = {
    PATREON_INDEX_SCHEMA_ID: SchemaEntry(
        schema_id=PATREON_INDEX_SCHEMA_ID,
        kind="patreon-index",
        status=STATUS_SUPPORTED,
        filename="patreon-index.v1.schema.json",
    ),
}

PATREON_POST_LIST_SCHEMAS: dict[str, SchemaEntry] = {
    PATREON_POST_LIST_SCHEMA_ID: SchemaEntry(
        schema_id=PATREON_POST_LIST_SCHEMA_ID,
        kind="patreon-post-list",
        status=STATUS_SUPPORTED,
        filename="patreon-post-list.v1.schema.json",
    ),
}

TAGS_SCHEMAS: dict[str, SchemaEntry] = {
    TAGS_SCHEMA_ID: SchemaEntry(
        schema_id=TAGS_SCHEMA_ID,
        kind="tags",
        status=STATUS_SUPPORTED,
        filename="lam-tags.v1.schema.json",
    ),
}

SUBBINS_SCHEMAS: dict[str, SchemaEntry] = {
    SUBBINS_SCHEMA_ID: SchemaEntry(
        schema_id=SUBBINS_SCHEMA_ID,
        kind="subbins",
        status=STATUS_SUPPORTED,
        filename="lam-subbins.v1.schema.json",
    ),
}

TAG_SIDECAR_SCHEMAS: dict[str, SchemaEntry] = {
    TAG_SIDECAR_SCHEMA_ID: SchemaEntry(
        schema_id=TAG_SIDECAR_SCHEMA_ID,
        kind="tag-sidecar",
        status=STATUS_SUPPORTED,
        filename="lam-tag-sidecar.v1.schema.json",
    ),
}

TAG_REVIEW_SCHEMAS: dict[str, SchemaEntry] = {
    TAG_REVIEW_SCHEMA_ID: SchemaEntry(
        schema_id=TAG_REVIEW_SCHEMA_ID,
        kind="tag-review",
        status=STATUS_SUPPORTED,
        filename="lam-tag-review.v1.schema.json",
    ),
}

OVERSIZE_SCHEMAS: dict[str, SchemaEntry] = {
    OVERSIZE_REGISTER_SCHEMA_ID: SchemaEntry(
        schema_id=OVERSIZE_REGISTER_SCHEMA_ID,
        kind="oversize-register",
        status=STATUS_SUPPORTED,
        filename="lam-oversize-register.v1.schema.json",
    ),
}

KIND_LABELS = {
    "plan": "plan",
    "results": "results",
    "site-profiles": "site-profiles",
    "patreon-creators": "patreon-creators",
    "patreon-drop-manifest": "patreon-drop-manifest",
    "patreon-index": "patreon-index",
    "patreon-post-list": "patreon-post-list",
    "tags": "tags",
    "subbins": "subbins",
    "tag-sidecar": "tag-sidecar",
    "tag-review": "tag-review",
    "oversize-register": "oversize-register",
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
    label = KIND_LABELS.get(kind, kind)
    shown = schema_id if isinstance(schema_id, str) else repr(schema_id)
    supported = ", ".join(ids_with_status(table, STATUS_SUPPORTED)) or "(none)"
    message = f"unsupported {label} schema {shown}; supported: {supported}"
    deprecated = ids_with_status(table, STATUS_DEPRECATED)
    if deprecated:
        message += "; deprecated: " + ", ".join(deprecated)
    return message


def deprecation_warning(entry: SchemaEntry) -> str:
    note = entry.removal_note or "it will be removed in a later release"
    label = KIND_LABELS.get(entry.kind, entry.kind)
    return f"{label} schema {entry.schema_id} is deprecated; {note}"
