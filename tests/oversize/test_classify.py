# SPDX-License-Identifier: AGPL-3.0-or-later
"""Combined / parts rules on tiny images."""

from __future__ import annotations

from pathlib import Path

from lam.oversize.engine import run_oversize_scan

_SCAN = {"min_mb": 10_000, "min_mp": 0.0002, "set_depth": 1}


def _by_name(payload: dict) -> dict[str, dict]:
    return {Path(item["original_path"]).name: item for item in payload["files"]}


def test_combined_by_folder_name(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "Combined High res" / "Big_Map.jpg", 80, 40)
    payload, code = run_oversize_scan(root, register=tmp_path / "reg.json", **_SCAN)
    assert code == 0
    item = _by_name(payload)["Big_Map.jpg"]
    assert item["combined"] is True
    assert item["parts_found"] == "no"
    assert "Combined" in item["combined_evidence"]


def test_combined_by_part_match(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "Aztec" / "Zone_Day.jpg", 80, 40)
    write_image(root / "Aztec" / "Zone_F_Day.jpg", 8, 8)
    payload, _code = run_oversize_scan(root, register=tmp_path / "reg.json", **_SCAN)
    item = _by_name(payload)["Zone_Day.jpg"]
    assert item["combined"] is True
    assert item["parts_found"] == "partial"
    assert {Path(path).name for path in item["part_paths"]} == {"Zone_F_Day.jpg"}


def test_parts_found_yes_partial_no(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "Letters" / "Map_Day.jpg", 80, 40)
    write_image(root / "Letters" / "Map_A_Day.jpg", 8, 8)
    write_image(root / "Letters" / "Map_B_Day.jpg", 8, 8)
    write_image(root / "Single" / "Map_Day.jpg", 80, 40)
    write_image(root / "Single" / "Map_F_Day.jpg", 8, 8)
    write_image(root / "Digits" / "Interior_Lvl2_PtA_OreVein.jpg", 80, 40)
    write_image(root / "Digits" / "A2_Interior_Lvl2_pt1_OreVein.jpg", 8, 8)
    write_image(root / "Digits" / "A2_Interior_Lvl2_pt2_OreVein.jpg", 8, 8)
    write_image(root / "Plain" / "Hill_Day.jpg", 80, 40)
    payload, code = run_oversize_scan(root, register=tmp_path / "reg.json", **_SCAN)
    assert code == 0
    rows = payload["files"]
    assert len(rows) == 4
    by_parent = {Path(item["original_path"]).parent.name: item for item in rows}
    assert by_parent["Letters"]["parts_found"] == "yes"
    assert by_parent["Letters"]["combined"] is True
    assert by_parent["Single"]["parts_found"] == "partial"
    assert by_parent["Digits"]["parts_found"] == "yes"
    assert "12" in by_parent["Digits"]["combined_evidence"] or "1" in by_parent["Digits"]["combined_evidence"]
    assert by_parent["Plain"]["combined"] is False
    assert by_parent["Plain"]["parts_found"] == "no"


def test_part_must_have_fewer_pixels(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "Set" / "Zone_Day.jpg", 40, 20)
    write_image(root / "Set" / "Zone_A_Day.jpg", 80, 80)
    payload, _code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.00001,
        register=tmp_path / "reg.json",
    )
    by_name = _by_name(payload)
    assert by_name["Zone_Day.jpg"]["parts_found"] == "no"
    assert by_name["Zone_Day.jpg"]["part_paths"] == []
    assert by_name["Zone_A_Day.jpg"]["parts_found"] == "no"


def test_set_depth_scopes_part_search(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "S" / "a" / "Zone_Day.jpg", 80, 40)
    write_image(root / "S" / "b" / "Zone_A_Day.jpg", 8, 8)
    write_image(root / "S" / "b" / "Zone_B_Day.jpg", 8, 8)
    wide, _ = run_oversize_scan(root, register=tmp_path / "wide.json", **_SCAN)
    narrow, _ = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        set_depth=2,
        register=tmp_path / "narrow.json",
    )
    wide_item = _by_name(wide)["Zone_Day.jpg"]
    narrow_item = _by_name(narrow)["Zone_Day.jpg"]
    assert wide_item["parts_found"] == "yes"
    assert {Path(path).name for path in wide_item["part_paths"]} == {"Zone_A_Day.jpg", "Zone_B_Day.jpg"}
    assert narrow_item["parts_found"] == "no"
    assert narrow_item["combined"] is False


def test_file_in_folder_root_searches_whole_tree(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "Zone_Day.jpg", 80, 40)
    write_image(root / "nested" / "Zone_A_Day.jpg", 8, 8)
    write_image(root / "nested" / "Zone_B_Day.jpg", 8, 8)
    payload, _code = run_oversize_scan(root, register=tmp_path / "reg.json", **_SCAN)
    item = _by_name(payload)["Zone_Day.jpg"]
    assert item["parts_found"] == "yes"
    assert {Path(path).name for path in item["part_paths"]} == {"Zone_A_Day.jpg", "Zone_B_Day.jpg"}
