# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single-file ``lam stage FILE`` and ``lam place STAGED DEST``.

The job commands (``stage new``, ``stage clean``, ``place JOBDIR``) stay in
their own modules. This module prints the shared ``place/v1`` document
(``tool`` is ``lam``).

``os.replace`` onto an existing dest would destroy the old bytes without
recycling them. The old dest is renamed to ``<dest>.~place.old`` first, the
new bytes land on ``dest``, and that holding file is what gets recycled
after the dest sha matches. The backup copy is a second file and is kept.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from lam.hashing import sha256_full
from lam.stage.copying import copy_verified, default_recycle
from lam.stage.errors import StageError
from lam.stage.paths import (
    ILLEGAL,
    abs_path,
    assert_staging_root_allowed,
    assert_write_target_allowed,
    default_staging_root,
    path_key,
    under_git,
)
from lam.token.paths import is_same_or_under

SCHEMA = "place/v1"
TOOL = "lam"
HOLD_SUFFIX = ".~place.old"
Recycle = Callable[[Path], object]


def _doc(
    *,
    status: str,
    dest: str | None = None,
    staged: str | None = None,
    sha256: str | None = None,
    old_sha256: str | None = None,
    backup_path: str | None = None,
    trash_path: str | None = None,
    size: int | None = None,
    message: str = "",
) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "tool": TOOL,
        "status": status,
        "dest": dest,
        "staged": staged,
        "sha256": sha256,
        "old_sha256": old_sha256,
        "backup_path": backup_path,
        "trash_path": trash_path,
        "size": size,
        "message": message,
    }


def _finish(doc: dict[str, object], code: int, json_out: bool) -> int:
    if json_out:
        print(json.dumps(doc, indent=2))
    if code != 0:
        print(str(doc.get("message") or ""), file=sys.stderr)
        return code
    if json_out:
        return 0
    status = doc.get("status")
    if status == "dry-run":
        print(f"would place {doc.get('staged')} -> {doc.get('dest')}")
    elif status == "staged":
        print(doc.get("staged") or "")
    else:
        print(doc.get("dest") or "")
    return 0


def _fail(status: str, message: str, **fields: object) -> StageError:
    err = StageError(message, code=1)
    err.fields = {"status": status, **fields}  # type: ignore[attr-defined]
    return err


def _parse_sha(value: str) -> str:
    text = value.strip().lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise _fail("refused", "refused: bad hex", sha256=None, size=None)
    return text


def _safe_name(name: str) -> str:
    if name in {"", ".", ".."}:
        return "file.bin"
    if any(char in ILLEGAL or ord(char) < 32 for char in name):
        return "file.bin"
    if name.endswith(" ") or name.endswith("."):
        return "file.bin"
    return name


def _file_size(path: Path) -> int | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return path.stat().st_size
    except OSError:
        return None


def _refused_text(exc: StageError) -> str:
    message = str(exc)
    if message.startswith("refused:"):
        return message
    return f"refused: {message}"


def _failed_text(message: str) -> str:
    if message.startswith("failed:"):
        return message
    return f"failed: {message}"


def _guard_root(root: Path) -> Path:
    try:
        checked = assert_staging_root_allowed(root)
    except StageError as exc:
        raise StageError(_refused_text(exc), code=1) from exc
    if under_git(checked):
        raise StageError(f"refused: path under .git: {checked}", code=1)
    return checked


def _guard_write(path: Path) -> Path:
    try:
        return assert_write_target_allowed(path)
    except StageError as exc:
        raise StageError(_refused_text(exc), code=1) from exc


def _holding(dest: Path) -> Path:
    return Path(str(dest) + HOLD_SUFFIX)


def _backup_destination(dest: Path, old_sha: str, backup_dir: Path | None) -> Path:
    folder = backup_dir if backup_dir is not None else dest.parent
    candidate = folder / f"{dest.name}.{old_sha}.bak"
    if not candidate.exists():
        return candidate
    for number in range(2, 1000):
        alt = folder / f"{dest.name}.{old_sha}.{number}.bak"
        if not alt.exists():
            return alt
    raise _fail("failed", "failed: no free backup name")


def _restore(dest: Path, holding: Path) -> None:
    """Put the renamed old file back at ``dest``. Remove only bytes we just wrote."""
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink() or not dest.is_file():
            raise StageError(f"cannot restore {dest}", code=1)
        dest.unlink()
    os.replace(holding, dest)


def _blocked(exc: StageError, *, dest: str, staged: str, source: Path) -> StageError:
    return _fail(
        "refused",
        _refused_text(exc),
        dest=dest,
        staged=staged,
        sha256=None,
        size=_file_size(source),
    )


def stage_file(
    file: str | Path,
    *,
    root: str | Path | None = None,
    json_out: bool = False,
) -> int:
    """Copy ``file`` under the staging root. Print the staged path, or JSON."""
    shown = str(file)
    try:
        source = abs_path(Path(file))
        if source.is_symlink() or not source.is_file():
            raise _fail("refused", f"refused: source is not a file: {source}", sha256=None, size=None)
        staging = _guard_root(abs_path(Path(root)) if root is not None else default_staging_root())
        digest = sha256_full(source)
        target = staging / "single" / digest / _safe_name(source.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path_key(target) != path_key(source):
            shutil.copyfile(source, target)
        if sha256_full(target) != digest:
            raise _fail("failed", f"failed: staged sha256 mismatch: {target}", sha256=None, size=None)
        doc = _doc(
            status="staged",
            staged=str(abs_path(target)),
            sha256=digest,
            size=target.stat().st_size,
            message="staged",
        )
        return _finish(doc, 0, json_out)
    except StageError as exc:
        if exc.code == 2:
            print(str(exc), file=sys.stderr)
            return 2
        extra = getattr(exc, "fields", {}) or {}
        message = str(exc)
        status = extra.get("status")
        if status not in {"refused", "failed"}:
            status = "refused" if message.startswith("refused:") else "failed"
        if status == "refused":
            message = message if message.startswith("refused:") else f"refused: {message}"
        else:
            message = _failed_text(message)
        doc = _doc(
            status=str(status),
            staged=shown,
            sha256=None,
            size=None,
            message=message,
        )
        return _finish(doc, 1, json_out)
    except OSError as exc:
        doc = _doc(status="failed", staged=shown, sha256=None, size=None, message=f"failed: {exc}")
        return _finish(doc, 1, json_out)


def place_file(
    staged: str | Path,
    dest: str | Path,
    *,
    expect_sha256: str | None = None,
    is_new: bool = False,
    dry_run: bool = False,
    backup_dir: str | Path | None = None,
    root: str | Path | None = None,
    grant: str | Path | None = None,
    json_out: bool = False,
    recycle: Recycle | None = None,
) -> int:
    """Copy one staged file onto ``dest``. Recycle the old file only after verify."""
    staged_shown = str(staged)
    dest_shown = str(dest)
    try:
        return _place(
            staged,
            dest,
            expect_sha256=expect_sha256,
            is_new=is_new,
            dry_run=dry_run,
            backup_dir=backup_dir,
            root=root,
            grant=grant,
            json_out=json_out,
            recycle=recycle,
        )
    except StageError as exc:
        if exc.code == 2:
            print(str(exc), file=sys.stderr)
            return 2
        extra = getattr(exc, "fields", {}) or {}
        message = str(exc)
        status = extra.get("status")
        if status not in {"placed", "dry-run", "staged", "conflict", "sha-mismatch", "refused", "failed"}:
            status = "refused" if message.startswith("refused:") else "failed"
        if status == "refused" and not message.startswith("refused:"):
            message = f"refused: {message}"
        if status == "failed":
            message = _failed_text(message)
        sha256 = None
        if status not in {"refused", "conflict", "sha-mismatch"}:
            sha256 = extra.get("sha256") if isinstance(extra.get("sha256"), str) else None
        size = extra.get("size") if isinstance(extra.get("size"), int) else None
        old_sha = extra.get("old_sha256") if isinstance(extra.get("old_sha256"), str) else None
        dest_value = extra.get("dest") if isinstance(extra.get("dest"), str) else dest_shown
        staged_value = extra.get("staged") if isinstance(extra.get("staged"), str) else staged_shown
        backup_value = extra.get("backup_path") if isinstance(extra.get("backup_path"), str) else None
        doc = _doc(
            status=str(status),
            dest=dest_value,
            staged=staged_value,
            sha256=sha256,
            old_sha256=old_sha,
            backup_path=backup_value,
            size=size,
            message=message,
        )
        return _finish(doc, 1, json_out)
    except OSError as exc:
        doc = _doc(
            status="failed",
            dest=dest_shown,
            staged=staged_shown,
            sha256=None,
            size=None,
            message=f"failed: {exc}",
        )
        return _finish(doc, 1, json_out)


def _place(
    staged: str | Path,
    dest: str | Path,
    *,
    expect_sha256: str | None,
    is_new: bool,
    dry_run: bool,
    backup_dir: str | Path | None,
    root: str | Path | None,
    grant: str | Path | None,
    json_out: bool,
    recycle: Recycle | None,
) -> int:
    if is_new and expect_sha256:
        raise StageError("pass only one of --expect-sha256 and --new", code=2)
    if not is_new and not expect_sha256:
        raise StageError("pass --expect-sha256 or --new", code=2)
    expected_old = _parse_sha(expect_sha256) if expect_sha256 else None

    source = abs_path(Path(staged))
    target = abs_path(Path(dest))
    if root is not None:
        try:
            root_path = _guard_root(abs_path(Path(root)))
        except StageError as exc:
            raise _blocked(exc, dest=str(target), staged=str(source), source=source) from exc
        if not is_same_or_under(source, root_path):
            raise _fail(
                "refused",
                "refused: staged file is outside --root",
                dest=str(target),
                staged=str(source),
                sha256=None,
                size=_file_size(source),
            )
    if grant is not None:
        try:
            grant_path = _guard_write(abs_path(Path(grant)))
        except StageError as exc:
            raise _blocked(exc, dest=str(target), staged=str(source), source=source) from exc
        if not is_same_or_under(target, grant_path):
            raise _fail(
                "refused",
                "refused: dest is outside --grant",
                dest=str(target),
                staged=str(source),
                sha256=None,
                size=_file_size(source),
            )
    try:
        target = _guard_write(target)
    except StageError as exc:
        raise _blocked(exc, dest=str(target), staged=str(source), source=source) from exc
    backup_root: Path | None = None
    if backup_dir is not None:
        try:
            backup_root = _guard_write(abs_path(Path(backup_dir)))
        except StageError as exc:
            raise _blocked(exc, dest=str(target), staged=str(source), source=source) from exc

    if source.is_symlink() or not source.is_file():
        raise _fail(
            "refused",
            "refused: missing staged file",
            dest=str(target),
            staged=str(source),
            sha256=None,
            size=None,
        )
    if path_key(source) == path_key(target):
        raise StageError("staged file and dest are the same path", code=2)

    staged_sha = sha256_full(source)
    staged_size = source.stat().st_size
    old_sha: str | None = None
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():
            raise _fail(
                "refused",
                f"refused: dest is not a file: {target}",
                dest=str(target),
                staged=str(source),
                sha256=None,
                size=staged_size,
            )
        old_sha = sha256_full(target)
        if is_new:
            raise _fail(
                "conflict",
                "conflict: dest exists",
                dest=str(target),
                staged=str(source),
                sha256=None,
                old_sha256=old_sha,
                size=staged_size,
            )
        if old_sha != expected_old:
            raise _fail(
                "sha-mismatch",
                "sha-mismatch: dest hash does not match",
                dest=str(target),
                staged=str(source),
                sha256=None,
                old_sha256=old_sha,
                size=staged_size,
            )
    elif not is_new:
        raise _fail(
            "refused",
            "refused: dest does not exist",
            dest=str(target),
            staged=str(source),
            sha256=None,
            size=staged_size,
        )

    if dry_run:
        doc = _doc(
            status="dry-run",
            dest=str(target),
            staged=str(source),
            sha256=staged_sha,
            old_sha256=old_sha,
            size=staged_size,
            message="dry-run",
        )
        return _finish(doc, 0, json_out)

    backup_path: Path | None = None
    holding: Path | None = None
    if old_sha is not None:
        hold = _holding(target)
        if hold.exists():
            raise _fail(
                "refused",
                f"refused: hold path exists: {hold}",
                dest=str(target),
                staged=str(source),
                sha256=None,
                old_sha256=old_sha,
                size=staged_size,
            )
        backup_path = _backup_destination(target, old_sha, backup_root)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target, backup_path)
        if sha256_full(backup_path) != old_sha:
            backup_path.unlink(missing_ok=True)
            raise _fail(
                "failed",
                f"failed: backup sha256 mismatch: {backup_path}",
                dest=str(target),
                staged=str(source),
                sha256=None,
                old_sha256=old_sha,
                backup_path=None,
                size=staged_size,
            )
        os.replace(target, hold)
        holding = hold

    try:
        copy_verified(source, target)
        actual = sha256_full(target)
        if actual != staged_sha:
            raise StageError(
                f"verify failed: dest sha {actual} != staged sha {staged_sha}",
                code=1,
            )
    except Exception as exc:
        restored = False
        if holding is not None and holding.exists():
            _restore(target, holding)
            restored = True
        final_sha = sha256_full(target) if target.is_file() and not target.is_symlink() else None
        final_size = target.stat().st_size if target.is_file() and not target.is_symlink() else None
        detail = f"verify failed: {exc}"
        if restored:
            detail = f"verify failed: dest restored: {exc}"
        doc = _doc(
            status="failed",
            dest=str(target),
            staged=str(source),
            sha256=final_sha,
            old_sha256=old_sha,
            backup_path=str(backup_path) if backup_path is not None else None,
            size=final_size,
            message=_failed_text(detail),
        )
        return _finish(doc, 1, json_out)

    if holding is not None:
        recycler = recycle or default_recycle
        try:
            recycler(holding)
        except Exception as exc:
            doc = _doc(
                status="failed",
                dest=str(target),
                staged=str(source),
                sha256=sha256_full(target),
                old_sha256=old_sha,
                backup_path=str(backup_path) if backup_path is not None else None,
                size=target.stat().st_size,
                message=f"failed: recycle failed: {exc}",
            )
            return _finish(doc, 1, json_out)
        if holding.exists():
            doc = _doc(
                status="failed",
                dest=str(target),
                staged=str(source),
                sha256=sha256_full(target),
                old_sha256=old_sha,
                backup_path=str(backup_path) if backup_path is not None else None,
                size=target.stat().st_size,
                message=f"failed: recycle failed: {holding} still exists",
            )
            return _finish(doc, 1, json_out)

    final = sha256_full(target)
    doc = _doc(
        status="placed",
        dest=str(target),
        staged=str(source),
        sha256=final,
        old_sha256=old_sha,
        backup_path=str(abs_path(backup_path)) if backup_path is not None else None,
        trash_path=None,
        size=target.stat().st_size,
        message="placed",
    )
    return _finish(doc, 0, json_out)
