# SPDX-License-Identifier: AGPL-3.0-or-later
"""File-name warnings applied at place time. ``--strict`` blocks on any warning."""

from __future__ import annotations

import re
from pathlib import Path

from lam.stage.paths import ILLEGAL, RESERVED

BOOK_EXTENSIONS = {".pdf", ".epub", ".mobi", ".azw", ".azw3", ".djvu", ".txt"}
BAD_RESIZE = ("_4k", "_2k", "_1k", "_max8000", "_proxy", "_resized")


def _universal(name: str) -> list[str]:
    warnings: list[str] = []
    if " " in name:
        warnings.append(f"spaces in {name!r}; use underscores")
    if any(char in ILLEGAL or ord(char) < 32 for char in name):
        warnings.append(f"illegal characters in {name!r}")
    if name.endswith(" ") or name.endswith("."):
        warnings.append(f"trailing dot or space in {name!r}")
    stem = Path(name).stem
    if stem.upper() in RESERVED or name.upper() in RESERVED:
        warnings.append(f"reserved name {name!r}")
    return warnings


def _game_book(filename: str) -> list[str]:
    message = f"game book name does not match System_Edition_Title: {filename}"
    if Path(filename).suffix.lower() != ".pdf":
        return [message]
    parts = Path(filename).stem.split("_")
    if any(not part for part in parts):
        return [message]
    if len(parts) == 3:
        return []
    if len(parts) == 4:
        return []
    if len(parts) == 5 and parts[1] == "Fan":
        return []
    return [message]


def _fiction(filename: str) -> list[str]:
    message = f"fiction name does not match Author_Year_Title: {filename}"
    if Path(filename).suffix.lower() not in BOOK_EXTENSIONS:
        return [message]
    parts = Path(filename).stem.split("_")
    if len(parts) < 3 or not re.fullmatch(r"\d{4}", parts[1]) or not parts[0] or not parts[2]:
        return [message]
    rest = parts[3:]
    if len(rest) >= 2 and rest[-2] == "coauth":
        if not rest[-1]:
            return [message]
        rest = rest[:-2]
    if not rest:
        return []
    if len(rest) == 2 and rest[0] and rest[1].isdigit():
        return []
    return [message]


def _review(filename: str) -> list[str]:
    message = f"review name does not match notBot_hint_yyyyMMdd: {filename}"
    suffix = Path(filename).suffix
    if len(suffix) < 2 or not suffix[1:].isalnum():
        return [message]
    parts = Path(filename).stem.split("_")
    if len(parts) < 3 or not re.fullmatch(r"not[A-Za-z0-9].*", parts[0]):
        return [message]
    if (
        len(parts) >= 4
        and re.fullmatch(r"\d{4}", parts[-1])
        and re.fullmatch(r"\d{8}", parts[-2])
    ):
        hint = parts[1:-2]
    elif re.fullmatch(r"\d{8}", parts[-1]):
        hint = parts[1:-1]
    else:
        return [message]
    if not hint or any(not part for part in hint):
        return [message]
    return []


def _image(filename: str) -> list[str]:
    stem = Path(filename).stem.casefold()
    if stem.endswith("_8k") or stem.endswith("_web"):
        return []
    for suffix in BAD_RESIZE:
        if stem.endswith(suffix):
            return [f"resized copy must be suffixed _8k or _web: {filename}"]
    return []


def lint_relative(relative: Path, kind: str) -> list[str]:
    """Warnings for one relative path. Pattern rules apply to the file name only."""
    warnings: list[str] = []
    parts = Path(relative).parts
    for part in parts:
        warnings.extend(_universal(part))
    if not parts or kind == "generic":
        return warnings
    filename = parts[-1]
    if kind == "game-book":
        warnings.extend(_game_book(filename))
    elif kind == "fiction":
        warnings.extend(_fiction(filename))
    elif kind == "review":
        warnings.extend(_review(filename))
    elif kind in {"image", "map"}:
        warnings.extend(_image(filename))
    return warnings
