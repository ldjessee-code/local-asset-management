# SPDX-License-Identifier: AGPL-3.0-or-later
"""lam publish uses the same copy, skip, and dry-run rules."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lam.hashing import sha256_full
from lam.stage.publish import publish_file


def test_publish_copies_and_writes_undo(tmp_path: Path):
    src = tmp_path / "tracker.csv"
    src.write_bytes(b"a,b\n")
    dest = tmp_path / "live" / "tracker.csv"
    code = publish_file(src, dest)
    assert code == 0
    assert dest.read_bytes() == b"a,b\n"
    assert src.exists()
    undo = Path(str(dest) + ".publish-undo.csv")
    assert undo.is_file()
    assert sha256_full(dest) == sha256_full(src)


def test_publish_skips_same_hash(tmp_path: Path):
    src = tmp_path / "src" / "tracker.csv"
    src.parent.mkdir()
    src.write_bytes(b"same")
    dest = tmp_path / "live" / "tracker.csv"
    dest.parent.mkdir()
    dest.write_bytes(b"same")
    assert publish_file(src, dest) == 0
    assert not Path(str(dest) + ".publish-undo.csv").exists()


def test_publish_conflict_writes_nothing(tmp_path: Path):
    src = tmp_path / "src" / "tracker.csv"
    src.parent.mkdir()
    src.write_bytes(b"new")
    dest = tmp_path / "live" / "tracker.csv"
    dest.parent.mkdir()
    dest.write_bytes(b"old")
    assert publish_file(src, dest) == 1
    assert dest.read_bytes() == b"old"
    assert not Path(str(dest) + ".publish-undo.csv").exists()


def test_publish_dry_run_writes_nothing(tmp_path: Path):
    src = tmp_path / "tracker.csv"
    src.write_bytes(b"new")
    dest = tmp_path / "out" / "tracker.csv"
    assert publish_file(src, dest, dry_run=True) == 0
    assert not dest.exists()
    assert not Path(str(dest) + ".publish-undo.csv").exists()


def test_publish_retries_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "src" / "tracker.csv"
    src.parent.mkdir()
    src.write_bytes(b"row")
    dest = tmp_path / "live" / "tracker.csv"
    state = {"n": 0}
    real = os.replace

    def flaky(src_path, dest_path):
        state["n"] += 1
        if state["n"] < 3:
            raise PermissionError(13, "Access is denied")
        real(src_path, dest_path)

    monkeypatch.setattr("lam.stage.copying._replace", flaky)
    sleeps: list[float] = []
    assert publish_file(src, dest, sleep=sleeps.append) == 0
    assert sleeps == [1, 2]
    assert dest.read_bytes() == b"row"
