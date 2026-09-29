# SPDX-License-Identifier: AGPL-3.0-or-later
"""AGENTS.md and the layered-sorting doc name the new schema ids."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_docs_name_tag_schemas():
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    arch = (REPO / "docs" / "architecture.md").read_text(encoding="utf-8")
    doc = (REPO / "docs" / "layered-sorting.md").read_text(encoding="utf-8")
    for schema_id in (
        "lam-tags/v1",
        "lam-subbins/v1",
        "lam-tag-sidecar/v1",
        "lam-tag-review/v1",
    ):
        assert schema_id in agents
        assert schema_id in doc
    assert "docs/layered-sorting.md" in agents
    assert "lam tag" in readme
    assert "lam.tag" in arch
    sample = json_text(REPO / "examples" / "subbins.sample.json")
    assert "lam-subbins/v1" in sample
    assert "SubA" in sample and "SubB" in sample


def json_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")
