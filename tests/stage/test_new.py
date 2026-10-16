# SPDX-License-Identifier: AGPL-3.0-or-later
"""lam stage new layout, clock, and schema."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from lam.cli import main
from lam.stage.job import create_job, load_job
from lam.stage.paths import MAP_BYTE_CAP


def test_stage_new_layout_and_schema(tmp_path: Path):
    target = tmp_path / "library" / "books"
    root = tmp_path / "root"
    folder = create_job(
        owner="notLib",
        job="books",
        target=target,
        root=root,
        plan=str(tmp_path / "plan.json"),
        kind="generic",
    )
    for name in ("in", "work", "out", "logs"):
        assert (folder / name).is_dir()
    job = load_job(folder / "job.json")
    assert job["schema"] == "lam-stage-job/v1"
    assert job["owner"] == "notLib"
    assert job["job"] == "books"
    assert job["target"] == str(target)
    assert job["plan"] == str(tmp_path / "plan.json")
    assert job["status"] == "new"
    assert job["kind"] == "generic"
    assert job["byte_cap"] is None
    raw = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert raw["status"] == "new"


def test_map_kind_stores_decimal_cap(tmp_path: Path):
    folder = create_job(
        owner="Ark",
        job="maps",
        target=tmp_path / "target",
        root=tmp_path / "root",
        kind="map",
    )
    assert load_job(folder / "job.json")["byte_cap"] == MAP_BYTE_CAP == 100_000_000


def test_clock_and_same_minute_suffix(tmp_path: Path):
    moment = datetime(2026, 10, 16, 15, 30, tzinfo=timezone.utc)
    root = tmp_path / "root"
    first = create_job(owner="Ark", job="maps", target=tmp_path / "t", root=root, now=moment)
    second = create_job(owner="Ark", job="maps", target=tmp_path / "t", root=root, now=moment)
    assert first.name == "maps_20261016_1530"
    assert second.name == "maps_20261016_1530_2"


def test_cli_stage_new(tmp_path: Path, capsys):
    root = tmp_path / "root"
    target = tmp_path / "target"
    try:
        main(
            [
                "stage",
                "new",
                "--owner",
                "Ark",
                "--job",
                "maps",
                "--target",
                str(target),
                "--root",
                str(root),
            ]
        )
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("main did not exit")
    printed = capsys.readouterr().out.strip()
    assert (Path(printed) / "job.json").is_file()
