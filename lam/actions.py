# SPDX-License-Identifier: AGPL-3.0-or-later
"""JSON file-action runner: dry-run by default, ``--apply`` to execute.

Separate from the ``scan → report → plan → apply`` pipeline. Never
overwrites, never permanently deletes. ``recycle`` goes to the Recycle Bin.
A cross-volume file move recycles the verified source the same way.
Paths at or under ``C:\\Example`` are refused.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from lam.hashing import sha256_full
from lam.schema_lite import validate_json
from lam.schemas import registry as schema_registry
from lam.schemas.registry import PLAN_SCHEMA_ID, RESULTS_SCHEMA_ID
OPS = ("move", "copy", "recycle", "mkdir")
UNDO_HEADER = ["action", "source", "destination", "size", "sha256", "timestamp"]
SHA256_LEN = 64

REASON_GIT = "refused: path under .git"
REASON_EXAMPLE = "refused: example path"
REASON_MISSING_SRC = "source does not exist"
REASON_SHA = "sha256 mismatch"
REASON_DST_EXISTS = "destination exists"
REASON_DST_PARENT = "destination parent does not exist"
REASON_SAME = "source and destination are the same path"
REASON_INSIDE = "destination is inside source"
REASON_NOT_V1 = "not supported in v1"
REASON_NO_RECYCLE = "no recycle bin available"
REASON_ALREADY = "already present"
REASON_DST_IS_FILE = "destination is a file"
REASON_STOP = "stopped after earlier error"
REASON_RESTORE_MANUAL = "restore manually from the Recycle Bin"
REASON_DIR_NOT_EMPTY = "directory is not empty"
REASON_CROSS_DIR = "cross-volume directory move not supported in v1"
REASON_CROSS_KEPT = (
    "verified copy exists at the destination and the source was left in place"
)

PrintFn = Callable[[str], None]
RecycleFn = Callable[[Path], None]


class RecycleUnavailable(RuntimeError):
    """Neither send2trash nor a Recycle Bin API is available."""


class PlanError(ValueError):
    """The plan or undo log is invalid as a whole."""


def load_plan_schema() -> dict:
    return schema_registry.PLAN_SCHEMAS[PLAN_SCHEMA_ID].load_document()


def load_results_schema() -> dict:
    return schema_registry.RESULTS_SCHEMAS[RESULTS_SCHEMA_ID].load_document()


def _readable_entry(table: dict, schema_id: str) -> schema_registry.SchemaEntry | None:
    entry = table.get(schema_id)
    if entry is None or entry.status not in {
        schema_registry.STATUS_SUPPORTED,
        schema_registry.STATUS_DEPRECATED,
    }:
        return None
    return entry


def validate_plan_document(data: Any) -> list[str]:
    """Return schema + absolute-path errors. Empty list means the plan is valid.

    An unknown or removed ``schema`` id is a single error whose text is
    ``unsupported plan schema ...`` (the runner raises that string as-is).
    """
    table = schema_registry.PLAN_SCHEMAS
    if isinstance(data, dict) and isinstance(data.get("schema"), str):
        schema_id = data["schema"]
        entry = _readable_entry(table, schema_id)
        if entry is None:
            return [schema_registry.unsupported_message("plan", schema_id, table)]
        schema = entry.load_document()
    else:
        schema = load_plan_schema()
    errors = validate_json(data, schema)
    if errors:
        return errors
    if not isinstance(data, dict):
        return ["$: expected an object"]
    for i, action in enumerate(data.get("actions") or []):
        op = action.get("op")
        if op in {"move", "copy", "recycle"} and "src" in action:
            errors.extend(_absolute_path_error(action["src"], f"$.actions[{i}].src"))
        if op in {"move", "copy", "mkdir"} and "dst" in action:
            errors.extend(_absolute_path_error(action["dst"], f"$.actions[{i}].dst"))
    return errors


def validate_results_document(data: Any) -> list[str]:
    table = schema_registry.RESULTS_SCHEMAS
    if isinstance(data, dict) and isinstance(data.get("schema"), str):
        schema_id = data["schema"]
        entry = _readable_entry(table, schema_id)
        if entry is None:
            return [schema_registry.unsupported_message("results", schema_id, table)]
        return validate_json(data, entry.load_document())
    return validate_json(data, load_results_schema())


def plan_deprecation_warning(data: Any) -> str | None:
    """Warning text when the plan id is deprecated, else None."""
    if not isinstance(data, dict):
        return None
    schema_id = data.get("schema")
    if not isinstance(schema_id, str):
        return None
    entry = schema_registry.PLAN_SCHEMAS.get(schema_id)
    if entry is None or entry.status != schema_registry.STATUS_DEPRECATED:
        return None
    return schema_registry.deprecation_warning(entry)


def local_iso_now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _absolute_path_error(value: str, path: str) -> list[str]:
    if not _is_absolute_string(value):
        return [f"{path}: path must be absolute"]
    return []


def _is_absolute_string(value: str) -> bool:
    p = Path(value)
    if not p.is_absolute():
        return False
    if os.name == "nt" and not p.drive:
        return False
    return True


def _abs(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(str(path))))


def _key(path: Path) -> str:
    return os.path.normcase(str(_abs(path)))


def _under_git(path: Path) -> bool:
    return any(part.lower() == ".git" for part in _abs(path).parts)


def _is_example_path(path: Path) -> bool:
    """True for ``C:\\Example`` and anything under that directory.

    Case-insensitive, on the normalized absolute path, whole path component.
    ``C:\\Examples`` and ``C:\\ExampleFoo`` do not match. A ``\\\\?\\`` prefix
    is stripped so an extended path is the same path.
    """
    text = str(_abs(path))
    prefix = "\\\\?\\"
    if text.startswith(prefix):
        text = os.path.normpath(text[len(prefix):])
    normalized = os.path.normcase(text)
    root = os.path.normcase(os.path.normpath(r"C:\Example"))
    if normalized == root:
        return True
    return normalized.startswith(root + os.sep)


def _hits_example(src: Path | None, dst: Path | None) -> bool:
    if src is not None and _is_example_path(src):
        return True
    if dst is not None and _is_example_path(dst):
        return True
    return False


def _key_is_under(inner: str, outer: str) -> bool:
    if inner == outer:
        return False
    sep = os.sep
    alt = "/" if sep != "/" else "\\"
    return inner.startswith(outer + sep) or inner.startswith(outer + alt)


def _is_under(inner: Path, outer: Path) -> bool:
    return _key_is_under(_key(inner), _key(outer))


def _same_path(a: Path, b: Path) -> bool:
    return _key(a) == _key(b)


def _parent_key(k: str) -> str | None:
    p = Path(k)
    parent = p.parent
    if parent == p:
        return None
    return os.path.normcase(os.path.normpath(str(parent)))


def _same_volume(src: Path, dst: Path) -> bool:
    src_p = _abs(src)
    dst_parent = _abs(dst.parent)
    if os.name == "nt":
        return os.path.normcase(src_p.drive) == os.path.normcase(Path(dst_parent).drive)
    try:
        return src_p.stat().st_dev == Path(dst_parent).stat().st_dev
    except OSError:
        return False


def _hex_sha(value: str) -> str:
    return value.lower()


def _looks_like_sha(value: str | None) -> bool:
    if not value:
        return False
    if len(value) != SHA256_LEN:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in value)


@dataclass
class SimNode:
    kind: str
    size: int | None = None
    sha256: str | None = None


@dataclass
class SimState:
    """Disk plus earlier plan items, so dry-run order matches apply."""

    gone: set[str] = field(default_factory=set)
    nodes: dict[str, SimNode] = field(default_factory=dict)

    def kind_at(self, path: Path) -> str | None:
        node = self.node_at(path)
        return node.kind if node is not None else None

    def node_at(self, path: Path) -> SimNode | None:
        k = _key(path)
        if k in self.nodes:
            return self.nodes[k]
        if self._covered_by_gone(k):
            return None
        try:
            if path.is_dir() and not path.is_symlink():
                return SimNode("dir")
            if path.is_file() or path.is_symlink():
                return SimNode("file")
            if path.exists():
                return SimNode("other")
        except OSError:
            return None
        return None

    def _covered_by_gone(self, k: str) -> bool:
        cur: str | None = k
        while cur:
            if cur in self.nodes:
                return False
            if cur in self.gone:
                return True
            cur = _parent_key(cur)
        return False

    def mark_present(
        self,
        path: Path,
        kind: str,
        size: int | None = None,
        sha256: str | None = None,
    ) -> None:
        k = _key(path)
        self.gone.discard(k)
        self.nodes[k] = SimNode(kind, size=size, sha256=sha256)

    def mark_gone(self, path: Path) -> None:
        k = _key(path)
        self.gone.add(k)
        self.nodes.pop(k, None)
        children = [x for x in list(self.nodes) if _key_is_under(x, k)]
        for child in children:
            self.nodes.pop(child, None)

    def mark_mkdir(self, dst: Path) -> None:
        chain: list[Path] = []
        cur = _abs(dst)
        while True:
            chain.append(cur)
            if cur.parent == cur:
                break
            cur = cur.parent
        for part in reversed(chain):
            existing = self.kind_at(part)
            if existing == "dir":
                continue
            if existing is None:
                self.mark_present(part, "dir")


@dataclass
class Prep:
    ok: bool
    reason: str
    op: str
    src: Path | None
    dst: Path | None
    size: int | None = None
    sha256: str | None = None
    src_kind: str | None = None
    already_present: bool = False
    action_id: str | None = None


def recycle_path(path: Path) -> None:
    """Send *path* to the Recycle Bin. Never permanently deletes as a fallback."""
    try:
        from send2trash import send2trash  # type: ignore[import-not-found]
    except ImportError:
        send2trash = None
    if send2trash is not None:
        send2trash(str(path))
        if path.exists():
            raise OSError("recycle did not remove source")
        return
    if os.name == "nt":
        _recycle_windows(path)
        if path.exists():
            raise OSError("recycle did not remove source")
        return
    raise RecycleUnavailable(REASON_NO_RECYCLE)


def _recycle_windows(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    FO_DELETE = 3
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    src = str(_abs(path))
    from_buf = ctypes.create_unicode_buffer(src + "\0")
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(from_buf, wintypes.LPCWSTR)
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    func = ctypes.windll.shell32.SHFileOperationW
    func.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    func.restype = ctypes.c_int
    result = int(func(ctypes.byref(op)))
    if result != 0 or op.fAnyOperationsAborted:
        raise OSError(f"recycle failed: code {result}")


def _remove_owned_file(path: Path) -> None:
    """Remove a file this runner just created (temp copy, placeholder, or results temp).

    Never call this on a path the user already had. Call sites: failed exclusive
    copy, failed exclusive rename placeholder, abandoned ``.lam-move-*`` temp,
    and abandoned ``.lam-tmp-*`` results file.
    """
    os.remove(path)


class _VerifiedCopyKept(Exception):
    """Verified dest copy exists and the source was not recycled."""

    def __init__(self, reason: str, size: int | None = None, sha256: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.size = size
        self.sha256 = sha256


def _cross_volume_keep_reason(detail: str) -> str:
    detail = detail.strip()
    if not detail:
        return REASON_CROSS_KEPT
    return f"{REASON_CROSS_KEPT}: {detail}"


def _complete_cross_volume_file_move(src: Path, recycle_fn: RecycleFn) -> None:
    """Send a verified cross-volume move source to the Recycle Bin.

    On failure the source is left in place. The caller also leaves the
    verified destination copy in place.
    """
    try:
        recycle_fn(src)
    except RecycleUnavailable as exc:
        raise _VerifiedCopyKept(_cross_volume_keep_reason(str(exc) or REASON_NO_RECYCLE)) from exc
    except OSError as exc:
        raise _VerifiedCopyKept(_cross_volume_keep_reason(str(exc) or "recycle failed")) from exc
    if src.exists():
        raise _VerifiedCopyKept(_cross_volume_keep_reason("recycle did not remove source"))


def _copy_file_exclusive(src: Path, dst: Path) -> str:
    if dst.exists():
        raise FileExistsError(str(dst))
    created = False
    try:
        with src.open("rb") as inf, dst.open("xb") as outf:
            created = True
            while True:
                chunk = inf.read(1024 * 1024)
                if not chunk:
                    break
                outf.write(chunk)
        shutil.copystat(src, dst)
        dest_hash = sha256_full(dst)
        src_hash = sha256_full(src)
        if _hex_sha(dest_hash) != _hex_sha(src_hash):
            raise OSError("copy sha256 mismatch after write")
        return dest_hash
    except Exception:
        if created and dst.is_file():
            try:
                _remove_owned_file(dst)
            except OSError:
                pass
        raise


def _rename_no_overwrite(src: Path, dst: Path) -> None:
    if dst.exists():
        raise FileExistsError(str(dst))
    if os.name == "nt" or src.is_dir():
        os.rename(src, dst)
        return
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(dst, flags)
    os.close(fd)
    try:
        os.rename(src, dst)
    except Exception:
        try:
            _remove_owned_file(dst)
        except OSError:
            pass
        raise


def _move_path(src: Path, dst: Path, recycle_fn: RecycleFn) -> tuple[int | None, str | None]:
    src_is_dir = src.is_dir() and not src.is_symlink()
    size: int | None = None
    digest: str | None = None
    if src_is_dir:
        if not _same_volume(src, dst):
            raise OSError(REASON_CROSS_DIR)
        if dst.exists():
            raise FileExistsError(str(dst))
        _rename_no_overwrite(src, dst)
        return None, None
    size = int(src.stat().st_size)
    digest = sha256_full(src)
    if _same_volume(src, dst):
        _rename_no_overwrite(src, dst)
        return size, digest
    tmp = dst.parent / f".lam-move-{uuid.uuid4().hex[:12]}-{dst.name}"
    tmp_alive = False
    try:
        _copy_file_exclusive(src, tmp)
        tmp_alive = True
        if _hex_sha(sha256_full(tmp)) != _hex_sha(digest):
            raise OSError("cross-volume copy sha256 mismatch")
        if dst.exists():
            raise FileExistsError(str(dst))
        os.rename(tmp, dst)
        tmp_alive = False
        if _hex_sha(sha256_full(dst)) != _hex_sha(digest):
            raise OSError("destination sha256 mismatch after cross-volume move")
        try:
            _complete_cross_volume_file_move(src, recycle_fn)
        except _VerifiedCopyKept as exc:
            raise _VerifiedCopyKept(exc.reason, size, digest) from exc
        return size, digest
    finally:
        if tmp_alive and tmp.exists() and tmp.is_file():
            try:
                _remove_owned_file(tmp)
            except OSError:
                pass


def _file_size(path: Path) -> int | None:
    try:
        if path.is_file():
            return int(path.stat().st_size)
    except OSError:
        return None
    return None


def _precheck(action: dict, state: SimState) -> Prep:
    op = action["op"]
    action_id = action.get("id")
    src = Path(action["src"]) if action.get("src") else None
    dst = Path(action["dst"]) if action.get("dst") else None
    planned_sha = action.get("sha256")

    if _hits_example(src, dst):
        return Prep(False, REASON_EXAMPLE, op, src, dst, action_id=action_id)
    if src is not None and _under_git(src):
        return Prep(False, REASON_GIT, op, src, dst, action_id=action_id)
    if dst is not None and _under_git(dst):
        return Prep(False, REASON_GIT, op, src, dst, action_id=action_id)

    if src is not None and dst is not None:
        if _same_path(src, dst):
            return Prep(False, REASON_SAME, op, src, dst, action_id=action_id)
        if _is_under(dst, src):
            return Prep(False, REASON_INSIDE, op, src, dst, action_id=action_id)

    if op == "mkdir":
        assert dst is not None
        kind = state.kind_at(dst)
        if kind == "dir":
            return Prep(
                True, REASON_ALREADY, op, src, dst, already_present=True, action_id=action_id
            )
        if kind is not None:
            return Prep(False, REASON_DST_IS_FILE, op, src, dst, action_id=action_id)
        ancestor = dst.parent
        while True:
            ak = state.kind_at(ancestor)
            if ak is not None and ak != "dir":
                return Prep(False, "destination parent is a file", op, src, dst, action_id=action_id)
            if ancestor.parent == ancestor:
                break
            ancestor = ancestor.parent
        return Prep(True, "would create directory", op, src, dst, action_id=action_id)

    assert src is not None
    src_kind = state.kind_at(src)
    if src_kind is None:
        return Prep(False, REASON_MISSING_SRC, op, src, dst, action_id=action_id)

    sim = state.nodes.get(_key(src))
    size = sim.size if sim is not None else (_file_size(src) if src_kind == "file" else None)
    digest: str | None = None
    if src_kind == "file":
        try:
            if sim is not None and sim.sha256:
                digest = sim.sha256
            else:
                digest = sha256_full(src)
                size = _file_size(src) if size is None else size
        except OSError as exc:
            return Prep(False, f"hash failed: {exc}", op, src, dst, size=size, action_id=action_id)
        if planned_sha and digest and _hex_sha(planned_sha) != _hex_sha(digest):
            return Prep(
                False, REASON_SHA, op, src, dst, size=size, sha256=digest, src_kind=src_kind, action_id=action_id
            )
    elif planned_sha:
        return Prep(
            False,
            "sha256 not applicable to a directory",
            op,
            src,
            dst,
            src_kind=src_kind,
            action_id=action_id,
        )

    if op == "copy":
        if src_kind != "file":
            return Prep(
                False, REASON_NOT_V1, op, src, dst, size=size, sha256=digest, src_kind=src_kind, action_id=action_id
            )
        assert dst is not None
        if state.kind_at(dst) is not None:
            return Prep(
                False, REASON_DST_EXISTS, op, src, dst, size=size, sha256=digest, src_kind=src_kind, action_id=action_id
            )
        if state.kind_at(dst.parent) != "dir":
            return Prep(
                False, REASON_DST_PARENT, op, src, dst, size=size, sha256=digest, src_kind=src_kind, action_id=action_id
            )
        return Prep(True, "would copy", op, src, dst, size=size, sha256=digest, src_kind=src_kind, action_id=action_id)

    if op == "move":
        assert dst is not None
        if state.kind_at(dst) is not None:
            return Prep(
                False, REASON_DST_EXISTS, op, src, dst, size=size, sha256=digest, src_kind=src_kind, action_id=action_id
            )
        if state.kind_at(dst.parent) != "dir":
            return Prep(
                False, REASON_DST_PARENT, op, src, dst, size=size, sha256=digest, src_kind=src_kind, action_id=action_id
            )
        if src_kind == "dir" and not _same_volume(src, dst):
            return Prep(
                False, REASON_CROSS_DIR, op, src, dst, size=size, sha256=digest, src_kind=src_kind, action_id=action_id
            )
        return Prep(True, "would move", op, src, dst, size=size, sha256=digest, src_kind=src_kind, action_id=action_id)

    if op == "recycle":
        return Prep(True, "would recycle", op, src, dst, size=size, sha256=digest, src_kind=src_kind, action_id=action_id)

    return Prep(False, f"unknown op {op!r}", op, src, dst, action_id=action_id)


def _apply_sim(state: SimState, prep: Prep) -> None:
    if not prep.ok:
        return
    if prep.op == "mkdir" and prep.dst is not None:
        if not prep.already_present:
            state.mark_mkdir(prep.dst)
        else:
            state.mark_present(prep.dst, "dir")
        return
    if prep.op == "copy" and prep.dst is not None:
        state.mark_present(prep.dst, "file", size=prep.size, sha256=prep.sha256)
        return
    if prep.op == "move" and prep.src is not None and prep.dst is not None:
        kind = prep.src_kind or "file"
        state.mark_gone(prep.src)
        state.mark_present(prep.dst, kind, size=prep.size, sha256=prep.sha256)
        return
    if prep.op == "recycle" and prep.src is not None:
        state.mark_gone(prep.src)


@dataclass
class ExecOutcome:
    ok: bool
    reason: str
    size: int | None = None
    sha256: str | None = None
    undo_action: str | None = None


def _execute(prep: Prep, recycle_fn: RecycleFn) -> ExecOutcome:
    try:
        if prep.op == "mkdir":
            assert prep.dst is not None
            if prep.already_present:
                return ExecOutcome(True, REASON_ALREADY)
            if prep.dst.exists() and not prep.dst.is_dir():
                return ExecOutcome(False, REASON_DST_IS_FILE)
            prep.dst.mkdir(parents=True, exist_ok=True)
            if not prep.dst.is_dir():
                return ExecOutcome(False, REASON_DST_IS_FILE)
            return ExecOutcome(True, "created directory")

        if prep.op == "copy":
            assert prep.src is not None and prep.dst is not None
            if prep.dst.exists():
                return ExecOutcome(False, REASON_DST_EXISTS, prep.size, prep.sha256)
            digest = _copy_file_exclusive(prep.src, prep.dst)
            size = _file_size(prep.dst)
            return ExecOutcome(True, "copied", size, digest)

        if prep.op == "move":
            assert prep.src is not None and prep.dst is not None
            if prep.dst.exists():
                return ExecOutcome(False, REASON_DST_EXISTS, prep.size, prep.sha256)
            size, digest = _move_path(prep.src, prep.dst, recycle_fn)
            return ExecOutcome(True, "moved", size, digest)

        if prep.op == "recycle":
            assert prep.src is not None
            try:
                recycle_fn(prep.src)
            except RecycleUnavailable:
                return ExecOutcome(False, REASON_NO_RECYCLE, prep.size, prep.sha256)
            if prep.src.exists():
                return ExecOutcome(False, "recycle did not remove source", prep.size, prep.sha256)
            return ExecOutcome(True, "recycled", prep.size, prep.sha256)
    except _VerifiedCopyKept as exc:
        return ExecOutcome(
            False,
            exc.reason,
            exc.size if exc.size is not None else prep.size,
            exc.sha256 or prep.sha256,
            "copy",
        )
    except FileExistsError:
        return ExecOutcome(False, REASON_DST_EXISTS, prep.size, prep.sha256)
    except RecycleUnavailable:
        return ExecOutcome(False, REASON_NO_RECYCLE, prep.size, prep.sha256)
    except OSError as exc:
        msg = str(exc)
        if msg in {REASON_CROSS_DIR, REASON_NO_RECYCLE}:
            return ExecOutcome(False, msg, prep.size, prep.sha256)
        return ExecOutcome(False, f"{type(exc).__name__}: {exc}", prep.size, prep.sha256)
    return ExecOutcome(False, f"unhandled op {prep.op!r}", prep.size, prep.sha256)


def _item_dict(index: int, prep: Prep, status: str, reason: str) -> dict[str, Any]:
    sha = prep.sha256.lower() if prep.sha256 else None
    return {
        "index": index,
        "id": prep.action_id,
        "op": prep.op,
        "src": str(_abs(prep.src)) if prep.src is not None else None,
        "dst": str(_abs(prep.dst)) if prep.dst is not None else None,
        "status": status,
        "reason": reason,
        "size": prep.size,
        "sha256": sha,
    }


def _summarize(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"ok": 0, "would_ok": 0, "failed": 0, "would_fail": 0, "skipped": 0}
    for item in items:
        key = item["status"]
        if key in counts:
            counts[key] += 1
    counts["total"] = len(items)
    return counts


def _print_item(item: dict[str, Any], printer: PrintFn) -> None:
    src = item.get("src") or ""
    dst = item.get("dst") or ""
    arrow = f"{src} -> {dst}" if src and dst else (src or dst)
    ident = f" id={item['id']}" if item.get("id") else ""
    printer(f"[{item['index']}] {item['op']} {item['status']}  {item['reason']}  {arrow}{ident}")


def _print_summary(summary: dict[str, int], mode: str, printer: PrintFn) -> None:
    printer(
        "summary: "
        f"total={summary['total']} ok={summary['ok']} would_ok={summary['would_ok']} "
        f"failed={summary['failed']} would_fail={summary['would_fail']} "
        f"skipped={summary['skipped']}  mode={mode}"
    )


def _choose_results_path(plan_path: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        path = _abs(explicit)
        if path.exists():
            raise PlanError(f"refusing to overwrite existing results file: {path}")
        return path
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    base = plan_path.with_name(f"{plan_path.stem}.results-{stamp}.json")
    if not base.exists():
        return base
    for n in range(2, 1000):
        cand = plan_path.with_name(f"{plan_path.stem}.results-{stamp}-{n}.json")
        if not cand.exists():
            return cand
    raise PlanError("could not choose a unique results path")


def _choose_undo_path(results_path: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        path = _abs(explicit)
        if path.exists():
            raise PlanError(f"refusing to overwrite existing undo log: {path}")
        return path
    path = results_path.with_suffix(".undo.csv")
    if path.exists():
        raise PlanError(f"refusing to overwrite existing undo log: {path}")
    return path


def _open_undo_log(path: Path) -> tuple[TextIO, csv.writer]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("w", newline="", encoding="utf-8")
    writer = csv.writer(fh, quoting=csv.QUOTE_ALL)
    writer.writerow(UNDO_HEADER)
    fh.flush()
    return fh, writer


def _append_undo(
    writer: csv.writer,
    fh: TextIO,
    action: str,
    source: str,
    destination: str,
    size: int | None,
    sha256: str | None,
) -> None:
    writer.writerow(
        [
            action.upper(),
            source,
            destination,
            "" if size is None else str(size),
            (sha256 or "").upper(),
            local_iso_now(),
        ]
    )
    fh.flush()


def _write_results(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".lam-tmp-{uuid.uuid4().hex[:8]}-{path.name}")
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if path.exists():
            raise PlanError(f"refusing to overwrite existing results file: {path}")
        os.rename(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                _remove_owned_file(tmp)
            except OSError:
                pass
        raise


def _results_body(
    *,
    plan: str,
    mode: str,
    started: str,
    finished: str,
    summary: dict[str, int],
    items: list[dict[str, Any]],
    plan_schema: str | None,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "schema": RESULTS_SCHEMA_ID,
        "plan_schema": plan_schema,
        "warnings": list(warnings),
        "plan": plan,
        "mode": mode,
        "started": started,
        "finished": finished,
        "summary": summary,
        "items": items,
    }


def _load_plan_file(plan_path: Path) -> dict:
    if not plan_path.is_file():
        raise PlanError(f"plan not found: {plan_path}")
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise PlanError(f"plan is not valid JSON: {exc}") from exc
    errors = validate_plan_document(data)
    if errors:
        if len(errors) == 1 and errors[0].startswith("unsupported plan schema "):
            raise PlanError(errors[0])
        raise PlanError("invalid file-action plan:\n  " + "\n  ".join(errors))
    return data


def run_file_actions(
    plan_path: Path,
    *,
    apply: bool = False,
    results_path: Path | None = None,
    undo_log_path: Path | None = None,
    stop_on_error: bool = False,
    recycle_fn: RecycleFn | None = None,
    printer: PrintFn | None = None,
) -> tuple[dict[str, Any], int]:
    """Run a file-action plan. Dry-run unless *apply* is true.

    Returns ``(results, exit_code)`` where 0 = all ok, 1 = any item failed.
    Invalid plans and unsupported or removed schema ids raise ``PlanError``
    (CLI maps that to exit 2). A deprecated schema id runs and is warned.
    """
    plan_path = _abs(plan_path)
    data = _load_plan_file(plan_path)
    schema_warning = plan_deprecation_warning(data)
    warnings = [schema_warning] if schema_warning else []
    plan_schema = data.get("schema") if isinstance(data.get("schema"), str) else None
    mode = "apply" if apply else "dry-run"
    started = local_iso_now()
    out_path = _choose_results_path(plan_path, results_path)
    rec = recycle_fn or recycle_path
    log = printer or print
    if schema_warning:
        log(f"warning: {schema_warning}")
    state = SimState()
    items: list[dict[str, Any]] = []
    undo_fh: TextIO | None = None
    undo_writer: csv.writer | None = None
    undo_path: Path | None = None
    if apply:
        undo_path = _choose_undo_path(out_path, undo_log_path)
        undo_fh, undo_writer = _open_undo_log(undo_path)

    stopping = False
    try:
        for index, action in enumerate(data["actions"]):
            if stopping:
                src = Path(action["src"]) if action.get("src") else None
                dst = Path(action["dst"]) if action.get("dst") else None
                prep = Prep(
                    False,
                    REASON_STOP,
                    action["op"],
                    src,
                    dst,
                    action_id=action.get("id"),
                )
                item = _item_dict(index, prep, "skipped", REASON_STOP)
                items.append(item)
                _print_item(item, log)
                continue
            prep = _precheck(action, state)
            if not prep.ok:
                status = "failed" if apply else "would_fail"
                item = _item_dict(index, prep, status, prep.reason)
                items.append(item)
                _print_item(item, log)
                if stop_on_error:
                    stopping = True
                continue
            if not apply:
                reason = prep.reason
                item = _item_dict(index, prep, "would_ok", reason)
                items.append(item)
                _print_item(item, log)
                _apply_sim(state, prep)
                continue
            outcome = _execute(prep, rec)
            prep.size = outcome.size if outcome.size is not None else prep.size
            prep.sha256 = outcome.sha256 if outcome.sha256 else prep.sha256
            if outcome.ok:
                item = _item_dict(index, prep, "ok", outcome.reason)
                items.append(item)
                _print_item(item, log)
                if undo_writer is not None and undo_fh is not None and not prep.already_present:
                    _append_undo(
                        undo_writer,
                        undo_fh,
                        prep.op,
                        str(_abs(prep.src)) if prep.src is not None else "",
                        str(_abs(prep.dst)) if prep.dst is not None else "",
                        prep.size,
                        prep.sha256,
                    )
                _apply_sim(state, prep)
            else:
                item = _item_dict(index, prep, "failed", outcome.reason)
                items.append(item)
                _print_item(item, log)
                if outcome.undo_action and undo_writer is not None and undo_fh is not None:
                    _append_undo(
                        undo_writer,
                        undo_fh,
                        outcome.undo_action,
                        str(_abs(prep.src)) if prep.src is not None else "",
                        str(_abs(prep.dst)) if prep.dst is not None else "",
                        prep.size,
                        prep.sha256,
                    )
                    if outcome.undo_action == "copy" and prep.dst is not None:
                        state.mark_present(prep.dst, "file", size=prep.size, sha256=prep.sha256)
                if stop_on_error:
                    stopping = True
    finally:
        if undo_fh is not None:
            undo_fh.close()

    finished = local_iso_now()
    summary = _summarize(items)
    results = _results_body(
        plan=str(plan_path),
        mode=mode,
        started=started,
        finished=finished,
        summary=summary,
        items=items,
        plan_schema=plan_schema,
        warnings=warnings,
    )
    _write_results(out_path, results)
    results["_results_path"] = str(out_path)
    if undo_path is not None:
        results["_undo_log_path"] = str(undo_path)
    _print_summary(summary, mode, log)
    log(f"results: {out_path}")
    if undo_path is not None:
        log(f"undo-log: {undo_path}")
    code = 1 if summary["failed"] or summary["would_fail"] else 0
    return results, code


def _read_undo_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise PlanError(f"undo log not found: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise PlanError("undo log is empty") from exc
        normalized = [h.strip().strip('"').lower() for h in header]
        expected = [h.lower() for h in UNDO_HEADER]
        if normalized != expected:
            raise PlanError(f"undo log header must be {UNDO_HEADER}, got {header}")
        rows: list[dict[str, str]] = []
        for raw in reader:
            if not raw or all(not c.strip() for c in raw):
                continue
            while len(raw) < 6:
                raw.append("")
            rows.append(
                {
                    "action": raw[0].strip(),
                    "source": raw[1].strip(),
                    "destination": raw[2].strip(),
                    "size": raw[3].strip(),
                    "sha256": raw[4].strip(),
                    "timestamp": raw[5].strip(),
                }
            )
    return rows


def _parse_undo_size(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def undo_file_actions(
    undo_csv_path: Path,
    *,
    apply: bool = False,
    results_path: Path | None = None,
    recycle_fn: RecycleFn | None = None,
    printer: PrintFn | None = None,
) -> tuple[dict[str, Any], int]:
    """Replay an undo CSV in reverse. Dry-run unless *apply* is true."""
    undo_csv_path = _abs(undo_csv_path)
    rows = _read_undo_csv(undo_csv_path)
    mode = "apply" if apply else "dry-run"
    started = local_iso_now()
    out_path = _choose_results_path(undo_csv_path, results_path)
    rec = recycle_fn or recycle_path
    log = printer or print
    state = SimState()
    items: list[dict[str, Any]] = []

    for index, row in enumerate(reversed(rows)):
        op = row["action"].strip().lower()
        source = Path(row["source"]) if row["source"] else None
        dest = Path(row["destination"]) if row["destination"] else None
        recorded_sha = row["sha256"] or None
        size = _parse_undo_size(row["size"])
        action_id = None
        if op not in OPS:
            prep = Prep(False, f"unknown action {row['action']!r}", op or "move", source, dest)
            status = "failed" if apply else "would_fail"
            item = _item_dict(index, prep, status, prep.reason)
            items.append(item)
            _print_item(item, log)
            continue

        if op == "recycle":
            if _hits_example(source, dest):
                prep = Prep(
                    False,
                    REASON_EXAMPLE,
                    op,
                    source,
                    dest,
                    size=size,
                    sha256=recorded_sha,
                    action_id=action_id,
                )
                status = "failed" if apply else "would_fail"
                item = _item_dict(index, prep, status, REASON_EXAMPLE)
                items.append(item)
                _print_item(item, log)
                continue
            prep = Prep(
                True,
                REASON_RESTORE_MANUAL,
                op,
                source,
                dest,
                size=size,
                sha256=recorded_sha,
                action_id=action_id,
            )
            item = _item_dict(index, prep, "skipped", REASON_RESTORE_MANUAL)
            items.append(item)
            _print_item(item, log)
            continue

        if op == "mkdir":
            prep, status, reason = _undo_mkdir(dest, apply=apply, state=state)
            item = _item_dict(index, prep, status, reason)
            items.append(item)
            _print_item(item, log)
            continue

        if op == "move":
            prep, status, reason = _undo_move(
                source,
                dest,
                recorded_sha,
                size,
                apply=apply,
                state=state,
                recycle_fn=rec,
            )
            item = _item_dict(index, prep, status, reason)
            items.append(item)
            _print_item(item, log)
            continue

        if op == "copy":
            prep, status, reason = _undo_copy(
                source, dest, recorded_sha, size, apply=apply, state=state, recycle_fn=rec
            )
            item = _item_dict(index, prep, status, reason)
            items.append(item)
            _print_item(item, log)
            continue

    finished = local_iso_now()
    summary = _summarize(items)
    results = _results_body(
        plan=str(undo_csv_path),
        mode=mode,
        started=started,
        finished=finished,
        summary=summary,
        items=items,
        plan_schema=None,
        warnings=[],
    )
    _write_results(out_path, results)
    results["_results_path"] = str(out_path)
    _print_summary(summary, mode, log)
    log(f"results: {out_path}")
    code = 1 if summary["failed"] or summary["would_fail"] else 0
    return results, code


def _undo_mkdir(dst: Path | None, *, apply: bool, state: SimState) -> tuple[Prep, str, str]:
    if dst is None:
        prep = Prep(False, "missing destination in undo log", "mkdir", None, None)
        return prep, ("failed" if apply else "would_fail"), prep.reason
    if _is_example_path(dst):
        prep = Prep(False, REASON_EXAMPLE, "mkdir", None, dst)
        return prep, ("failed" if apply else "would_fail"), prep.reason
    if _under_git(dst):
        prep = Prep(False, REASON_GIT, "mkdir", None, dst)
        return prep, ("failed" if apply else "would_fail"), prep.reason
    kind = state.kind_at(dst)
    if kind is None:
        prep = Prep(False, REASON_MISSING_SRC, "mkdir", None, dst)
        return prep, ("failed" if apply else "would_fail"), prep.reason
    if kind != "dir":
        prep = Prep(False, REASON_DST_IS_FILE, "mkdir", None, dst)
        return prep, ("failed" if apply else "would_fail"), prep.reason
    try:
        empty = not any(dst.iterdir()) if dst.is_dir() else True
    except OSError:
        empty = False
    if not empty:
        prep = Prep(True, REASON_DIR_NOT_EMPTY, "mkdir", None, dst)
        return prep, "skipped", REASON_DIR_NOT_EMPTY
    if not apply:
        prep = Prep(True, "would remove empty directory", "mkdir", None, dst)
        state.mark_gone(dst)
        return prep, "would_ok", prep.reason
    try:
        os.rmdir(dst)
    except OSError as exc:
        prep = Prep(False, f"{type(exc).__name__}: {exc}", "mkdir", None, dst)
        return prep, "failed", prep.reason
    prep = Prep(True, "removed empty directory", "mkdir", None, dst)
    state.mark_gone(dst)
    return prep, "ok", prep.reason


def _undo_move(
    source: Path | None,
    dest: Path | None,
    recorded_sha: str | None,
    size: int | None,
    *,
    apply: bool,
    state: SimState,
    recycle_fn: RecycleFn,
) -> tuple[Prep, str, str]:
    if source is None or dest is None:
        prep = Prep(False, "move undo needs source and destination", "move", source, dest)
        return prep, ("failed" if apply else "would_fail"), prep.reason
    # Original MOVE source -> dest. Undo is dest -> source.
    reverse = {"op": "move", "src": str(dest), "dst": str(source)}
    if recorded_sha:
        reverse["sha256"] = recorded_sha
    prep = _precheck(reverse, state)
    prep.size = size if prep.size is None else prep.size
    if not prep.ok:
        status = "failed" if apply else "would_fail"
        return prep, status, prep.reason
    if not apply:
        _apply_sim(state, prep)
        return prep, "would_ok", "would move"
    outcome = _execute(prep, recycle_fn)
    prep.size = outcome.size if outcome.size is not None else prep.size
    prep.sha256 = outcome.sha256 if outcome.sha256 else prep.sha256
    if outcome.ok:
        _apply_sim(state, prep)
        return prep, "ok", outcome.reason
    if outcome.undo_action == "copy" and prep.dst is not None:
        state.mark_present(prep.dst, "file", size=prep.size, sha256=prep.sha256)
    return prep, "failed", outcome.reason


def _undo_copy(
    source: Path | None,
    dest: Path | None,
    recorded_sha: str | None,
    size: int | None,
    *,
    apply: bool,
    state: SimState,
    recycle_fn: RecycleFn,
) -> tuple[Prep, str, str]:
    if dest is None:
        prep = Prep(False, "copy undo needs destination", "copy", source, dest)
        return prep, ("failed" if apply else "would_fail"), prep.reason
    reverse = {"op": "recycle", "src": str(dest)}
    if recorded_sha:
        reverse["sha256"] = recorded_sha
    prep = _precheck(reverse, state)
    prep.size = size if prep.size is None else prep.size
    prep.op = "copy"
    if not prep.ok:
        status = "failed" if apply else "would_fail"
        return prep, status, prep.reason
    if not apply:
        gone = Prep(True, "would recycle", "recycle", dest, None, src_kind=prep.src_kind)
        _apply_sim(state, gone)
        return prep, "would_ok", "would recycle copy"
    rec_prep = Prep(
        True,
        "would recycle",
        "recycle",
        dest,
        None,
        size=prep.size,
        sha256=prep.sha256,
        src_kind=prep.src_kind,
    )
    outcome = _execute(rec_prep, recycle_fn)
    prep.size = outcome.size if outcome.size is not None else prep.size
    prep.sha256 = outcome.sha256 if outcome.sha256 else prep.sha256
    if outcome.ok:
        _apply_sim(state, rec_prep)
        return prep, "ok", "recycled copy"
    return prep, "failed", outcome.reason


def actions_exit_from_plan_error(exc: PlanError, *, printer: PrintFn | None = None) -> int:
    log = printer or (lambda s: print(s, file=sys.stderr))
    log(str(exc))
    return 2
