# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 14: manifest and index schemas, atomic index, second manifest name."""

from __future__ import annotations

import json
import os
from pathlib import Path

from lam.schema_lite import validate_json
from lam.schemas.registry import (
    PATREON_INDEX_SCHEMAS,
    PATREON_MANIFEST_SCHEMAS,
    PATREON_POST_LIST_SCHEMAS,
)
from tests.patreon.builders import attachment, creator, page, post, write_config, write_fixture
from tests.patreon.conftest import invoke


def test_outputs_match_schemas_and_keep_titles(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LAM_PATREON_NOW", "2026-10-01T12:00:00-04:00")
    root = tmp_path / "fx"
    items = [
        post("d1", "Downloaded", "2026-09-01T15:00:00+00:00", attachments=[attachment("m1", "map.zip")]),
        post(
            "s1",
            "Same Bytes",
            "2026-08-01T15:00:00+00:00",
            attachments=[attachment("m2", "copy.zip")],
        ),
        post(
            "o1",
            "Outside",
            "2026-07-01T15:00:00+00:00",
            content='<a href="https://mega.nz/file/abc">m</a>',
        ),
        post("f1", "Failed", "2026-06-01T15:00:00+00:00", attachments=[attachment("fail1", "bad.zip")]),
        post("l1", "Locked", "2026-05-01T15:00:00+00:00", can_view=False),
    ]
    write_fixture(root, "kidneyboy", [page(items)])
    (root / "files").mkdir()
    (root / "files" / "m1").write_bytes(b"alpha-bytes")
    (root / "files" / "m2").write_bytes(b"alpha-bytes")
    stage = tmp_path / "stage"
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy")],
        staging_root=str(stage),
    )
    results = tmp_path / "manifest.json"
    post_list = tmp_path / "list.json"
    assert invoke(
        [
            "patreon",
            "list",
            "--creators",
            str(cfg),
            "--fixture-dir",
            str(root),
            "--since",
            "2025-01-01",
            "--format",
            "json",
            "--results",
            str(post_list),
        ]
    ) == 0
    renames: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src, dst):
        renames.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    assert invoke(
        [
            "patreon",
            "sync",
            "--creators",
            str(cfg),
            "--fixture-dir",
            str(root),
            "--apply",
            "--since",
            "2025-01-01",
            "--results",
            str(results),
        ]
    ) == 1
    manifest = json.loads(results.read_text(encoding="utf-8"))
    listed = json.loads(post_list.read_text(encoding="utf-8"))
    assert validate_json(manifest, PATREON_MANIFEST_SCHEMAS["patreon-drop-manifest/v1"].load_document()) == []
    assert validate_json(listed, PATREON_POST_LIST_SCHEMAS["patreon-post-list/v1"].load_document()) == []
    by_status = {item["status"]: item for item in manifest["posts"]}
    for status in ("downloaded", "deferred", "failed", "skipped-duplicate", "locked"):
        assert status in by_status
        assert by_status[status]["post_title"]
        assert by_status[status]["post_url"].startswith("https://example.invalid/")
    index_path = stage / "Kidney_Boy" / "_index" / "patreon-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert validate_json(index, PATREON_INDEX_SCHEMAS["patreon-index/v1"].load_document()) == []
    assert any(
        Path(src).name.startswith(".lam-tmp-") and Path(dst).name == "patreon-index.json"
        for src, dst in renames
    )
    monkeypatch.setenv("LAM_PATREON_NOW", "2026-10-01T12:00:02-04:00")
    assert invoke(
        [
            "patreon",
            "sync",
            "--creators",
            str(cfg),
            "--fixture-dir",
            str(root),
            "--apply",
            "--since",
            "2025-01-01",
        ]
    ) == 0
    names = sorted(path.name for path in (stage / "Kidney_Boy").rglob("drop-manifest_*.json"))
    assert "drop-manifest_20261001_120000.json" in names
    assert "drop-manifest_20261001_120002.json" in names
