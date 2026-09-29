# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load a court-supplied ``lam-subbins/v1`` list. No built-in categories."""

from __future__ import annotations

from pathlib import Path

from lam.schemas.registry import SUBBINS_SCHEMAS
from lam.tag.bins import LEVEL1_BINS
from lam.tag.errors import TagError
from lam.tag.io import load_json_file, require_known_schema
from lam.tag.rules import SubBin


def check_subbin_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise TagError(f"invalid subbin name {name!r}")
    if name in {".", ".."} or name.lower() == ".git":
        raise TagError(f"invalid subbin name {name!r}")
    if any(ch in name for ch in "\\/:"):
        raise TagError(f"invalid subbin name {name!r}")


def load_subbins(path: Path) -> tuple[str, tuple[SubBin, ...]]:
    data = load_json_file(Path(path))
    require_known_schema(data, SUBBINS_SCHEMAS, "subbins")
    parent = data.get("bin")
    if parent not in LEVEL1_BINS:
        raise TagError(f"subbins bin must be one of {', '.join(LEVEL1_BINS)}")
    found: list[SubBin] = []
    seen: set[str] = set()
    for item in data["subbins"]:
        name = item["name"]
        check_subbin_name(name)
        if name in seen:
            raise TagError(f"duplicate subbin {name}")
        seen.add(name)
        hints = tuple(str(hint) for hint in (item.get("hints") or []))
        found.append(SubBin(name, str(item.get("description") or ""), hints))
    if not found:
        raise TagError("subbins list is empty")
    return str(parent), tuple(found)
