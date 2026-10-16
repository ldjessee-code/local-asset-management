# SPDX-License-Identifier: AGPL-3.0-or-later
"""Delete staged bytes only after a verified place, and only into the Recycle Bin."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections.abc import Callable

from lam.stage.copying import default_recycle
from lam.stage.errors import StageError
from lam.stage.job import iso_stamp, load_job, save_job
from lam.stage.paths import assert_staging_root_allowed, default_staging_root

KEEP_DAYS = 14
STALE_DAYS = 30
PAYLOAD_DIRS = ("in", "work", "out", "logs")
Recycle = Callable[[Path], None]


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def older_than(stamp: str, now: datetime, days: int) -> bool:
    then = _parse(stamp)
    if then.tzinfo is None and now.tzinfo is not None:
        then = then.replace(tzinfo=now.tzinfo)
    elif now.tzinfo is None and then.tzinfo is not None:
        now = now.replace(tzinfo=then.tzinfo)
    return now - then >= timedelta(days=days)


def clean_staging(
    *,
    root: str | Path | None = None,
    apply: bool = False,
    now: datetime | None = None,
    recycle: Recycle | None = None,
) -> int:
    """Dry-run unless ``apply``. Placed jobs lose payload dirs after 14 days.

    Failed and unplaced jobs are kept. ``--apply`` sets ``stale`` after 30 days.
    ``new`` jobs are left alone. ``work`` is included so stand-ins do not remain.
    """
    staging = assert_staging_root_allowed(Path(root) if root is not None else default_staging_root())
    moment = now or datetime.now().astimezone()
    recycler = recycle or default_recycle
    code = 0
    if not staging.is_dir():
        return 0
    for owner_dir in sorted(staging.iterdir(), key=lambda item: item.name):
        if not owner_dir.is_dir() or owner_dir.is_symlink():
            continue
        for job_dir in sorted(owner_dir.iterdir(), key=lambda item: item.name):
            if not job_dir.is_dir() or job_dir.is_symlink():
                continue
            path = job_dir / "job.json"
            if not path.is_file():
                continue
            try:
                job = load_job(path)
            except StageError as exc:
                print(str(exc), file=sys.stderr)
                continue
            status = job.get("status")
            if status == "placed":
                placed = job.get("placed")
                if not placed:
                    print(f"placed job missing timestamp: {path}", file=sys.stderr)
                    code = 2
                    continue
                if job.get("cleaned"):
                    continue
                if not older_than(placed, moment, KEEP_DAYS):
                    continue
                if not apply:
                    print(f"would recycle {job_dir}")
                    continue
                try:
                    for name in PAYLOAD_DIRS:
                        folder = job_dir / name
                        if folder.exists():
                            recycler(folder)
                    job["cleaned"] = iso_stamp(moment)
                    save_job(path, job)
                except Exception as exc:
                    print(str(exc), file=sys.stderr)
                    return 1
                continue
            if status in {"failed", "unplaced"}:
                created = job.get("created")
                if not created or not older_than(created, moment, STALE_DAYS):
                    continue
                if job.get("stale") is True:
                    continue
                if not apply:
                    print(f"would flag stale {job_dir}")
                    continue
                job["stale"] = True
                save_job(path, job)
    return code
