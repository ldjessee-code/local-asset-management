# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 13: index skip, sha duplicate, name suffix, part file."""

from __future__ import annotations

import os
from pathlib import Path

from lam.patreon.http_source import FixtureSource
from tests.patreon.builders import attachment, creator, page, post, write_config, write_fixture
from tests.patreon.conftest import invoke


def _tree(tmp_path: Path, posts_spec, files: dict[str, bytes]):
    root = tmp_path / "fx"
    items = []
    for pid, title, media_id, name in posts_spec:
        items.append(
            post(pid, title, "2026-06-01T15:00:00+00:00", attachments=[attachment(media_id, name)])
        )
    write_fixture(root, "kidneyboy", [page(items)])
    (root / "files").mkdir()
    for media_id, data in files.items():
        (root / "files" / media_id).write_bytes(data)
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


def test_second_apply_skips_by_media_id(tmp_path: Path, monkeypatch):
    root, cfg = _tree(tmp_path, [("p1", "Harbor", "m1", "map.zip")], {"m1": b"alpha-bytes"})
    assert _sync(root, cfg, monkeypatch) == 0
    FixtureSource.downloaded = []
    assert _sync(root, cfg, monkeypatch) == 0
    assert FixtureSource.downloaded == []
    assert len(list((tmp_path / "stage").rglob("map.zip"))) == 1


def test_same_bytes_new_id_skipped(tmp_path: Path, monkeypatch):
    root, cfg = _tree(
        tmp_path,
        [("p1", "Harbor", "m1", "map.zip"), ("p2", "Other", "m2", "other.zip")],
        {"m1": b"same-bytes", "m2": b"same-bytes"},
    )
    removes = []

    def boom(*args, **kwargs):
        removes.append(args)
        raise AssertionError("os.remove")

    monkeypatch.setattr(os, "remove", boom)
    assert _sync(root, cfg, monkeypatch) == 0
    assert removes == []
    zips = list((tmp_path / "stage").rglob("*.zip"))
    assert len(zips) == 1
    assert zips[0].read_bytes() == b"same-bytes"


def test_name_collision_gets_sha_suffix(tmp_path: Path, monkeypatch):
    root = tmp_path / "fx"
    item = post(
        "p1",
        "Harbor",
        "2026-06-01T15:00:00+00:00",
        attachments=[attachment("m1", "map.zip"), attachment("m2", "map.zip")],
    )
    write_fixture(root, "kidneyboy", [page([item])])
    (root / "files").mkdir()
    (root / "files" / "m1").write_bytes(b"alpha-bytes")
    (root / "files" / "m2").write_bytes(b"beta-bytes!")
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy")],
        staging_root=str(tmp_path / "stage"),
    )
    assert _sync(root, cfg, monkeypatch) == 0
    files = sorted((tmp_path / "stage").rglob("*.zip"))
    assert len(files) == 2
    names = sorted(path.name for path in files)
    assert "map.zip" in names
    other = [name for name in names if name != "map.zip"][0]
    assert "__" in other
    by_name = {path.name: path.read_bytes() for path in files}
    assert by_name["map.zip"] == b"alpha-bytes"
