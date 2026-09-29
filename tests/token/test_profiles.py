# SPDX-License-Identifier: AGPL-3.0-or-later
"""Site-profiles schema: load, required fields, versioning, enabled, duplicates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lam.schemas.registry import SITE_PROFILES_SCHEMA_ID, SITE_PROFILES_SCHEMAS, SchemaEntry
from lam.token.errors import EXIT_USAGE, TokenUsageError
from lam.token.profiles import (
    builtin_profiles,
    load_profiles_document,
    require_site,
    status_sites,
    validate_profiles_document,
)

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "examples" / "site-profiles.sample.json"


def _doc(**overrides) -> dict:
    base = {
        "schema": SITE_PROFILES_SCHEMA_ID,
        "sites": [
            {
                "id": "patreon",
                "display_name": "Patreon",
                "login_url": "https://www.patreon.com/login",
                "cookie_domains": [".patreon.com"],
                "required_cookies": ["session_id"],
                "header_cookies": "all",
                "validate": {
                    "url": "https://www.patreon.com/api/current_user",
                    "expect_json_path": "data.id",
                    "expect_status": 200,
                },
                "env_var": "PATREON_SESSION_COOKIE",
                "enabled": True,
            }
        ],
    }
    base.update(overrides)
    return base


def test_sample_loads():
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert validate_profiles_document(data) == []
    profiles = load_profiles_document(data)
    ids = [site.id for site in profiles.sites]
    assert "patreon" in ids
    assert "example-wiki" in ids
    patreon = profiles.get("patreon")
    assert patreon.login_url.startswith("https://")
    assert ".patreon.com" in patreon.cookie_domains
    assert "session_id" in patreon.required_cookies
    assert patreon.header_cookies == "all"
    assert patreon.env_var == "PATREON_SESSION_COOKIE"


def test_builtin_patreon_needs_no_config():
    profiles = builtin_profiles()
    site = profiles.get("patreon")
    assert site.id == "patreon"
    assert site.enabled is True
    assert site.required_cookies == ("session_id",)


@pytest.mark.parametrize("field", ["id", "login_url", "cookie_domains"])
def test_missing_required_field_exit_2(field: str):
    site = {
        "id": "patreon",
        "display_name": "Patreon",
        "login_url": "https://www.patreon.com/login",
        "cookie_domains": [".patreon.com"],
        "required_cookies": ["session_id"],
    }
    del site[field]
    data = {"schema": SITE_PROFILES_SCHEMA_ID, "sites": [site]}
    errors = validate_profiles_document(data)
    assert errors
    assert any(field in err for err in errors)
    with pytest.raises(TokenUsageError) as caught:
        load_profiles_document(data)
    assert caught.value.exit_code == EXIT_USAGE
    assert field in str(caught.value)
    assert str(caught.value).startswith("GATE ")


def test_unknown_schema_id_rejected(monkeypatch: pytest.MonkeyPatch):
    data = _doc(schema="lam-site-profiles/v9")
    errors = validate_profiles_document(data)
    assert len(errors) == 1
    assert errors[0] == (
        "unsupported site-profiles schema lam-site-profiles/v9; "
        "supported: lam-site-profiles/v1"
    )
    with pytest.raises(TokenUsageError) as caught:
        load_profiles_document(data)
    assert caught.value.exit_code == EXIT_USAGE
    assert "lam-site-profiles/v9" in str(caught.value)
    assert "lam-site-profiles/v1" in str(caught.value)
    assert str(caught.value).startswith("GATE schema:")


def test_removed_schema_id_rejected(monkeypatch: pytest.MonkeyPatch):
    removed_id = "lam-site-profiles/v-removed"
    updated = dict(SITE_PROFILES_SCHEMAS)
    updated[removed_id] = SchemaEntry(
        schema_id=removed_id,
        kind="site-profiles",
        status="removed",
        removal_note="removed in 0.2",
    )
    monkeypatch.setattr("lam.schemas.registry.SITE_PROFILES_SCHEMAS", updated)
    monkeypatch.setattr("lam.token.profiles.SITE_PROFILES_SCHEMAS", updated)

    data = _doc(schema=removed_id)
    errors = validate_profiles_document(data)
    assert errors[0].startswith("unsupported site-profiles schema lam-site-profiles/v-removed")
    assert "supported: lam-site-profiles/v1" in errors[0]
    with pytest.raises(TokenUsageError) as caught:
        load_profiles_document(data)
    assert caught.value.exit_code == EXIT_USAGE


def test_deprecated_schema_id_warns_and_runs(monkeypatch: pytest.MonkeyPatch):
    import copy

    deprecated_id = "lam-site-profiles/deprecated-test"
    document = copy.deepcopy(SITE_PROFILES_SCHEMAS[SITE_PROFILES_SCHEMA_ID].load_document())
    document["properties"]["schema"] = {"const": deprecated_id}
    updated = dict(SITE_PROFILES_SCHEMAS)
    updated[deprecated_id] = SchemaEntry(
        schema_id=deprecated_id,
        kind="site-profiles",
        status="deprecated",
        removal_note="removed in 9.9 (test only)",
        document=document,
    )
    monkeypatch.setattr("lam.schemas.registry.SITE_PROFILES_SCHEMAS", updated)
    monkeypatch.setattr("lam.token.profiles.SITE_PROFILES_SCHEMAS", updated)

    data = _doc(schema=deprecated_id)
    profiles = load_profiles_document(data)
    assert profiles.sites[0].id == "patreon"
    assert profiles.warnings
    assert "deprecated" in profiles.warnings[0]
    assert "lam-site-profiles/deprecated-test" in profiles.warnings[0]


def test_disabled_site_skipped_by_status_refused_by_login_get():
    data = _doc()
    data["sites"][0]["enabled"] = False
    profiles = load_profiles_document(data)
    assert status_sites(profiles) == []
    with pytest.raises(TokenUsageError) as login_err:
        require_site(profiles, "patreon", action="login")
    assert login_err.value.exit_code == EXIT_USAGE
    assert "disabled" in str(login_err.value).lower()
    assert str(login_err.value).startswith("GATE ")
    with pytest.raises(TokenUsageError) as get_err:
        require_site(profiles, "patreon", action="get")
    assert get_err.value.exit_code == EXIT_USAGE
    assert "disabled" in str(get_err.value).lower()


def test_duplicate_site_ids_rejected():
    data = _doc()
    data["sites"] = [data["sites"][0], dict(data["sites"][0])]
    errors = validate_profiles_document(data)
    assert errors
    assert any("duplicate" in err.lower() for err in errors)
    with pytest.raises(TokenUsageError) as caught:
        load_profiles_document(data)
    assert caught.value.exit_code == EXIT_USAGE
    assert "duplicate" in str(caught.value).lower()
    assert "patreon" in str(caught.value)
