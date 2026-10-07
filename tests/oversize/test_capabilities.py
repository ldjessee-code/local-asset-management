# SPDX-License-Identifier: AGPL-3.0-or-later
"""lam-oversize-register/v1 shows up in the registry and in lam capabilities."""

from __future__ import annotations

import json

from lam.capabilities import describe_capabilities, format_capabilities_text
from lam.cli import NO_CONFIG_CMDS, main
from lam.schemas import registry as reg


SCHEMA_ID = "lam-oversize-register/v1"


def test_capabilities_lists_oversize_and_schema(capsys):
    entry = reg.OVERSIZE_SCHEMAS[SCHEMA_ID]
    assert entry.status == "supported"
    assert entry.load_document()["title"] == SCHEMA_ID
    cap = describe_capabilities()
    blob = json.dumps(cap)
    assert SCHEMA_ID in blob
    assert "oversize" in [cmd["name"] for cmd in cap["commands"]]
    assert "oversize" in NO_CONFIG_CMDS
    text = format_capabilities_text(cap)
    assert SCHEMA_ID in text
    main(["capabilities", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert SCHEMA_ID in json.dumps(payload)
