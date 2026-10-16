# SPDX-License-Identifier: AGPL-3.0-or-later
"""Byte-capped stand-ins. Scan stays bytes OR pixels."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from lam.hashing import sha256_full
from lam.oversize.engine import _over_threshold, verify_zip, write_verified_zip
from lam.oversize.errors import OversizeError
from lam.oversize.images import write_standin_under_bytes


def test_over_threshold_is_still_bytes_or_pixels():
    assert _over_threshold(10, 10, 100, 89) is False
    assert _over_threshold(100 * 1024 * 1024 + 1, 10, 100, 89) is True
    assert _over_threshold(10, 89 * 1_000_000 + 1, 100, 89) is True


def _noisy_jpeg(path: Path, width: int, height: int) -> Path:
    """High-entropy JPEG. A flat colour compresses far below a useful cap."""
    pyvips = pytest.importorskip("pyvips")
    image = pyvips.Image.gaussnoise(width, height, sigma=80, mean=128).cast("uchar")
    if image.bands == 1:
        image = image.bandjoin([image, image])
    path.parent.mkdir(parents=True, exist_ok=True)
    image.jpegsave(str(path), Q=95)
    return path


def _scratches(directory: Path) -> list[Path]:
    return [path for path in directory.glob(".*") if ".try" in path.name]


def test_standin_fits_under_cap_and_leaves_source(tmp_path: Path):
    pyvips = pytest.importorskip("pyvips")
    src = _noisy_jpeg(tmp_path / "noise.jpg", 960, 640)
    src_size = src.stat().st_size
    assert src_size > 100_000
    cap = 60_000
    assert cap < src_size
    dest = tmp_path / "out" / "noise.jpg"

    width, height = write_standin_under_bytes(src, dest, max_bytes=cap)

    assert dest.is_file()
    assert dest.stat().st_size <= cap
    assert dest.read_bytes()[:2] == b"\xff\xd8"
    image = pyvips.Image.new_from_file(str(dest), access="sequential")
    assert (int(image.width), int(image.height)) == (width, height)
    assert width >= 1 and height >= 1
    assert src.is_file()
    assert src.stat().st_size == src_size
    assert _scratches(dest.parent) == []


def test_standin_refuses_existing_dest(tmp_path: Path, write_image):
    src = write_image(tmp_path / "src.jpg", 48, 32)
    src_size = src.stat().st_size
    dest = tmp_path / "already.jpg"
    dest.write_bytes(b"keep-these-bytes")

    with pytest.raises(FileExistsError):
        write_standin_under_bytes(src, dest, max_bytes=100_000)

    assert dest.read_bytes() == b"keep-these-bytes"
    assert src.is_file()
    assert src.stat().st_size == src_size
    assert _scratches(tmp_path) == []


def test_standin_one_byte_cap_raises_and_leaves_no_dest(tmp_path: Path, write_image):
    src = write_image(tmp_path / "src.jpg", 64, 48)
    src_size = src.stat().st_size
    dest = tmp_path / "tiny.jpg"

    with pytest.raises(OversizeError) as caught:
        write_standin_under_bytes(src, dest, max_bytes=1)

    assert caught.value.code == 1
    assert "1" in str(caught.value)
    assert "max_bytes" in str(caught.value)
    assert not dest.exists()
    assert src.is_file()
    assert src.stat().st_size == src_size
    assert _scratches(tmp_path) == []


def test_write_verified_zip_publishes_member_and_keeps_source(tmp_path: Path):
    src = tmp_path / "maps" / "Night Light.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"original-bytes-for-zip")
    src_size = src.stat().st_size
    digest = sha256_full(src)
    dest = tmp_path / "zips" / "Night_Light.zip"

    write_verified_zip(src, dest, digest)

    assert dest.is_file()
    verify_zip(dest, src, digest)
    with zipfile.ZipFile(dest) as handle:
        assert handle.namelist() == [src.name]
    assert src.is_file()
    assert src.stat().st_size == src_size


def test_write_verified_zip_refuses_existing_dest(tmp_path: Path):
    src = tmp_path / "plate.jpg"
    src.write_bytes(b"source-stays")
    src_size = src.stat().st_size
    dest = tmp_path / "plate.zip"
    dest.write_bytes(b"existing-zip-bytes")

    with pytest.raises(FileExistsError):
        write_verified_zip(src, dest, sha256_full(src))

    assert dest.read_bytes() == b"existing-zip-bytes"
    assert src.is_file()
    assert src.stat().st_size == src_size
