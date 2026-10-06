# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reproduce the court's 35-row combined and parts_found columns."""

from __future__ import annotations

import re
from pathlib import Path

from tests.oversize.court_rows import DECOYS, ROWS

from lam.oversize.engine import run_oversize_scan

_FACTOR = 200
_MIN_MP = 0.002


def _scaled(width: int, height: int) -> tuple[int, int]:
    return max(2, int(round(width / _FACTOR))), max(2, int(round(height / _FACTOR)))


def _part_size(rel: str) -> tuple[int, int]:
    name = Path(rel).name
    # SlitherSwamp Day_Unlit is yes only because the parts cover >= 80% of
    # the area. The letter run a,b,d,e,f has a gap, so the pixels have to
    # be large enough. They still stay under the oversize threshold.
    if re.search(r"SlitherSwamp_[A-F]_Day_Unlit_", name):
        return (40, 24)
    return (8, 8)


def test_court_rows_match_combined_and_parts_found(tmp_path: Path, write_image):
    assert len(ROWS) == 35
    slither_w, slither_h = _scaled(10200, 21338)
    assert slither_w * slither_h > _MIN_MP * 1_000_000
    assert 5 * 40 * 24 >= 0.8 * slither_w * slither_h
    assert 40 * 24 < _MIN_MP * 1_000_000
    small_w, small_h = _scaled(9816, 12192)
    assert small_w * small_h > _MIN_MP * 1_000_000

    root = tmp_path / "library"
    written: dict[str, tuple[int, int]] = {}
    for row in ROWS:
        written[row.rel] = _scaled(row.width, row.height)
        for part in row.parts:
            written.setdefault(part, _part_size(part))
    for decoy in DECOYS:
        written.setdefault(decoy, (8, 8))
    for rel, (width, height) in written.items():
        write_image(root / Path(rel), width, height)

    payload, code = run_oversize_scan(
        root,
        min_mb=100_000,
        min_mp=_MIN_MP,
        set_depth=1,
        register=tmp_path / "reg.json",
    )
    assert code == 0
    assert len(payload["files"]) == 35
    by_name = {Path(item["original_path"]).name: item for item in payload["files"]}
    for row in ROWS:
        item = by_name[Path(row.rel).name]
        assert item["combined"] is row.combined, row.rel
        assert item["parts_found"] == row.parts_found, row.rel
        got = {Path(path).name for path in item["part_paths"]}
        expect = {Path(path).name for path in row.parts}
        assert got == expect, row.rel
