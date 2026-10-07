# SPDX-License-Identifier: AGPL-3.0-or-later
"""pyvips helpers. Imported only when an oversize command needs pixels."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from lam.oversize.errors import MissingPyvips

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"})


def load_pyvips():
    """Import pyvips. Tests replace this to simulate a missing install."""
    import pyvips

    return pyvips


def require_pyvips():
    try:
        return load_pyvips()
    except ImportError:
        raise MissingPyvips() from None


def read_dimensions(path: Path) -> tuple[int, int]:
    """Header width and height. ``access='sequential'`` does not decode the pixels."""
    pyvips = require_pyvips()
    image = pyvips.Image.new_from_file(str(path), access="sequential")
    width, height = int(image.width), int(image.height)
    del image
    return width, height


def proxy_extension(src: Path) -> str:
    """JPEG and PNG keep their type. TIFF and WebP proxies are JPEG files."""
    ext = src.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png"}:
        return ext
    return ".jpg"


def _unlink_temp(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def write_proxy(src: Path, dest: Path, *, max_dim: int, quality: int) -> tuple[int, int]:
    """Shrink-on-load thumbnail. ``size='down'`` never upscales.

    The pixels land in a sibling temp file and replace *dest* only when that
    path is still absent.
    """
    pyvips = require_pyvips()
    image = pyvips.Image.thumbnail(str(src), int(max_dim), height=int(max_dim), size="down")
    width, height = int(image.width), int(image.height)
    if dest.exists():
        del image
        raise FileExistsError(f"refusing to replace existing proxy: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.tmp")
    try:
        if dest.suffix.lower() == ".png":
            image.pngsave(str(tmp))
        else:
            image.jpegsave(str(tmp), Q=int(quality))
        if dest.exists():
            raise FileExistsError(f"refusing to replace existing proxy: {dest}")
        os.replace(tmp, dest)
    except Exception:
        _unlink_temp(tmp)
        raise
    finally:
        del image
    return width, height
