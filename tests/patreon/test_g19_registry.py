# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 19: schema registry and lam capabilities."""

from __future__ import annotations

import json

from lam.capabilities import describe_capabilities, format_capabilities_text
from lam.cli import main
from lam.schemas import registry as reg

IDS = (
    "patreon-creators/v1",
    "patreon-drop-manifest/v1",
    "patreon-index/v1",
    "patreon-post-list/v1",
)


def test_registry_entries_supported():
    tables = {
        "patreon-creators/v1": reg.PATREON_CREATORS_SCHEMAS,
        "patreon-drop-manifest/v1": reg.PATREON_MANIFEST_SCHEMAS,
        "patreon-index/v1": reg.PATREON_INDEX_SCHEMAS,
        "patreon-post-list/v1": reg.PATREON_POST_LIST_SCHEMAS,
    }
    for schema_id, table in tables.items():
        entry = table[schema_id]
        assert entry.status == "supported"
        assert entry.load_document()["title"] == schema_id
    assert reg.SITE_PROFILES_SCHEMAS[reg.SITE_PROFILES_SCHEMA_ID].status == "supported"


def test_capabilities_json_and_text(capsys):
    cap = describe_capabilities()
    blob = json.dumps(cap)
    for schema_id in IDS:
        assert schema_id in blob
    assert "lam-site-profiles/v1" in blob
    names = [cmd["name"] for cmd in cap["commands"]]
    assert "patreon" in names
    text = format_capabilities_text(cap)
    for schema_id in IDS:
        assert schema_id in text
    main(["capabilities"])
    plain = capsys.readouterr().out
    assert "patreon-creators/v1" in plain
    main(["capabilities", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert "patreon-post-list/v1" in json.dumps(payload)
