# SPDX-License-Identifier: AGPL-3.0-or-later
"""Staging schema ids show up in the registry and in lam capabilities."""

from __future__ import annotations

import json

from lam.capabilities import describe_capabilities, format_capabilities_text
from lam.cli import NO_CONFIG_CMDS, main
from lam.schemas import registry as reg

SCHEMA_IDS = ("lam-stage-job/v1", "lam-place-manifest/v1")


def test_capabilities_lists_staging_schemas(capsys):
    assert reg.STAGE_JOB_SCHEMAS[SCHEMA_IDS[0]].status == "supported"
    assert reg.PLACE_MANIFEST_SCHEMAS[SCHEMA_IDS[1]].status == "supported"
    assert reg.STAGE_JOB_SCHEMAS[SCHEMA_IDS[0]].load_document()["title"] == SCHEMA_IDS[0]
    assert reg.PLACE_MANIFEST_SCHEMAS[SCHEMA_IDS[1]].load_document()["title"] == SCHEMA_IDS[1]
    cap = describe_capabilities()
    blob = json.dumps(cap)
    names = [cmd["name"] for cmd in cap["commands"]]
    for schema_id in SCHEMA_IDS:
        assert schema_id in blob
        assert schema_id in format_capabilities_text(cap)
    for name in ("stage", "place", "publish"):
        assert name in names
        assert name in NO_CONFIG_CMDS
    main(["capabilities", "--json"])
    payload = json.loads(capsys.readouterr().out)
    encoded = json.dumps(payload)
    for schema_id in SCHEMA_IDS:
        assert schema_id in encoded
    assert cap["staging"]["map_byte_cap"] == 100000000
