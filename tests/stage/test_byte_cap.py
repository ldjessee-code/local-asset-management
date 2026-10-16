# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kind map: stand-in under the byte cap, zip beside it, original kept in out/."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lam.hashing import sha256_full
from lam.stage.job import create_job
from lam.stage.paths import MAP_BYTE_CAP
from lam.stage.place import place_job


def _noisy_jpeg(path: Path) -> Path:
    pyvips = pytest.importorskip("pyvips")
    image = pyvips.Image.gaussnoise(960, 640, sigma=80, mean=128).cast("uchar")
    if image.bands == 1:
        image = image.bandjoin([image, image])
    path.parent.mkdir(parents=True, exist_ok=True)
    image.jpegsave(str(path), Q=95)
    return path


def test_map_standin_and_zip_are_both_placed(tmp_path: Path):
    pytest.importorskip("pyvips")
    assert MAP_BYTE_CAP == 100_000_000
    target = tmp_path / "target"
    target.mkdir()
    folder = create_job(
        owner="Ark",
        job="maps",
        target=target,
        root=tmp_path / "root",
        kind="map",
    )
    src = _noisy_jpeg(folder / "out" / "noise.jpg")
    cap = 60_000
    assert src.stat().st_size > cap
    recycled: list[Path] = []
    code = place_job(
        folder,
        jobs_dir=tmp_path / "jobs",
        byte_cap=cap,
        doorbell=lambda _status: None,
        recycle=lambda path: recycled.append(Path(path)),
    )
    assert code == 0
    assert recycled == []
    placed = target / "noise.jpg"
    zipped = target / "_Originals_Zipped" / "noise.zip"
    assert placed.is_file()
    assert placed.stat().st_size <= cap
    assert zipped.is_file()
    assert src.is_file()
    assert src.stat().st_size > cap
    document = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    roles = {entry["role"] for entry in document["entries"]}
    assert roles == {"stand-in", "original-zip"}
    for entry in document["entries"]:
        dest = Path(entry["dest"])
        assert dest.is_file()
        assert sha256_full(dest) == entry["sha256"]
        assert dest.stat().st_size == entry["size"]
