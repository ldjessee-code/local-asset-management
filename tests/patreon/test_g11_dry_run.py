# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 11: sync dry-run writes nothing under staging."""

from __future__ import annotations

import json
from pathlib import Path

from tests.patreon.builders import attachment, creator, page, post, write_config, write_fixture
from tests.patreon.conftest import invoke


def test_dry_run_leaves_staging_untouched(tmp_path: Path):
    root = tmp_path / "fx"
    write_fixture(
        root,
        "kidneyboy",
        [page([post("p1", "Harbor", "2026-06-01T15:00:00+00:00", attachments=[attachment("a1", "map.zip")])])],
    )
    stage = tmp_path / "stage"
    before = []
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy")],
        staging_root=str(stage),
    )
    results = tmp_path / "dry.json"
    code = invoke(
        [
            "patreon",
            "sync",
            "--creators",
            str(cfg),
            "--fixture-dir",
            str(root),
            "--since",
            "2025-01-01",
            "--results",
            str(results),
        ]
    )
    assert code == 0
    assert list(stage.rglob("*")) == before
    assert not stage.exists()
    doc = json.loads(results.read_text(encoding="utf-8"))
    assert doc["mode"] == "dry-run"
    assert doc["posts"][0]["post_title"] == "Harbor"
    assert doc["posts"][0]["files"][0]["status"] == "planned"
