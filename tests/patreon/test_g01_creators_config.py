# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 1: patreon-creators/v1 schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lam.patreon.config import load_creators_document, validate_creators_document
from lam.patreon.errors import EXIT_USAGE, PatreonUsageError
from lam.schemas.registry import PATREON_CREATORS_SCHEMA_ID, PATREON_CREATORS_SCHEMAS, SchemaEntry
from tests.patreon.conftest import invoke

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "examples" / "patreon-creators.sample.json"


def _doc() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_sample_loads():
    data = _doc()
    assert validate_creators_document(data) == []
    loaded = load_creators_document(data)
    assert loaded.schema == "patreon-creators/v1"
    slugs = [item.slug for item in loaded.creators]
    assert slugs == ["Kidney_Boy", "Party_of_Two"]
    assert loaded.staging_root == r"C:\Example\PatreonDL"
    kidney = loaded.creators[0]
    assert kidney.patreon_url == "https://www.patreon.com/kidneyboy"
    assert kidney.vanity == "kidneyboy"
    assert any("chronos" in pattern for pattern in kidney.filters.exclude_title_regex)


@pytest.mark.parametrize("field", ["schema", "creators"])
def test_missing_top_level_field(field: str):
    data = _doc()
    del data[field]
    errors = validate_creators_document(data)
    assert errors
    assert any(field in err for err in errors)
    with pytest.raises(PatreonUsageError) as caught:
        load_creators_document(data)
    assert caught.value.exit_code == EXIT_USAGE
    assert field in str(caught.value)


@pytest.mark.parametrize("field", ["slug", "patreon_url"])
def test_missing_creator_field(field: str):
    data = _doc()
    del data["creators"][0][field]
    errors = validate_creators_document(data)
    assert any(field in err for err in errors)
    with pytest.raises(PatreonUsageError) as caught:
        load_creators_document(data)
    assert caught.value.exit_code == EXIT_USAGE
    assert field in str(caught.value)


def test_unknown_schema_lists_supported():
    data = _doc()
    data["schema"] = "patreon-creators/v9"
    errors = validate_creators_document(data)
    assert len(errors) == 1
    assert "unsupported patreon-creators schema patreon-creators/v9" in errors[0]
    assert "supported: patreon-creators/v1" in errors[0]
    with pytest.raises(PatreonUsageError) as caught:
        load_creators_document(data)
    assert caught.value.exit_code == EXIT_USAGE


def test_removed_schema_rejected(monkeypatch: pytest.MonkeyPatch):
    removed_id = "patreon-creators/v-removed"
    updated = dict(PATREON_CREATORS_SCHEMAS)
    updated[removed_id] = SchemaEntry(
        schema_id=removed_id,
        kind="patreon-creators",
        status="removed",
        removal_note="removed in test",
    )
    monkeypatch.setattr("lam.schemas.registry.PATREON_CREATORS_SCHEMAS", updated)
    monkeypatch.setattr("lam.patreon.config.PATREON_CREATORS_SCHEMAS", updated)
    data = _doc()
    data["schema"] = removed_id
    errors = validate_creators_document(data)
    assert "unsupported patreon-creators schema patreon-creators/v-removed" in errors[0]
    assert "supported: patreon-creators/v1" in errors[0]
    with pytest.raises(PatreonUsageError) as caught:
        load_creators_document(data)
    assert caught.value.exit_code == EXIT_USAGE


def test_deprecated_schema_warns_on_stderr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys):
    import copy

    deprecated_id = "patreon-creators/deprecated-test"
    document = copy.deepcopy(PATREON_CREATORS_SCHEMAS[PATREON_CREATORS_SCHEMA_ID].load_document())
    document["properties"]["schema"] = {"const": deprecated_id}
    updated = dict(PATREON_CREATORS_SCHEMAS)
    updated[deprecated_id] = SchemaEntry(
        schema_id=deprecated_id,
        kind="patreon-creators",
        status="deprecated",
        removal_note="removed in 9.9 (test only)",
        document=document,
    )
    monkeypatch.setattr("lam.schemas.registry.PATREON_CREATORS_SCHEMAS", updated)
    monkeypatch.setattr("lam.patreon.config.PATREON_CREATORS_SCHEMAS", updated)
    data = _doc()
    data["schema"] = deprecated_id
    loaded = load_creators_document(data)
    assert loaded.creators[0].slug == "Kidney_Boy"
    assert loaded.warnings
    assert "deprecated" in loaded.warnings[0]
    path = tmp_path / "creators.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    code = invoke(["patreon", "validate-config", str(path)])
    assert code == 0
    err = capsys.readouterr().err
    assert "deprecated" in err
    assert deprecated_id in err


def test_disabled_creator_skipped(tmp_path: Path, capsys):
    from tests.patreon.builders import attachment, page, post, write_config, write_fixture

    root = tmp_path / "fx"
    resource, included = post(
        "p1",
        "Visible",
        "2026-06-01T15:00:00+00:00",
        attachments=[attachment("a1", "map.zip")],
    )
    write_fixture(root, "kidneyboy", [page([(resource, included)])])
    cfg = write_config(
        tmp_path / "creators.json",
        [
            {
                "slug": "Kidney_Boy",
                "display_name": "Kidney Boy",
                "patreon_url": "https://www.patreon.com/kidneyboy",
                "campaign_id": None,
                "enabled": False,
            }
        ],
        staging_root=str(tmp_path / "stage"),
    )
    code = invoke(
        [
            "patreon",
            "list",
            "--creators",
            str(cfg),
            "--fixture-dir",
            str(root),
            "--since",
            "2025-09-28",
            "--until",
            "2026-09-29",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Kidney_Boy: skipped (enabled: false)" in out
    assert "Harbor" not in out
    assert "Visible" not in out


def test_duplicate_slugs_rejected():
    data = _doc()
    data["creators"].append(dict(data["creators"][0]))
    errors = validate_creators_document(data)
    assert any("duplicate" in err.lower() and "Kidney_Boy" in err for err in errors)
    with pytest.raises(PatreonUsageError) as caught:
        load_creators_document(data)
    assert caught.value.exit_code == EXIT_USAGE


@pytest.mark.parametrize("slug", ["bad slug", "../no", "CON", "COM1", "name.", "a/b"])
def test_unsafe_slug_rejected(slug: str):
    data = _doc()
    data["creators"][0]["slug"] = slug
    errors = validate_creators_document(data)
    assert errors
    assert any(slug in err for err in errors)
    with pytest.raises(PatreonUsageError) as caught:
        load_creators_document(data)
    assert caught.value.exit_code == EXIT_USAGE


def test_bad_regex_rejected():
    data = _doc()
    data["creators"][0]["filters"]["exclude_title_regex"] = ["("]
    errors = validate_creators_document(data)
    assert any("regex" in err.lower() for err in errors)
    with pytest.raises(PatreonUsageError) as caught:
        load_creators_document(data)
    assert caught.value.exit_code == EXIT_USAGE


def test_default_chronos_regex_when_filters_omitted():
    data = _doc()
    data["creators"][0].pop("filters")
    loaded = load_creators_document(data)
    patterns = loaded.creators[0].filters.exclude_title_regex
    assert any("chronos" in item for item in patterns)
