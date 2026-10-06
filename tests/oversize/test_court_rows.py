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
    _add_pack_name_candidates(written)
    for rel, (width, height) in written.items():
        write_image(root / Path(rel), width, height)

    payload, code = run_oversize_scan(
        root,
        min_mb=100_000,
        min_mp=_MIN_MP,
        set_depth=1,
        part_scope="set",
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

    pack, pack_code = run_oversize_scan(
        root,
        min_mb=100_000,
        min_mp=_MIN_MP,
        set_depth=1,
        part_scope="pack",
        locate=False,
        register=tmp_path / "pack.json",
    )
    assert pack_code == 0
    pack_by_name = {Path(item["original_path"]).name: item for item in pack["files"]}
    for variant in ("Day_Eth Plane", "Day", "Night_Eth Plane", "Night"):
        item = pack_by_name[f"Citadel_Combined_{variant}_Gridless.jpg"]
        assert {Path(path).name for path in item["part_paths"]} == {
            f"Citadel_{letter}_{variant}_Gridless.jpg" for letter in "ABCD"
        }, variant
    part_variant = {
        "Inferno": "Inferno",
        "OreVein": "Ore Vein",
        "Snow": "Snow",
        "Volcano": "Volcano",
    }
    for combined_variant, piece in part_variant.items():
        item = pack_by_name[f"DwarvenInterior_Lvl2_PtA_{combined_variant}.jpg"]
        names = {Path(path).name for path in item["part_paths"]}
        assert names == {f"A2_DwarvenInterior_Lvl2_pt{number}_{piece}.jpg" for number in range(1, 10)}


def _add_pack_name_candidates(written: dict[str, tuple[int, int]]) -> None:
    """Whole-pack name matches from evidence_notes.md. Not inside the phase-1 set."""
    for variant in ("Day_Eth Plane", "Day", "Night_Eth Plane", "Night"):
        for letter, number in (("B", "02"), ("C", "03"), ("D", "04")):
            rel = (
                f"5_RadiantCitadel_Set{number}\\HD\\Citadel_{letter}\\Gridless\\"
                f"Citadel_{letter}_{variant}_Gridless.jpg"
            )
            written.setdefault(rel, (8, 8))
    pieces = (
        ("Inferno", "Inferno"),
        ("Ore Vein", "OreVein"),
        ("Snow", "Snow"),
        ("Volcano", "Volcano"),
    )
    for part_variant, _combined_variant in pieces:
        for number in range(3, 10):
            rel = (
                f"5_DwarvenStronghold_Set{number + 5:02d}\\A2_Jpeg\\"
                f"A2_DwarvenInterior_Lvl2_pt{number}_{part_variant}.jpg"
            )
            written.setdefault(rel, (8, 8))
