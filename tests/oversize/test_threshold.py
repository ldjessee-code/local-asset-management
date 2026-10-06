# SPDX-License-Identifier: AGPL-3.0-or-later
"""Oversize is bytes OR pixels. Non-images and unsafe paths are skipped."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from lam.oversize.engine import run_oversize_scan


def _names(payload: dict) -> set[str]:
    return {Path(item["original_path"]).name for item in payload["files"]}


def test_oversize_when_bytes_exceed_min_mb(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    path = write_image(root / "small.jpg", 8, 8)
    size = path.stat().st_size
    hit = (size - 1) / (1024 * 1024)
    payload, code = run_oversize_scan(
        root,
        min_mb=hit,
        min_mp=89,
        register=tmp_path / "reg.json",
    )
    assert code == 0
    assert _names(payload) == {"small.jpg"}


def test_oversize_when_pixels_exceed_min_mp(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "wide.jpg", 80, 40)
    write_image(root / "tiny.jpg", 8, 8)
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        register=tmp_path / "reg.json",
    )
    assert code == 0
    assert _names(payload) == {"wide.jpg"}


def test_under_both_thresholds_is_ignored(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "tiny.jpg", 8, 8)
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=89,
        register=tmp_path / "reg.json",
    )
    assert code == 0
    assert payload["files"] == []


def test_thresholds_are_configurable(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "mid.jpg", 40, 20)
    low, _ = run_oversize_scan(root, min_mb=10_000, min_mp=0.0001, register=tmp_path / "a.json")
    high, _ = run_oversize_scan(root, min_mb=10_000, min_mp=0.01, register=tmp_path / "b.json")
    assert _names(low) == {"mid.jpg"}
    assert high["files"] == []


def test_skips_non_images_zips_proxies_and_git(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "keep.jpg", 80, 40)
    write_image(root / "keep_max8000.jpg", 80, 40)
    (root / "notes.txt").write_text("not an image", encoding="utf-8")
    with zipfile.ZipFile(root / "pack.zip", "w") as handle:
        handle.writestr("keep.jpg", b"not-a-real-jpeg")
    write_image(root / ".git" / "hidden.jpg", 80, 40)
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        register=tmp_path / "reg.json",
    )
    assert code == 0
    assert _names(payload) == {"keep.jpg"}


def test_skips_symlinks(tmp_path: Path, write_image):
    root = tmp_path / "maps"
    write_image(root / "keep.jpg", 80, 40)
    outside = tmp_path / "outside"
    write_image(outside / "secret.jpg", 80, 40)
    try:
        (root / "link.jpg").symlink_to(outside / "secret.jpg")
        (root / "linkdir").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink not permitted")
    payload, code = run_oversize_scan(
        root,
        min_mb=10_000,
        min_mp=0.0002,
        register=tmp_path / "reg.json",
    )
    assert code == 0
    assert _names(payload) == {"keep.jpg"}
