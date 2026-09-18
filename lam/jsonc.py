# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSON with ``//`` and ``/* */`` comments. Strings are left intact."""

from __future__ import annotations

import json
from typing import Any


def strip_jsonc(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    in_str = False
    escape = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in "\n\r":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(n, i + 2)
            continue
        out.append(c)
        i += 1
    return "".join(out)


def loads(text: str) -> Any:
    return json.loads(strip_jsonc(text))


def load_path(path) -> Any:
    return loads(path.read_text(encoding="utf-8"))


HEADER = (
    "// Local Asset Management config.\n"
    "// Keep this file outside every source folder and outside the destination.\n"
)


def dump_path(path, data: Any) -> None:
    text = HEADER + json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
