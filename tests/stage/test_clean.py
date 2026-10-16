# SPDX-License-Identifier: AGPL-3.0-or-later
"""stage clean recycles old placed payload and only flags failed jobs stale."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lam.stage.clean import clean_staging
from lam.stage.job import create_job, load_job, save_job


def _at(days_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _make(tmp_path: Path, *, status: str, when: datetime, stamp: str | None) -> Path:
    folder = create_job(owner="Ark", job="notes", target=tmp_path / "t", root=tmp_path / "root", now=when)
    (folder / "out" / "keep.txt").write_bytes(b"x")
    job = load_job(folder / "job.json")
    job["status"] = status
    job["placed"] = stamp
    save_job(folder / "job.json", job)
    return folder


def test_dry_run_removes_nothing(tmp_path: Path):
    when = _at(20)
    folder = _make(tmp_path, status="placed", when=when, stamp=when.isoformat())
    code = clean_staging(root=tmp_path / "root", apply=False, now=datetime.now(timezone.utc))
    assert code == 0
    assert (folder / "out" / "keep.txt").is_file()
    assert load_job(folder / "job.json").get("cleaned") in (None, "")


def test_apply_recycles_old_placed_and_keeps_job(tmp_path: Path):
    when = _at(15)
    folder = _make(tmp_path, status="placed", when=when, stamp=when.isoformat())
    recycled: list[Path] = []

    def recycle(path: Path) -> None:
        recycled.append(Path(path))
        shutil.rmtree(path)

    now = datetime.now(timezone.utc)
    assert clean_staging(root=tmp_path / "root", apply=True, now=now, recycle=recycle) == 0
    names = {path.name for path in recycled}
    assert names == {"in", "work", "out", "logs"}
    assert (folder / "job.json").is_file()
    assert not (folder / "out").exists()
    assert load_job(folder / "job.json")["cleaned"]


def test_young_placed_job_is_kept(tmp_path: Path):
    when = _at(1)
    folder = _make(tmp_path, status="placed", when=when, stamp=when.isoformat())
    clean_staging(root=tmp_path / "root", apply=True, now=datetime.now(timezone.utc), recycle=lambda _p: (_ for _ in ()).throw(AssertionError("recycled")))
    assert (folder / "out" / "keep.txt").is_file()


def test_failed_and_unplaced_go_stale_and_stay(tmp_path: Path):
    old = _at(31)
    root = tmp_path / "root"
    failed = create_job(owner="Ark", job="bad", target=tmp_path / "t", root=root, now=old)
    (failed / "out" / "a.txt").write_bytes(b"a")
    job = load_job(failed / "job.json")
    job["status"] = "failed"
    save_job(failed / "job.json", job)
    unplaced = create_job(owner="Ark", job="held", target=tmp_path / "t", root=root, now=old)
    (unplaced / "out" / "b.txt").write_bytes(b"b")
    job = load_job(unplaced / "job.json")
    job["status"] = "unplaced"
    save_job(unplaced / "job.json", job)
    fresh = create_job(owner="Ark", job="newjob", target=tmp_path / "t", root=root, now=old)
    assert clean_staging(root=root, apply=True, now=datetime.now(timezone.utc), recycle=lambda _p: (_ for _ in ()).throw(AssertionError("recycled"))) == 0
    assert load_job(failed / "job.json")["stale"] is True
    assert (failed / "out" / "a.txt").is_file()
    assert load_job(unplaced / "job.json")["stale"] is True
    assert (unplaced / "out" / "b.txt").is_file()
    assert load_job(fresh / "job.json")["stale"] is False
    assert (fresh / "out").is_dir()
