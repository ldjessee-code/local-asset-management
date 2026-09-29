# SPDX-License-Identifier: AGPL-3.0-or-later
"""New schema ids show up in the registry and in lam capabilities."""

from __future__ import annotations

import json

from lam.capabilities import describe_capabilities, format_capabilities_text
from lam.cli import NO_CONFIG_CMDS, main
from lam.schemas import registry as reg


IDS = (
    "lam-tags/v1",
    "lam-subbins/v1",
    "lam-tag-sidecar/v1",
    "lam-tag-review/v1",
)


def test_registry_and_capabilities_list_tag_schemas(capsys):
    tables = {
        "lam-tags/v1": reg.TAGS_SCHEMAS,
        "lam-subbins/v1": reg.SUBBINS_SCHEMAS,
        "lam-tag-sidecar/v1": reg.TAG_SIDECAR_SCHEMAS,
        "lam-tag-review/v1": reg.TAG_REVIEW_SCHEMAS,
    }
    for schema_id, table in tables.items():
        entry = table[schema_id]
        assert entry.status == "supported"
        assert entry.load_document()["title"] == schema_id
    cap = describe_capabilities()
    blob = json.dumps(cap)
    for schema_id in IDS:
        assert schema_id in blob
    assert "tag" in [cmd["name"] for cmd in cap["commands"]]
    assert "tag" in NO_CONFIG_CMDS
    text = format_capabilities_text(cap)
    for schema_id in IDS:
        assert schema_id in text
    main(["capabilities", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert "lam-tags/v1" in json.dumps(payload)
