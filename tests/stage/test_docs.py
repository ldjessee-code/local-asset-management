# SPDX-License-Identifier: AGPL-3.0-or-later
"""AGENTS.md, README, and docs name the staging schemas."""

from __future__ import annotations

import json
from pathlib import Path

from lam.stage.job import require_document
from lam.schemas.registry import PLACE_MANIFEST_SCHEMAS, STAGE_JOB_SCHEMAS

REPO = Path(__file__).resolve().parents[2]
SCHEMA_IDS = ("lam-stage-job/v1", "lam-place-manifest/v1")


def test_docs_name_staging_schemas():
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    arch = (REPO / "docs" / "architecture.md").read_text(encoding="utf-8")
    doc = (REPO / "docs" / "staging.md").read_text(encoding="utf-8")
    for schema_id in SCHEMA_IDS:
        assert schema_id in agents
        assert schema_id in doc
    assert "docs/staging.md" in agents
    assert "Proof (staging)" in agents
    assert "lam stage" in readme
    assert "lam place" in readme
    assert "lam publish" in readme
    assert "lam.stage" in arch
    assert "100000000" in doc


def test_samples_match_schemas():
    job = json.loads((REPO / "examples" / "stage-job.sample.json").read_text(encoding="utf-8"))
    manifest = json.loads((REPO / "examples" / "place-manifest.sample.json").read_text(encoding="utf-8"))
    require_document(job, STAGE_JOB_SCHEMAS, "stage-job")
    require_document(manifest, PLACE_MANIFEST_SCHEMAS, "place-manifest")
