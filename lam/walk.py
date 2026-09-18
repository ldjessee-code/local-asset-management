# SPDX-License-Identifier: AGPL-3.0-or-later
"""Directory walk via ``os.scandir``. Skips symlinks and excluded names."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path


def walk_files(
    root: Path,
    exclude_names: set[str],
    on_error: Callable[[str, str], None] | None = None,
) -> Iterator[tuple[Path, os.stat_result]]:
    if not root.exists():
        if on_error:
            on_error(str(root), "source path does not exist")
        return
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    name = entry.name
                    if name in exclude_names:
                        continue
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            yield Path(entry.path), entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        if on_error:
                            on_error(entry.path, str(exc))
        except OSError as exc:
            if on_error:
                on_error(str(current), str(exc))
