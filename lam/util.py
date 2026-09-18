# SPDX-License-Identifier: AGPL-3.0-or-later
"""Path helpers: POSIX storage, source names, bundle/pack split."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

DATE_DIR = re.compile(
    r"^(?:\d{4}|\d{4}[-_]\d{2}|\d{4}[-_]\d{2}[-_]\d{2}|\d{8})$"
)
YEAR_DIR = re.compile(r"^\d{4}$")
MONTH_DIR = re.compile(r"^(?:0[1-9]|1[0-2])$")
UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def posix(path: Path | str) -> str:
    return Path(path).as_posix()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def source_name(path: Path) -> str:
    name = path.name
    if not name or name.endswith(":"):
        stem = posix(path).replace(":", "").strip("/")
        name = stem.replace("/", "_") or "root"
    return UNSAFE_NAME.sub("_", name)


def split_bundle_pack(relpath: str) -> tuple[str, str, str]:
    """Return (bundle, pack, relpath_from_pack).

    Bundle is the first meaningful folder under the source that is not a
    date dump. Pack is the next one. Unknown → ``_unknown`` / ``_root``.
    """
    parts = Path(relpath).parts
    if not parts:
        return "_unknown", "_root", ""
    dirs = list(parts[:-1])
    filename = parts[-1]
    meaningful: list[str] = []
    prev_year = False
    for part in dirs:
        if part in (".", ".."):
            continue
        skip_date = bool(DATE_DIR.match(part)) or (prev_year and bool(MONTH_DIR.match(part)))
        prev_year = bool(YEAR_DIR.match(part))
        if skip_date:
            continue
        meaningful.append(part)
    if not meaningful:
        return "_unknown", "_root", filename
    bundle = meaningful[0]
    if len(meaningful) == 1:
        return bundle, "_root", filename
    pack = meaningful[1]
    rest = meaningful[2:]
    from_pack = "/".join([*rest, filename]) if rest else filename
    return bundle, pack, from_pack
