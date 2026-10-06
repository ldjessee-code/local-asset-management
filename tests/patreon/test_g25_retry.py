# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 25: a failed download is not indexed, and the next sync tries again."""

from __future__ import annotations

import json
from pathlib import Path

from lam.patreon.http_source import FixtureSource
from tests.patreon.builders import attachment, creator, page, post, write_config, write_fixture
from tests.patreon.conftest import invoke


def _tree(tmp_path: Path):
    root = tmp_path / "fx"
    item = post("p1", "Harbor", "2026-06-01T15:00:00+00:00", attachments=[attachment("m1", "map.zip")])
    write_fixture(root, "kidneyboy", [page([item])])
    (root / "files").mkdir()
    (root / "files" / "m1").write_bytes(b"alpha-bytes")
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy")],
        staging_root=str(tmp_path / "stage"),
    )
    return root, cfg


def _sync(root, cfg, monkeypatch):
    monkeypatch.setenv("LAM_PATREON_NOW", "2026-10-01T12:00:00-04:00")
    return invoke(
        ["patreon", "sync", "--creators", str(cfg), "--fixture-dir", str(root), "--apply", "--since", "2025-01-01"]
    )


def test_failed_download_is_absent_from_index_and_retried(tmp_path: Path, monkeypatch, capsys):
    root, cfg = _tree(tmp_path)
    original = FixtureSource.download
    state = {"failed": False}

    def flaky(self, media, dest):
        if not state["failed"]:
            state["failed"] = True
            raise OSError("disk full")
        return original(self, media, dest)

    monkeypatch.setattr(FixtureSource, "download", flaky)
    assert _sync(root, cfg, monkeypatch) == 1
    captured = capsys.readouterr()
    assert "ready for notLib expand" in captured.out
    assert any(line.startswith("FINAL ") and "command=patreon-sync" in line for line in captured.err.splitlines())
    index_path = tmp_path / "stage" / "Kidney_Boy" / "_index" / "patreon-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert "m1" not in index["media"]
    manifests = list((tmp_path / "stage").rglob("drop-manifest_*.json"))
    assert len(manifests) == 1
    document = json.loads(manifests[0].read_text(encoding="utf-8"))
    failed = document["posts"][0]["files"][0]
    assert failed["status"] == "failed"
    assert failed["reason"] == "OSError: disk full"

    monkeypatch.setenv("LAM_PATREON_NOW", "2026-10-01T12:00:02-04:00")
    assert invoke(
        ["patreon", "sync", "--creators", str(cfg), "--fixture-dir", str(root), "--apply", "--since", "2025-01-01"]
    ) == 0
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert "m1" in index["media"]
    zips = [path for path in (tmp_path / "stage").rglob("map.zip") if path.is_file()]
    assert len(zips) == 1
    assert zips[0].read_bytes() == b"alpha-bytes"
    manifests = sorted((tmp_path / "stage").rglob("drop-manifest_*.json"))
    latest = json.loads(manifests[-1].read_text(encoding="utf-8"))
    assert latest["posts"][0]["files"][0]["status"] == "downloaded"
