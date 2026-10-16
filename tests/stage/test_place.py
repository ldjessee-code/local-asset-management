# SPDX-License-Identifier: AGPL-3.0-or-later
"""Place copies through a temp name and records an undo CSV."""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from lam.actions import UNDO_HEADER
from lam.hashing import sha256_full
from lam.stage.job import create_job, load_job
from lam.stage.place import place_job


def _job(tmp_path: Path, body: bytes = b"payload") -> tuple[Path, Path, Path]:
    root = tmp_path / "root"
    target = tmp_path / "target"
    target.mkdir()
    folder = create_job(owner="Ark", job="notes", target=target, root=root)
    (folder / "out" / "note.txt").write_bytes(body)
    return folder, target, tmp_path / "jobs"


def test_place_uses_temp_then_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    folder, target, jobs = _job(tmp_path)
    seen: list[str] = []
    real = os.replace

    def spy(src, dest):
        seen.append(Path(src).name)
        real(src, dest)

    monkeypatch.setattr("lam.stage.copying._replace", spy)
    calls: list[str] = []
    code = place_job(folder, jobs_dir=jobs, doorbell=calls.append)
    dest = target / "note.txt"
    assert code == 0
    assert seen == ["note.txt.~place.tmp"]
    assert (folder / "out" / "note.txt").read_bytes() == dest.read_bytes()
    assert sha256_full(dest) == sha256_full(folder / "out" / "note.txt")
    job = load_job(folder / "job.json")
    assert job["status"] == "placed"
    assert job["placed"]
    with (folder / "logs" / "undo.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == UNDO_HEADER
    assert calls == ["PASS"]
    assert (jobs / "manifest.json").is_file()
    assert (jobs / "logs" / "undo.csv").is_file()


def test_large_binary_log_is_not_copied(tmp_path: Path):
    folder, _target, jobs = _job(tmp_path)
    (folder / "logs" / "blob.bin").write_bytes(b"x" * 32)
    place_job(folder, jobs_dir=jobs, doorbell=lambda _status: None)
    assert not (jobs / "logs" / "blob.bin").exists()
    assert (jobs / "manifest.json").is_file()


def test_place_rename_failure_marks_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    folder, target, jobs = _job(tmp_path)

    def always_locked(_src, _dest):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr("lam.stage.copying._replace", always_locked)
    calls: list[str] = []
    code = place_job(folder, jobs_dir=jobs, doorbell=calls.append, sleep=lambda _seconds: None)
    assert code == 1
    assert not (target / "note.txt").exists()
    assert not list(target.glob("*.~place.tmp"))
    assert load_job(folder / "job.json")["status"] == "failed"
    assert calls == ["FAIL"]
