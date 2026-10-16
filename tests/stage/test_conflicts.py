# SPDX-License-Identifier: AGPL-3.0-or-later
"""Duplicates, conflicts, suffix, replace, and dry-run."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lam.stage.job import create_job, load_job
from lam.stage.place import place_job


def _ready(tmp_path: Path, body: bytes = b"new-bytes") -> tuple[Path, Path]:
    target = tmp_path / "target"
    target.mkdir()
    folder = create_job(owner="Ark", job="notes", target=target, root=tmp_path / "root")
    (folder / "out" / "note.txt").write_bytes(body)
    return folder, target


def test_same_hash_is_a_dup(tmp_path: Path):
    folder, target = _ready(tmp_path, b"same")
    dest = target / "note.txt"
    dest.write_bytes(b"same")
    before = dest.read_bytes()
    calls: list[str] = []
    code = place_job(folder, jobs_dir=tmp_path / "jobs", doorbell=calls.append)
    assert code == 0
    assert dest.read_bytes() == before
    assert "dup" in (folder / "logs" / "place.log").read_text(encoding="utf-8")
    assert not (folder / "logs" / "undo.csv").exists()
    assert load_job(folder / "job.json")["status"] == "placed"
    assert calls == ["PASS"]


def test_different_hash_stops_with_nothing_written(tmp_path: Path):
    folder, target = _ready(tmp_path, b"new-bytes")
    dest = target / "note.txt"
    dest.write_bytes(b"old-bytes")
    calls: list[str] = []
    code = place_job(folder, jobs_dir=tmp_path / "jobs", doorbell=calls.append)
    assert code == 1
    assert dest.read_bytes() == b"old-bytes"
    assert load_job(folder / "job.json")["status"] == "unplaced"
    assert str(dest) in (folder / "logs" / "conflicts.txt").read_text(encoding="utf-8")
    assert not (folder / "manifest.json").exists()
    assert calls == ["FAIL"]
    assert not any((folder / "work").iterdir())


def test_suffix_v2_then_v3(tmp_path: Path):
    folder, target = _ready(tmp_path, b"new-bytes")
    (target / "note.txt").write_bytes(b"old")
    (target / "note_v2.txt").write_bytes(b"older")
    code = place_job(
        folder,
        jobs_dir=tmp_path / "jobs",
        on_conflict="suffix",
        doorbell=lambda _status: None,
    )
    assert code == 0
    assert (target / "note.txt").read_bytes() == b"old"
    assert (target / "note_v2.txt").read_bytes() == b"older"
    assert (target / "note_v3.txt").read_bytes() == b"new-bytes"


def test_replace_recycles_once_and_does_not_unlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    folder, target = _ready(tmp_path, b"new-bytes")
    dest = target / "note.txt"
    dest.write_bytes(b"old-bytes")
    recycled: list[Path] = []
    real_unlink = os.unlink

    def fake_recycle(path: Path) -> None:
        recycled.append(Path(path))
        real_unlink(path)

    def banned(path, *_args, **_kwargs):
        raise AssertionError(f"hard delete {path}")

    monkeypatch.setattr(os, "remove", banned)
    monkeypatch.setattr(os, "unlink", banned)
    code = place_job(
        folder,
        jobs_dir=tmp_path / "jobs",
        replace=True,
        recycle=fake_recycle,
        doorbell=lambda _status: None,
    )
    assert code == 0
    assert recycled == [dest]
    assert dest.read_bytes() == b"new-bytes"


def test_dry_run_writes_nothing_and_does_not_ring(tmp_path: Path):
    folder, target = _ready(tmp_path)
    before = (folder / "job.json").read_text(encoding="utf-8")
    calls: list[str] = []
    code = place_job(folder, dry_run=True, jobs_dir=tmp_path / "jobs", doorbell=calls.append)
    assert code == 0
    assert (folder / "job.json").read_text(encoding="utf-8") == before
    assert not (folder / "manifest.json").exists()
    assert not any((folder / "work").iterdir())
    assert not (target / "note.txt").exists()
    assert calls == []
    assert not (tmp_path / "jobs").exists()
