# SPDX-License-Identifier: AGPL-3.0-or-later
"""Create and load ``lam-stage-job/v1`` documents."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from lam.schema_lite import validate_json
from lam.schemas.registry import (
    STATUS_DEPRECATED,
    STATUS_SUPPORTED,
    STAGE_JOB_SCHEMAS,
    SchemaEntry,
    unsupported_message,
)
from lam.stage.errors import StageError
from lam.stage.paths import MAP_BYTE_CAP, assert_staging_root_allowed, default_staging_root, safe_segment

KINDS = ("generic", "game-book", "fiction", "review", "image", "map")
JOB_KIND = "stage-job"


def iso_stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return moment.replace(microsecond=0).isoformat()


def require_document(data: Any, table: dict[str, SchemaEntry], kind: str) -> SchemaEntry:
    schema_id = data.get("schema") if isinstance(data, dict) else None
    entry = table.get(schema_id) if isinstance(schema_id, str) else None
    if entry is None or entry.status not in {STATUS_SUPPORTED, STATUS_DEPRECATED}:
        raise StageError(unsupported_message(kind, schema_id, table))
    errors = validate_json(data, entry.load_document())
    if errors:
        raise StageError(f"invalid {kind} document:\n  " + "\n  ".join(errors))
    return entry


def load_job(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise StageError(f"job.json not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise StageError(f"not valid JSON: {path}: {exc}") from exc
    require_document(data, STAGE_JOB_SCHEMAS, JOB_KIND)
    return data


def save_job(path: Path, document: dict[str, Any]) -> None:
    require_document(document, STAGE_JOB_SCHEMAS, JOB_KIND)
    path = Path(path)
    text = json.dumps(document, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def create_job(
    *,
    owner: str,
    job: str,
    target: str | Path,
    root: str | Path | None = None,
    plan: str | None = None,
    kind: str = "generic",
    byte_cap: int | None = None,
    now: datetime | None = None,
) -> Path:
    """Create ``<root>/<owner>/<job>_yyyyMMdd_hhmm/{in,work,out,logs}`` and ``job.json``."""
    if kind not in KINDS:
        raise StageError(f"unknown kind {kind}")
    if byte_cap is not None and (not isinstance(byte_cap, int) or isinstance(byte_cap, bool) or byte_cap < 1):
        raise StageError("byte-cap must be at least 1")
    owner = safe_segment(owner, "owner")
    job = safe_segment(job, "job")
    staging = assert_staging_root_allowed(Path(root) if root is not None else default_staging_root())
    moment = now or datetime.now().astimezone()
    stamp = moment.strftime("%Y%m%d_%H%M")
    folder = staging / owner / f"{job}_{stamp}"
    number = 2
    while folder.exists():
        folder = staging / owner / f"{job}_{stamp}_{number}"
        number += 1
        if number > 1000:
            raise StageError(f"no free job folder near {staging / owner}")
    for name in ("in", "work", "out", "logs"):
        (folder / name).mkdir(parents=True)
    stored_cap: int | None
    if byte_cap is not None:
        stored_cap = byte_cap
    elif kind == "map":
        stored_cap = MAP_BYTE_CAP
    else:
        stored_cap = None
    document: dict[str, Any] = {
        "schema": "lam-stage-job/v1",
        "owner": owner,
        "job": job,
        "target": str(target),
        "plan": plan,
        "status": "new",
        "created": iso_stamp(moment),
        "kind": kind,
        "placed": None,
        "stale": False,
        "cleaned": None,
        "byte_cap": stored_cap,
        "job_dir": str(folder),
        "staging_root": str(staging),
    }
    save_job(folder / "job.json", document)
    return folder
