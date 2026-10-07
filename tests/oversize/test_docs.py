# SPDX-License-Identifier: AGPL-3.0-or-later
"""AGENTS.md and the oversize doc name the register schema."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCHEMA_ID = "lam-oversize-register/v1"


def test_docs_name_oversize_schema():
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    arch = (REPO / "docs" / "architecture.md").read_text(encoding="utf-8")
    doc = (REPO / "docs" / "oversize.md").read_text(encoding="utf-8")
    assert SCHEMA_ID in agents
    assert SCHEMA_ID in doc
    assert "docs/oversize.md" in agents
    assert "Proof (oversize)" in agents
    assert "lam oversize" in readme
    assert "lam.oversize" in arch
