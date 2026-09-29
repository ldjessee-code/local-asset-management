# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 9: locked posts are not failures and are not downloaded."""

from __future__ import annotations

import json
from pathlib import Path

from lam.patreon.http_source import FixtureSource
from tests.patreon.builders import attachment, creator, page, post, write_config, write_fixture
from tests.patreon.conftest import invoke


def test_locked_not_downloaded(tmp_path: Path, capsys):
    FixtureSource.downloaded = []
    secret = attachment("secret", "secret.zip", url="https://example.invalid/files/secret.zip")
    root = tmp_path / "fx"
    write_fixture(
        root,
        "kidneyboy",
        [page([post("lock", "Hidden", "2026-06-01T15:00:00+00:00", can_view=False, attachments=[secret])])],
    )
    (root / "files").mkdir()
    (root / "files" / "secret").write_bytes(b"nope")
    stage = tmp_path / "stage"
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy")],
        staging_root=str(stage),
    )
    code = invoke(
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
            str(tmp_path / "out.json"),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "locked" in captured.out
    assert "failed" not in captured.out.lower()
    assert FixtureSource.downloaded == []
    assert "secret.zip" not in " ".join(FixtureSource.downloaded)
    text = (tmp_path / "out.json").read_text(encoding="utf-8")
    doc = json.loads(text)
    assert doc["posts"][0]["status"] == "locked"
    assert doc["posts"][0]["post_url"].startswith("https://example.invalid/")
    assert "https://example.invalid/files/secret.zip" not in text or doc["posts"][0]["status"] == "locked"
    assert not any(stage.rglob("secret.zip"))
