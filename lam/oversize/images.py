# SPDX-License-Identifier: AGPL-3.0-or-later
"""pyvips helpers. Imported only when an oversize command needs pixels."""

from __future__ import annotations

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


def write_proxy(src: Path, dest: Path, *, max_dim: int, quality: int) -> tuple[int, int]:
    """Shrink-on-load thumbnail. ``size='down'`` never upscales."""
    pyvips = require_pyvips()
    image = pyvips.Image.thumbnail(str(src), int(max_dim), height=int(max_dim), size="down")
    if dest.suffix.lower() == ".png":
        image.pngsave(str(dest))
    else:
        image.jpegsave(str(dest), Q=int(quality))
    width, height = int(image.width), int(image.height)
    del image
    return width, height
