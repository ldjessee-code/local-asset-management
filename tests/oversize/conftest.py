# SPDX-License-Identifier: AGPL-3.0-or-later
"""Oversize tests stay on tmp_path, off the network, and off the real Recycle Bin."""

from __future__ import annotations

import socket
import zlib
from pathlib import Path

import pytest


class _BlockedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise RuntimeError("network is blocked in oversize tests")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(socket, "socket", _BlockedSocket)


@pytest.fixture(autouse=True)
def _no_real_recycle(monkeypatch: pytest.MonkeyPatch):
    """The product must take an injected recycle. The real bin is never called."""

    def boom(path):
        raise AssertionError(f"real recycle_path called for {path}")

    monkeypatch.setattr("lam.actions.recycle_path", boom)


@pytest.fixture
def write_image():
    pyvips = pytest.importorskip("pyvips")

    def _write(path: Path, width: int, height: int) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        crc = zlib.crc32(str(path).encode("utf-8"))
        red = (crc & 255) or 1
        green = ((crc >> 8) & 255) or 2
        blue = ((crc >> 16) & 255) or 3
        data = bytes((red, green, blue)) * (width * height)
        image = pyvips.Image.new_from_memory(data, width, height, 3, "uchar")
        ext = path.suffix.lower()
        target = str(path)
        if ext in {".jpg", ".jpeg"}:
            image.jpegsave(target, Q=70)
        elif ext == ".png":
            image.pngsave(target)
        elif ext == ".webp":
            image.webpsave(target)
        elif ext in {".tif", ".tiff"}:
            image.tiffsave(target)
        else:
            raise AssertionError(f"unsupported test image suffix: {ext}")
        return path

    return _write
