# SPDX-License-Identifier: AGPL-3.0-or-later
"""Doorbell argv is built here. Tests never start court-run.ps1."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lam.stage.doorbell import ring
from lam.stage.job import create_job
from lam.stage.place import place_job


def test_ring_argv_pass_and_fail():
    script = Path(r"C:\court-run.ps1")
    seen: list[list[str]] = []

    def fake(argv, **_kwargs):
        seen.append(list(argv))
        return SimpleNamespace(returncode=0)

    ring("PASS", name="maps", requester="Ark", script=script, run=fake)
    ring("FAIL", name="maps", requester="Ark", script=script, run=fake)
    pass_argv, fail_argv = seen
    assert pass_argv[:4] == ["pwsh", "-NoProfile", "-File", str(script)]
    assert "-Name" in pass_argv and "maps" in pass_argv
    assert "-Requester" in pass_argv and "Ark" in pass_argv
    assert "exit 0" in pass_argv
    assert "-JobsRoot" not in pass_argv
    assert "exit 1" in fail_argv


def test_place_rings_and_a_failed_ring_keeps_the_copy(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    folder = create_job(owner="Ark", job="notes", target=target, root=tmp_path / "root")
    (folder / "out" / "note.txt").write_bytes(b"ok")

    def boom(_status: str) -> None:
        raise RuntimeError("no bell")

    code = place_job(folder, jobs_dir=tmp_path / "jobs", doorbell=boom)
    assert code == 0
    assert (target / "note.txt").read_bytes() == b"ok"
    assert "doorbell failed" in (folder / "logs" / "place.log").read_text(encoding="utf-8")


def test_dry_run_does_not_ring(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    folder = create_job(owner="Ark", job="notes", target=target, root=tmp_path / "root")
    (folder / "out" / "note.txt").write_bytes(b"ok")
    calls: list[str] = []
    assert place_job(folder, dry_run=True, doorbell=calls.append) == 0
    assert calls == []
