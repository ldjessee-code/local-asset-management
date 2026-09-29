# SPDX-License-Identifier: AGPL-3.0-or-later
"""lam-site-profiles/v1 is in the registry and in lam capabilities."""

from __future__ import annotations

import json

from lam.capabilities import describe_capabilities, format_capabilities_text
from lam.cli import main
from lam.schemas.registry import SITE_PROFILES_SCHEMA_ID, SITE_PROFILES_SCHEMAS, STATUS_SUPPORTED


def test_registry_lists_site_profiles_v1():
    entry = SITE_PROFILES_SCHEMAS[SITE_PROFILES_SCHEMA_ID]
    assert entry.status == STATUS_SUPPORTED
    assert entry.kind == "site-profiles"
    assert entry.filename == "site-profiles.v1.schema.json"
    assert entry.load_document()["title"] == SITE_PROFILES_SCHEMA_ID


def test_capabilities_json_lists_site_profiles():
    cap = describe_capabilities()
    token = cap["token"]
    assert token["schema"] == SITE_PROFILES_SCHEMA_ID
    assert token["schema_ids"]["supported"] == [SITE_PROFILES_SCHEMA_ID]
    assert token["schema_ids"]["deprecated"] == []
    names = [cmd["name"] for cmd in cap["commands"]]
    assert "token" in names
    modules = [m["module"] for m in cap["modules"]]
    assert "lam.token" in modules
    text = format_capabilities_text(cap)
    assert "lam-site-profiles/v1" in text
    assert "Site-profile schemas:" in text


def test_cli_capabilities_json_and_text(capsys):
    main(["capabilities"])
    out = capsys.readouterr().out
    assert "lam-site-profiles/v1" in out
    assert "lam token" in out
    main(["capabilities", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["token"]["schema"] == "lam-site-profiles/v1"
    assert "lam-site-profiles/v1" in json.dumps(payload)
