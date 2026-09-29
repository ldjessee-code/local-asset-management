# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bundle America/Indianapolis so Windows can resolve it without the tzdata package.

The IANA key is not shipped with the stdlib on Windows. These bytes are the
US Eastern rules Indiana has followed since 2006 (second Sunday in March,
first Sunday in November), generated here rather than copied from tzdata.
"""

from __future__ import annotations

import struct
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import zoneinfo

_ROOT = Path(tempfile.gettempdir()) / "lam-zoneinfo-v1"
_INSTALLED = False


def _nth_sunday(year: int, month: int, n: int) -> int:
    found = 0
    for day in range(1, 32):
        try:
            current = datetime(year, month, day)
        except ValueError:
            break
        if current.weekday() == 6:
            found += 1
            if found == n:
                return day
    raise RuntimeError(f"no Sunday {n} in {year}-{month}")


def _stamp(year: int, month: int, day: int, hour: int) -> int:
    return int(datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp())


def tzif_bytes() -> bytes:
    """TZif version 2 for America/Indianapolis, transitions 2015–2037 plus a POSIX rule."""
    transitions: list[tuple[int, int]] = []
    for year in range(2015, 2038):
        start = _stamp(year, 3, _nth_sunday(year, 3, 2), 7)  # 02:00 EST -> 07:00 UTC
        end = _stamp(year, 11, _nth_sunday(year, 11, 1), 6)  # 02:00 EDT -> 06:00 UTC
        transitions.append((start, 1))
        transitions.append((end, 0))
    abbr = b"EST\x00EDT\x00"
    types = (
        (-5 * 3600, 0, 0),
        (-4 * 3600, 1, 4),
    )
    timecnt = len(transitions)
    typecnt = len(types)
    counts = (0, 0, 0, timecnt, typecnt, len(abbr))
    header = struct.pack(">4sc15x6I", b"TZif", b"2", *counts)
    indexes = bytes(index for _, index in transitions)
    ttinfo = b"".join(struct.pack(">iBB", offset, isdst, abbr_index) for offset, isdst, abbr_index in types)
    times32 = b"".join(struct.pack(">i", stamp) for stamp, _ in transitions)
    times64 = b"".join(struct.pack(">q", stamp) for stamp, _ in transitions)
    body32 = times32 + indexes + ttinfo + abbr
    body64 = times64 + indexes + ttinfo + abbr
    posix = b"\nEST5EDT,M3.2.0/2,M11.1.0/2\n"
    return header + body32 + header + body64 + posix


def install_zoneinfo() -> None:
    """Put the bundled zone on ``TZPATH`` if it is not there yet."""
    global _INSTALLED
    if _INSTALLED:
        return
    dest = _ROOT / "America" / "Indianapolis"
    if not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = tzif_bytes()
        try:
            with dest.open("xb") as handle:
                handle.write(data)
        except FileExistsError:
            pass
    root = str(_ROOT)
    current = [item for item in zoneinfo.TZPATH if item != root]
    zoneinfo.reset_tzpath(to=[root, *current])
    _INSTALLED = True
