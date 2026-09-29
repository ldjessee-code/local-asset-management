# SPDX-License-Identifier: AGPL-3.0-or-later
"""AGENTS.md lists the site-profiles schema and the token-fetcher doc."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_agents_lists_site_profiles_and_token_doc():
    text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "lam-site-profiles/v1" in text
    assert "docs/token-fetcher.md" in text
