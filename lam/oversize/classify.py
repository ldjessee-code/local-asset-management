# SPDX-License-Identifier: AGPL-3.0-or-later
"""Decide whether an oversize map is a combined overview, and which parts exist.

A part is another image in the same set whose name is the overview name plus
exactly one letter, digit, or ``pt<digit>`` token, and which has fewer pixels.
"""

from __future__ import annotations

import re
from pathlib import Path

NOISE = frozenset(
    {
        "combined",
        "variations",
        "gridless",
        "gridded",
        "grid",
        "hd",
        "a2",
        "pta",
        "ptb",
        "online",
    }
)

_PT = re.compile(r"^pt(\d+)$")
_APOSTROPHES = ("'", "\u2019", "\u2018")


def normalize_tokens(stem: str) -> list[str]:
    """Lowercase, drop apostrophes and spaces, collapse ``_``, drop noise."""
    text = stem.lower()
    for mark in _APOSTROPHES:
        text = text.replace(mark, "")
    text = "".join(ch for ch in text if not ch.isspace())
    text = re.sub(r"_+", "_", text).strip("_")
    return [token for token in text.split("_") if token and token not in NOISE]


def _marker(token: str) -> str | None:
    if len(token) == 1 and token.isalnum():
        return token
    match = _PT.fullmatch(token)
    if match:
        return match.group(1)
    return None


def extra_marker(overview: list[str], candidate: list[str]) -> str | None:
    """Return the extra letter or digit if *candidate* is a part of *overview*."""
    if len(candidate) != len(overview) + 1:
        return None
    for index, token in enumerate(candidate):
        marker = _marker(token)
        if marker is None:
            continue
        if candidate[:index] + candidate[index + 1 :] == overview:
            return marker
    return None


def contiguous_run(markers: list[str]) -> bool:
    """True when 2+ distinct markers are ``a, b, c...`` or ``1, 2, 3...``."""
    unique = set(markers)
    if len(unique) < 2:
        return False
    if all(len(item) == 1 and item.isalpha() for item in unique):
        ords = sorted(ord(item) for item in unique)
        start = ord("a")
        return ords == list(range(start, start + len(ords)))
    if all(item.isdigit() for item in unique):
        numbers = sorted(int(item) for item in unique)
        return numbers == list(range(1, len(numbers) + 1))
    return False


def marker_text(markers: list[str]) -> str:
    if not markers:
        return "-"
    unique = set(markers)
    if all(item.isdigit() for item in unique):
        return "".join(str(number) for number in sorted(int(item) for item in unique))
    return "".join(sorted(unique))


def area_percent(part_pixels: int, total_pixels: int) -> tuple[float, int]:
    if total_pixels <= 0:
        return 0.0, 0
    raw = 100.0 * part_pixels / total_pixels
    return raw, int(round(raw))


def parts_found(markers: list[str], raw_area: float) -> str:
    if not markers:
        return "no"
    if raw_area >= 80.0 or contiguous_run(markers):
        return "yes"
    return "partial"


def path_says_combined(path: Path, root: Path) -> bool:
    """True when any folder or the file name under *root* contains ``combined``."""
    relative = path.resolve().relative_to(root.resolve())
    chunks = [root.name, *relative.parts]
    return any("combined" in chunk.lower() for chunk in chunks)


def evidence_text(
    *,
    name_combined: bool,
    count: int,
    markers: list[str],
    area: int,
    status: str,
) -> str:
    if count:
        phrase = f"{count} part maps of same variant in set"
    else:
        phrase = "no lettered/pt part maps of this map in set"
    if name_combined:
        head = f"name/folder says Combined; {phrase}"
    elif count:
        head = phrase
    else:
        head = f"no Combined in name; {phrase}"
    if status == "yes" and contiguous_run(markers) and area < 80:
        rule = "letter/digit run from a/1"
    elif status == "yes":
        rule = "area>=80"
    elif status == "partial":
        rule = "parts present, area under 80, run incomplete"
    else:
        rule = "no parts"
    return f"{head}; rule={rule}; letters={marker_text(markers)}; area={area}%"


def set_directory(path: Path, root: Path, depth: int) -> Path:
    """Ancestor *depth* levels below *root*. A file in *root* uses *root*."""
    relative_parent = path.resolve().parent.relative_to(root.resolve())
    parts = relative_parent.parts[: max(depth, 0)]
    if not parts:
        return root.resolve()
    return root.resolve().joinpath(*parts)


def is_inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
    except (OSError, ValueError):
        return False
    return True


def should_zip(mode: str, combined: bool, found: str) -> bool:
    if mode == "none":
        return False
    if mode == "all":
        return True
    if mode == "combined":
        return combined
    if mode == "combined-with-parts":
        return combined and found == "yes"
    raise ValueError(f"unknown zip mode: {mode}")
