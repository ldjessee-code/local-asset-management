# SPDX-License-Identifier: AGPL-3.0-or-later
"""Copy by temp file, rename with lock retry, then verify the SHA-256."""

from __future__ import annotations

import csv
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from lam.actions import UNDO_HEADER, local_iso_now
from lam.hashing import sha256_full
from lam.stage.errors import StageError

TEMP_SUFFIX = ".~place.tmp"
BACKOFF_SECONDS = (1, 2, 4, 8, 16)
Sleep = Callable[[float], None]


def default_recycle(path: Path) -> None:
    """Recycle Bin via ``lam.actions.recycle_path``, looked up at call time."""
    from lam.actions import recycle_path

    recycle_path(path)


def _replace(src: Path, dest: Path) -> None:
    """Indirection so tests can fail the rename without patching every ``os.replace``."""
    os.replace(src, dest)


def is_lock_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) == 5


def _unlink_temp(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def copy_verified(
    src: Path,
    dest: Path,
    *,
    sleep: Sleep | None = None,
    undo_writer: csv.writer | None = None,
    attempts: int = 6,
) -> str:
    """Copy ``src`` to ``dest``. Leave ``src`` in place.

    The bytes land in ``<dest>.~place.tmp``, then ``os.replace`` retries on
    ``PermissionError`` or winerror 5. Sleeps are 1, 2, 4, 8, 16 seconds
    after the first five failures. ``sleep`` is injectable.
    """
    if sleep is None:
        sleep = time.sleep
    src = Path(src)
    dest = Path(dest)
    if not src.is_file():
        raise StageError(f"source does not exist: {src}", code=1)
    expected = sha256_full(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(dest) + TEMP_SUFFIX)
    _unlink_temp(tmp)
    shutil.copyfile(src, tmp)
    try:
        if sha256_full(tmp) != expected:
            raise StageError(f"temp sha256 mismatch: {tmp}", code=1)
        replaced = False
        last: OSError | None = None
        for attempt in range(attempts):
            try:
                _replace(tmp, dest)
                replaced = True
                break
            except OSError as exc:
                last = exc
                if not is_lock_error(exc) or attempt == attempts - 1:
                    raise StageError(f"rename failed: {exc}", code=1) from exc
                sleep(BACKOFF_SECONDS[attempt])
        if not replaced:
            raise StageError(f"rename failed: {last}", code=1)
    finally:
        _unlink_temp(tmp)
    if sha256_full(dest) != expected:
        raise StageError(f"dest sha256 mismatch: {dest}", code=1)
    if undo_writer is not None:
        undo_writer.writerow(
            [
                "COPY",
                str(src),
                str(dest),
                str(src.stat().st_size),
                expected.upper(),
                local_iso_now(),
            ]
        )
    return expected


def open_undo(path: Path) -> tuple[object, csv.writer]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", newline="", encoding="utf-8")
    writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
    writer.writerow(UNDO_HEADER)
    handle.flush()
    return handle, writer
