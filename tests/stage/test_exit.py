# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exit 2: unknown schema and C:\\Example. No doorbell."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lam.cli import main
from lam.stage.job import create_job


def test_unknown_schema_exits_2(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch):
    rung: list[str] = []

    def fake_ring(*_args, **_kwargs):
        rung.append("called")

    monkeypatch.setattr("lam.stage.place.ring", fake_ring)
    monkeypatch.setattr("lam.stage.doorbell.ring", fake_ring)
    job = tmp_path / "job"
    (job / "out").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"schema": "nope/v1"}), encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main(["place", str(job), "--jobs-dir", str(tmp_path / "jobs")])
    assert caught.value.code == 2
    assert "unsupported" in capsys.readouterr().err
    assert rung == []


def test_example_target_exits_2(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch):
    rung: list[str] = []
    monkeypatch.setattr("lam.stage.place.ring", lambda *_a, **_k: rung.append("x"))
    folder = create_job(
        owner="Ark",
        job="notes",
        target=r"C:\Example\library",
        root=tmp_path / "root",
    )
    (folder / "out" / "note.txt").write_bytes(b"x")
    with pytest.raises(SystemExit) as caught:
        main(["place", str(folder), "--jobs-dir", str(tmp_path / "jobs")])
    assert caught.value.code == 2
    assert "example path" in capsys.readouterr().err
    assert rung == []
