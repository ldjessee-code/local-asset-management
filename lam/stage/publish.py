# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lam publish``: one live file, same temp-rename-verify step as place."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from lam.hashing import sha256_full
from lam.stage.copying import Sleep, copy_verified, default_recycle, open_undo
from lam.stage.errors import StageError
from lam.stage.paths import assert_write_target_allowed, path_key

Recycle = Callable[[Path], None]


def _versioned(dest: Path) -> Path:
    for number in range(2, 1001):
        candidate = dest.with_name(f"{dest.stem}_v{number}{dest.suffix}")
        if not candidate.exists():
            return candidate
    raise StageError(f"no free name near {dest}", code=1)


def publish_file(
    src: str | Path,
    dest: str | Path,
    *,
    dry_run: bool = False,
    on_conflict: str = "stop",
    replace: bool = False,
    sleep: Sleep | None = None,
    undo_log: str | Path | None = None,
    recycle: Recycle | None = None,
) -> int:
    """Copy ``src`` to ``dest``. Dry-run writes nothing, including the undo CSV."""
    if replace and on_conflict == "suffix":
        raise StageError("pass only one of --replace and --on-conflict=suffix")
    source = Path(src)
    target = assert_write_target_allowed(Path(dest))
    if not source.is_file():
        raise StageError(f"source does not exist: {source}")
    if path_key(source) == path_key(target):
        raise StageError("source and destination are the same path")
    digest = sha256_full(source)
    action = "copy"
    final = target
    if target.exists():
        if target.is_file() and sha256_full(target) == digest:
            print(f"dup {target}")
            return 0
        if replace:
            action = "replace"
        elif on_conflict == "suffix":
            action = "suffix"
            final = _versioned(target)
        else:
            print(f"conflict {target}")
            return 1
    if dry_run:
        print(f"would copy {source} -> {final}")
        return 0
    if sleep is None:
        sleep = time.sleep
    recycler = recycle or default_recycle
    if action == "replace":
        recycler(target)
        if target.exists():
            raise StageError(f"recycle did not remove {target}", code=1)
    undo_path = Path(undo_log) if undo_log is not None else Path(str(target) + ".publish-undo.csv")
    handle, writer = open_undo(undo_path)
    try:
        copy_verified(source, final, sleep=sleep, undo_writer=writer)
        handle.flush()
    finally:
        handle.close()
    print(f"published {final}")
    return 0
