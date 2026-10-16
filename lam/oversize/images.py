# SPDX-License-Identifier: AGPL-3.0-or-later
"""pyvips helpers. Imported only when an oversize command needs pixels."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from lam.oversize.errors import MissingPyvips, OversizeError

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"})

# Long-side attempts, largest first. ``max_dim`` is tried even when it is not in this list.
_STANDIN_EDGES = (8000, 4000, 2000, 1600, 1200, 800, 400, 200, 100, 64, 32)
# JPEG quality attempts. Never try a step above the caller's ``quality``.
_STANDIN_QUALITIES = (90, 70, 50, 30, 15, 5)


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


def _standin_edges(max_dim: int) -> list[int]:
    """``max_dim`` first, then the fixed edges that are strictly smaller."""
    limit = int(max_dim)
    edges = [limit]
    for edge in _STANDIN_EDGES:
        if edge < limit:
            edges.append(edge)
    return edges


def _standin_qualities(src: Path, quality: int) -> list[int]:
    """PNG ignores quality. JPEG proxies (including WebP and TIFF) may drop Q."""
    limit = int(quality)
    if proxy_extension(src) == ".png":
        return [limit]
    steps = [step for step in _STANDIN_QUALITIES if step <= limit]
    return steps or [limit]


def write_standin_under_bytes(
    src: Path,
    dest: Path,
    *,
    max_bytes: int,
    max_dim: int = 8000,
    quality: int = 90,
) -> tuple[int, int]:
    """Write a proxy at or under ``max_bytes``. Never removes or recycles *src*.

    Each attempt goes through :func:`write_proxy` to a scratch file. ``write_proxy``
    picks JPEG or PNG from the destination suffix, so the scratch keeps *dest*'s
    suffix. A scratch that is still over the cap is deleted. A scratch that fits
    is published with ``os.replace`` only while *dest* is absent.
    """
    if max_bytes < 1:
        raise OversizeError("max_bytes must be at least 1")
    src = Path(src)
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(f"refusing to replace existing stand-in: {dest}")

    for edge in _standin_edges(max_dim):
        for attempt_quality in _standin_qualities(src, quality):
            # Suffix matches *dest* so write_proxy does not JPEG-save a PNG.
            scratch = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.try{dest.suffix}")
            published = False
            try:
                width, height = write_proxy(src, scratch, max_dim=edge, quality=attempt_quality)
                if scratch.stat().st_size > max_bytes:
                    continue
                if dest.exists():
                    raise FileExistsError(f"refusing to replace existing stand-in: {dest}")
                os.replace(scratch, dest)
                published = True
                return width, height
            finally:
                if not published:
                    _unlink_temp(scratch)
    raise OversizeError(f"stand-in exceeds max_bytes ({max_bytes})", code=1)
