# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 20: AGENTS.md names every patreon schema and the doc."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AGENTS = REPO / "AGENTS.md"


def test_agents_lists_patreon_formats():
    text = AGENTS.read_text(encoding="utf-8")
    for schema_id in (
        "patreon-creators/v1",
        "patreon-drop-manifest/v1",
        "patreon-index/v1",
        "patreon-post-list/v1",
    ):
        assert schema_id in text
    assert "docs/patreon-sync.md" in text
    assert "lam/schemas/registry.py" in text
