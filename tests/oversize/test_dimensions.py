# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dimensions come from the image header."""

from __future__ import annotations

from pathlib import Path

from lam.oversize.engine import run_oversize_scan


def test_header_dimensions_jpg_and_png(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "photo.jpg", 17, 13)
    write_image(root / "plate.png", 9, 21)
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0000001,
        register=tmp_path / "reg.json",
    )
    assert code == 0
    by_name = {Path(item["original_path"]).name: item for item in payload["files"]}
    assert (by_name["photo.jpg"]["width"], by_name["photo.jpg"]["height"]) == (17, 13)
    assert (by_name["plate.png"]["width"], by_name["plate.png"]["height"]) == (9, 21)
