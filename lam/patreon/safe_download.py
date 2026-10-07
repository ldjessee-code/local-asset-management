# SPDX-License-Identifier: AGPL-3.0-or-later
"""Download to a temp name, then rename. Creator split archives are not temps.

A file we created while downloading ends in ``.partial``, in legacy
``<name>.<ext>.partN``, or in ``.bad`` / ``.badN``. A creator split is a
separate attachment: ``.001``, ``.partN.rar``, ``.zNN`` plus the ``.zip``,
or a ``Part N`` / ``_ptN`` name. One URL is one file unless the creator
listed those parts.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

TEMP_SUFFIX = ".partial"

# Legacy job temps are ``<name>.<ext>.partN`` (any digit width). ``.partN.rar``
# is a creator volume and must not match.
_PARTIAL_RE = re.compile(r"(?i)^(?P<target>.+)\.partial$")
_LEGACY_PART_RE = re.compile(r"(?i)^(?P<target>.+\.[^.]+)\.part\d+$")
_BAD_RE = re.compile(r"(?i)^(?P<target>.+)\.bad\d*$")
_RAR_PART_RE = re.compile(r"(?i)^(?P<stem>.+)\.part(?P<num>\d+)(?P<ext>\.rar)$")
_ZIP_Z_RE = re.compile(r"(?i)^(?P<stem>.+)\.z(?P<num>\d{2})$")
_NUMERIC_RE = re.compile(r"(?i)^(?P<base>.+)\.(?P<num>\d{3})$")
_NAMED_PAREN_RE = re.compile(
    r"(?i)^(?P<stem>.*?)\s*\(\s*part\s+(?P<num>\d+)\s*\)\s*(?P<ext>\.[^.]+)$"
)
_NAMED_PT_RE = re.compile(r"(?i)^(?P<stem>.*?)_pt(?P<num>\d+)\s*(?P<ext>\.[^.]+)$")
_NAMED_PART_RE = re.compile(
    r"(?i)^(?P<stem>.*?)\s+part\s+(?P<num>\d+)\s*(?P<ext>\.[^.]+)$"
)
_ZIP_LAST_INDEX = 1_000_000


class DownloadSizeError(OSError):
    """The body was empty or a different size than expected."""


@dataclass
class DownloadResult:
    path: Path
    sha256: str
    size: int
    skipped_existing: bool
    resumed: bool


@dataclass(frozen=True)
class SplitPart:
    base: str
    index: int
    scheme: str
    concat: bool


def temp_path_for(dest: Path) -> Path:
    dest = Path(dest)
    return dest.with_name(dest.name + TEMP_SUFFIX)


def our_temp_target(name: str) -> str | None:
    """Final file name for one of our temps, or None when ``name`` is not ours."""
    for pattern in (_PARTIAL_RE, _LEGACY_PART_RE, _BAD_RE):
        match = pattern.match(name)
        if match:
            return match.group("target")
    return None


def is_our_temp(name: str) -> bool:
    return our_temp_target(name) is not None


def split_part_info(name: str) -> SplitPart | None:
    """Creator multi-part attachment, or None for a normal file or our temp.

    The matching ``.zip`` of a ``.zNN`` span is not itself a split name.
    ``group_split_sets`` adds that zip when it is in the same list.
    """
    if is_our_temp(name):
        return None
    rar = _RAR_PART_RE.match(name)
    if rar:
        return SplitPart(
            base=rar.group("stem") + rar.group("ext"),
            index=int(rar.group("num")),
            scheme="rar_part",
            concat=False,
        )
    zipped = _ZIP_Z_RE.match(name)
    if zipped:
        return SplitPart(
            base=zipped.group("stem") + ".zip",
            index=int(zipped.group("num")),
            scheme="zip_z",
            concat=False,
        )
    numeric = _NUMERIC_RE.match(name)
    if numeric:
        return SplitPart(
            base=numeric.group("base"),
            index=int(numeric.group("num")),
            scheme="numeric",
            concat=True,
        )
    for pattern in (_NAMED_PAREN_RE, _NAMED_PT_RE, _NAMED_PART_RE):
        named = pattern.match(name)
        if named:
            stem = named.group("stem").rstrip()
            return SplitPart(
                base=stem + named.group("ext"),
                index=int(named.group("num")),
                scheme="named_part",
                concat=False,
            )
    return None


def group_split_sets(names: Iterable[str]) -> dict[str, list[str]]:
    """Base name to part names, index order. Groups smaller than 2 are dropped."""
    plain: list[str] = []
    buckets: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for name in names:
        if is_our_temp(name):
            continue
        info = split_part_info(name)
        if info is None:
            plain.append(name)
            continue
        buckets.setdefault((info.scheme, info.base), []).append((info.index, name))
    plain_by_lower = {name.lower(): name for name in plain}
    for (scheme, base), rows in buckets.items():
        if scheme != "zip_z":
            continue
        match = plain_by_lower.get(base.lower())
        if match is None:
            continue
        if any(existing == match for _, existing in rows):
            continue
        rows.append((_ZIP_LAST_INDEX, match))
    grouped: dict[str, list[str]] = {}
    for (_scheme, base), rows in buckets.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda item: (item[0], item[1].lower()))
        if base not in grouped:
            grouped[base] = [item[1] for item in rows]
    return grouped


def _leftover_temp_exists(dest: Path) -> bool:
    parent = dest.parent
    if not parent.exists():
        return False
    wanted = dest.name
    try:
        children = list(parent.iterdir())
    except OSError:
        return temp_path_for(dest).exists()
    for child in children:
        if child.is_file() and our_temp_target(child.name) == wanted:
            return True
    return False


def write_atomic(
    dest: Path,
    chunks: Iterable[bytes],
    *,
    expected_size: int | None = None,
) -> DownloadResult:
    """Stream ``chunks`` to ``<dest>.partial``, then ``os.replace`` onto ``dest``.

    Opens the temp with ``wb`` so a leftover temp is overwritten. Never uses
    ``xb``. A size mismatch or an empty body deletes the temp and raises
    ``DownloadSizeError``.
    """
    dest = Path(dest)
    expected: int | None
    if expected_size is None:
        expected = None
    else:
        expected = int(expected_size)
    temp = temp_path_for(dest)
    resumed = _leftover_temp_exists(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with temp.open("wb") as handle:
        for chunk in chunks:
            if not chunk:
                continue
            handle.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if size == 0 or (expected is not None and size != expected):
        temp.unlink(missing_ok=True)
        if size == 0:
            raise DownloadSizeError(f"empty download: {dest.name}")
        raise DownloadSizeError(
            f"size mismatch for {dest.name}: got {size}, expected {expected}"
        )
    os.replace(temp, dest)
    return DownloadResult(
        path=dest,
        sha256=digest.hexdigest(),
        size=size,
        skipped_existing=False,
        resumed=resumed,
    )


def reassemble(parts: list[Path], dest: Path) -> DownloadResult:
    """Concatenate numeric split parts. Other schemes raise ``ValueError``.

    The part files are left in place.
    """
    paths = [Path(part) for part in parts]
    if len(paths) < 2:
        raise ValueError("reassemble needs at least two concat parts")
    for path in paths:
        info = split_part_info(path.name)
        if info is None or not info.concat:
            raise ValueError(f"not a concat part: {path.name}")

    def chunks() -> Iterable[bytes]:
        for path in paths:
            with path.open("rb") as handle:
                while True:
                    block = handle.read(1024 * 1024)
                    if not block:
                        break
                    yield block

    return write_atomic(dest, chunks())


def run_bounded(func, items, *, max_workers: int = 1, min_interval: float = 1.0) -> list:
    """Call ``func`` on each item. At most two run at once. One is the default.

    Results keep input order. A raised exception is stored in that slot.
    Starts are at least ``min_interval`` seconds apart.
    """
    if type(max_workers) is not int or max_workers < 1 or max_workers > 2:
        raise ValueError("max_workers must be 1 or 2")
    queue = list(items)
    results: list = [None] * len(queue)
    if not queue:
        return results
    interval = float(min_interval or 0.0)
    lock = threading.Lock()
    running = 0
    finished = threading.Condition(lock)

    def runner(index: int, item) -> None:
        nonlocal running
        try:
            results[index] = func(item)
        except Exception as exc:
            results[index] = exc
        finally:
            with finished:
                running -= 1
                finished.notify_all()

    threads: list[threading.Thread] = []
    for index, item in enumerate(queue):
        if index > 0 and interval > 0:
            time.sleep(interval)
        with finished:
            while running >= max_workers:
                finished.wait()
            running += 1
        thread = threading.Thread(target=runner, args=(index, item), name=f"lam-download-{index}")
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    return results
