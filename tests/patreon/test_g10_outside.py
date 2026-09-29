# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 10: attachments download; outside hosts are deferred and never fetched."""

from __future__ import annotations

import json
import socket
from pathlib import Path

from lam.patreon.http_source import FixtureSource
from tests.patreon.builders import attachment, creator, page, post, write_config, write_fixture
from tests.patreon.conftest import invoke

CONTENT = (
    '<a href="https://www.dropbox.com/s/fake/pack.zip">d</a>'
    '<a href="https://drive.google.com/file/d/abc">g</a>'
    '<a href="https://mega.nz/file/abc">m</a>'
    '<a href="https://www.mediafire.com/file/abc">f</a>'
)


def test_outside_links_not_fetched(tmp_path: Path, capsys):
    FixtureSource.downloaded = []
    root = tmp_path / "fx"
    only = post("out", "Drive pack", "2026-06-01T15:00:00+00:00", content=CONTENT)
    both = post(
        "mix",
        "Mixed",
        "2026-07-01T15:00:00+00:00",
        content=CONTENT,
        attachments=[attachment("zip1", "map.zip")],
    )
    write_fixture(root, "kidneyboy", [page([both, only])])
    (root / "files").mkdir()
    (root / "files" / "zip1").write_bytes(b"PKZIP")
    stage = tmp_path / "stage"
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy")],
        staging_root=str(stage),
    )
    results = tmp_path / "out.json"
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
            str(results),
        ]
    )
    assert code == 0
    doc = json.loads(results.read_text(encoding="utf-8"))
    by_id = {item["post_id"]: item for item in doc["posts"]}
    assert by_id["out"]["status"] == "deferred"
    blob = json.dumps(by_id["out"])
    assert "dropbox.com" in blob and "https://www.dropbox.com/s/fake/pack.zip" in blob
    assert "drive.google.com" in blob and "mega.nz" in blob
    assert FixtureSource.downloaded == ["https://example.invalid/files/zip1/map.zip"]
    joined = " ".join(FixtureSource.downloaded)
    assert "dropbox.com" not in joined
    assert "mega.nz" not in joined
    assert "mediafire.com" not in joined
    assert "drive.google.com" not in joined
    with __import__("pytest").raises(RuntimeError, match="network is blocked"):
        socket.socket()
