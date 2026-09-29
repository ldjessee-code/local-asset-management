# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folder-safe slugs and Windows file names."""

from __future__ import annotations

import re

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def folder_safe(slug: str) -> bool:
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        return False
    if slug.upper() in _RESERVED:
        return False
    if slug.endswith((".", " ")):
        return False
    return True


def sanitize_component(name: str, *, fallback: str = "file", limit: int = 80) -> str:
    cleaned = _INVALID.sub("_", name or "").rstrip(" .")
    if not cleaned:
        cleaned = fallback
    head = cleaned.split(".", 1)[0]
    if head.upper() in _RESERVED:
        cleaned = "_" + cleaned
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip(" .")
    return cleaned or fallback
