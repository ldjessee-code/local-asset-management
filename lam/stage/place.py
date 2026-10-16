# SPDX-License-Identifier: AGPL-3.0-or-later
"""``lam place``: copy ``out/`` into the target. Never move. Never hard-delete."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from lam.hashing import sha256_full
from lam.oversize.engine import write_verified_zip
from lam.oversize.errors import MissingPyvips, OversizeError
from lam.oversize.images import write_standin_under_bytes
from lam.stage.copying import Sleep, copy_verified, default_recycle, open_undo
from lam.stage.doorbell import ring
from lam.stage.errors import StageError
from lam.stage.job import iso_stamp, load_job, save_job
from lam.stage.lint import lint_relative
from lam.stage.manifest import (
    check_manifest,
    entries_from_plan,
    iter_out_files,
    load_manifest,
    plan_from_manifest,
    plan_from_out,
    save_manifest,
)
from lam.stage.paths import (
    MAP_BYTE_CAP,
    abs_path,
    assert_staging_root_allowed,
    assert_write_target_allowed,
)

Doorbell = Callable[[str], None]
Recycle = Callable[[Path], None]
TEXT_SUFFIXES = {".json", ".csv", ".md", ".txt", ".log"}
MAX_TEXT_BYTES = 1_048_576


def _versioned(dest: Path) -> Path:
    for number in range(2, 1001):
        candidate = dest.with_name(f"{dest.stem}_v{number}{dest.suffix}")
        if not candidate.exists():
            return candidate
    raise StageError(f"no free name near {dest}", code=1)


def _effective_cap(job: dict[str, Any], kind: str, byte_cap: int | None) -> int | None:
    if byte_cap is not None:
        if isinstance(byte_cap, bool) or not isinstance(byte_cap, int) or byte_cap < 1:
            raise StageError("byte-cap must be at least 1")
        return byte_cap
    stored = job.get("byte_cap")
    if isinstance(stored, int) and not isinstance(stored, bool):
        return stored
    if kind == "map":
        return MAP_BYTE_CAP
    return None


def _append(log_path: Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip() + "\n")


def _copy_reports(job_dir: Path, jobs_dir: Path | None) -> None:
    if jobs_dir is None:
        return
    jobs_dir.mkdir(parents=True, exist_ok=True)
    files = [job_dir / "job.json", job_dir / "manifest.json"]
    logs = job_dir / "logs"
    if logs.is_dir():
        files.extend(path for path in logs.rglob("*") if path.is_file())
    for src in files:
        if not src.is_file():
            continue
        if src.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if src.stat().st_size > MAX_TEXT_BYTES:
            continue
        dest = jobs_dir / src.relative_to(job_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())


def _ring(status: str, job: dict[str, Any], doorbell: Doorbell | None, log_path: Path) -> None:
    try:
        if doorbell is not None:
            doorbell(status)
        else:
            ring(status, name=str(job["job"]), requester=str(job["owner"]))
    except Exception as exc:
        _append(log_path, f"doorbell failed: {exc}")


def _finish(
    job_path: Path,
    job: dict[str, Any],
    *,
    status: str,
    code: int,
    jobs_dir: Path | None,
    doorbell: Doorbell | None,
    now: Any,
) -> int:
    job["status"] = status
    if status == "placed":
        job["placed"] = iso_stamp(now)
    save_job(job_path, job)
    job_dir = job_path.parent
    _copy_reports(job_dir, jobs_dir)
    _ring("PASS" if status == "placed" else "FAIL", job, doorbell, job_dir / "logs" / "place.log")
    return code


def _classify(item: dict[str, Any], *, replace: bool, on_conflict: str) -> str:
    dest = Path(item["dest"])
    known = item["sha256"]
    if not dest.exists():
        return "copy"
    if known and dest.is_file() and sha256_full(dest) == known:
        return "dup"
    if replace:
        return "replace"
    if on_conflict == "suffix":
        return "suffix"
    return "conflict"


def _materialize(item: dict[str, Any], job_dir: Path, cap: int) -> None:
    """Write a stand-in and a zip under ``work/``. Does not touch the target."""
    original = Path(item["original"])
    relative = original.relative_to(abs_path(job_dir / "out"))
    if item["role"] == "stand-in":
        work = job_dir / "work" / "standin" / relative
        work.parent.mkdir(parents=True, exist_ok=True)
        write_standin_under_bytes(original, work, max_bytes=cap)
        item["src"] = work
    elif item["role"] == "original-zip":
        work = job_dir / "work" / "zips" / relative.parent / Path(item["dest"]).name
        work.parent.mkdir(parents=True, exist_ok=True)
        write_verified_zip(original, work, sha256_full(original))
        item["src"] = work
    else:
        return
    item["sha256"] = sha256_full(Path(item["src"]))
    item["size"] = Path(item["src"]).stat().st_size


def place_job(
    jobdir: str | Path,
    *,
    dry_run: bool = False,
    strict: bool = False,
    on_conflict: str = "stop",
    replace: bool = False,
    jobs_dir: str | Path | None = None,
    kind: str | None = None,
    byte_cap: int | None = None,
    sleep: Sleep | None = None,
    doorbell: Doorbell | None = None,
    recycle: Recycle | None = None,
    now: Any = None,
) -> int:
    """Copy a staging job into its target. Return 0, 1, 2, or 3."""
    if replace and on_conflict == "suffix":
        raise StageError("pass only one of --replace and --on-conflict=suffix")
    if on_conflict not in {"stop", "suffix"}:
        raise StageError("on-conflict must be stop or suffix")
    job_dir = assert_staging_root_allowed(Path(jobdir))
    job_path = job_dir / "job.json"
    job = load_job(job_path)
    moment = now or datetime.now().astimezone()
    target = assert_write_target_allowed(Path(job["target"]))
    effective_kind = kind or job["kind"]
    cap = _effective_cap(job, effective_kind, byte_cap)
    out = job_dir / "out"
    manifest_path = job_dir / "manifest.json"
    if manifest_path.is_file():
        document = load_manifest(manifest_path)
        check_manifest(document, out, target)
        planned = plan_from_manifest(document)
        have_manifest = True
    else:
        files = iter_out_files(out)
        try:
            planned = plan_from_out(files, out=out, target=target, cap=cap)
        except StageError as exc:
            if exc.code == 2 or dry_run:
                if dry_run and exc.code == 1:
                    print(str(exc), file=sys.stderr)
                    return 1
                raise
            _append(job_dir / "logs" / "place.log", str(exc))
            return _finish(
                job_path, job, status="failed", code=exc.code, jobs_dir=Path(jobs_dir) if jobs_dir else None,
                doorbell=doorbell, now=moment,
            )
        have_manifest = False

    warnings: list[str] = []
    for path in iter_out_files(out):
        relative = path.relative_to(abs_path(out))
        for warning in lint_relative(relative, effective_kind):
            warnings.append(f"{relative}: {warning}")
    if warnings:
        for warning in warnings:
            print(f"lint: {warning}")
    if strict and warnings:
        if dry_run:
            return 1
        lint_path = job_dir / "logs" / "lint.txt"
        lint_path.parent.mkdir(parents=True, exist_ok=True)
        lint_path.write_text("\n".join(warnings) + "\n", encoding="utf-8")
        return _finish(
            job_path, job, status="unplaced", code=1, jobs_dir=Path(jobs_dir) if jobs_dir else None,
            doorbell=doorbell, now=moment,
        )

    for item in planned:
        item["action"] = _classify(item, replace=replace, on_conflict=on_conflict)
        if item["action"] == "suffix":
            item["dest"] = _versioned(Path(item["dest"]))
    conflicts = [item for item in planned if item["action"] == "conflict"]
    if conflicts:
        for item in conflicts:
            print(f"conflict {item['dest']}")
        if dry_run:
            return 1
        conflict_path = job_dir / "logs" / "conflicts.txt"
        conflict_path.parent.mkdir(parents=True, exist_ok=True)
        conflict_path.write_text(
            "\n".join(str(item["dest"]) for item in conflicts) + "\n",
            encoding="utf-8",
        )
        return _finish(
            job_path, job, status="unplaced", code=1, jobs_dir=Path(jobs_dir) if jobs_dir else None,
            doorbell=doorbell, now=moment,
        )

    if dry_run:
        for item in planned:
            print(f"would copy {item['src'] or item['original']} -> {item['dest']}")
        return 0

    report_dir = Path(jobs_dir) if jobs_dir is not None else None
    if not have_manifest:
        try:
            for item in planned:
                if item["sha256"] is None:
                    if cap is None:
                        raise StageError("internal: missing hash without a byte cap", code=1)
                    _materialize(item, job_dir, cap)
        except MissingPyvips as exc:
            _append(job_dir / "logs" / "place.log", str(exc))
            return _finish(
                job_path, job, status="failed", code=3, jobs_dir=report_dir, doorbell=doorbell, now=moment,
            )
        except (OversizeError, StageError, OSError) as exc:
            _append(job_dir / "logs" / "place.log", str(exc))
            code = exc.code if isinstance(exc, (OversizeError, StageError)) else 1
            if code == 2:
                code = 1
            return _finish(
                job_path, job, status="failed", code=code, jobs_dir=report_dir, doorbell=doorbell, now=moment,
            )
        document = {
            "schema": "lam-place-manifest/v1",
            "job": job["job"],
            "target": str(target),
            "created": iso_stamp(moment),
            "entries": entries_from_plan(planned),
        }
        save_manifest(manifest_path, document)

    if sleep is None:
        sleep = time.sleep
    recycler = recycle or default_recycle
    log_path = job_dir / "logs" / "place.log"
    undo_handle = None
    undo_writer = None
    try:
        for item in planned:
            action = item["action"]
            if action == "dup":
                line = f"dup {item['dest']}"
                print(line)
                _append(log_path, line)
                continue
            dest = Path(item["dest"])
            if action == "replace":
                recycler(dest)
                if dest.exists():
                    raise StageError(f"recycle did not remove {dest}", code=1)
            if undo_writer is None:
                undo_handle, undo_writer = open_undo(job_dir / "logs" / "undo.csv")
            copy_verified(Path(item["src"]), dest, sleep=sleep, undo_writer=undo_writer)
            if undo_handle is not None:
                undo_handle.flush()
    except StageError as exc:
        _append(log_path, str(exc))
        return _finish(
            job_path, job, status="failed", code=exc.code, jobs_dir=report_dir, doorbell=doorbell, now=moment,
        )
    except Exception as exc:
        _append(log_path, str(exc))
        return _finish(
            job_path, job, status="failed", code=1, jobs_dir=report_dir, doorbell=doorbell, now=moment,
        )
    finally:
        if undo_handle is not None and not undo_handle.closed:
            undo_handle.close()
    return _finish(
        job_path, job, status="placed", code=0, jobs_dir=report_dir, doorbell=doorbell, now=moment,
    )
