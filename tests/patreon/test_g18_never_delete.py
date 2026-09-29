# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 18: apply never calls remove, unlink, or rmtree."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests.patreon.builders import attachment, creator, page, post, write_config, write_fixture
from tests.patreon.conftest import invoke


def test_apply_never_deletes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("delete called")

    monkeypatch.setattr(os, "remove", boom)
    monkeypatch.setattr(os, "unlink", boom)
    monkeypatch.setattr(shutil, "rmtree", boom)
    monkeypatch.setattr(Path, "unlink", boom)
    monkeypatch.setattr(Path, "rmdir", boom)
    monkeypatch.setenv("LAM_PATREON_NOW", "2026-10-01T12:00:00-04:00")
    root = tmp_path / "fx"
    items = [
        post(
            "p1",
            "Harbor",
            "2026-06-01T15:00:00+00:00",
            attachments=[attachment("m1", "map.zip"), attachment("m2", "map.zip")],
        ),
        post("p2", "Bad", "2026-05-01T15:00:00+00:00", attachments=[attachment("fail1", "bad.zip")]),
    ]
    write_fixture(root, "kidneyboy", [page(items)])
    (root / "files").mkdir()
    (root / "files" / "m1").write_bytes(b"alpha-bytes")
    (root / "files" / "m2").write_bytes(b"alpha-bytes")
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy")],
        staging_root=str(tmp_path / "stage"),
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
        ]
    )
    assert code == 1
