# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pack and unpack zip and tar.gz archives.

Dry-run is the default. ``--apply`` writes. Existing files are never
overwritten and nothing is deleted. A pack verify failure leaves the
sibling temp in place and does not create the final archive.

``manifest.tsv`` sits at the archive root. Each line is
``relpath<TAB>size<TAB>sha256``, sorted by relative path. The manifest
does not list itself, and unpack does not extract that member.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lam.config import ConfigError, find_default_config, load_config, path_is_under
from lam.hashing import sha256_full
from lam.log import finish_run, log_event, log_file_event, start_run

MANIFEST_NAME = "manifest.tsv"
_FORMATS = ("zip", "tar.gz")
_SHA_RE = re.compile(r"[0-9a-fA-F]{64}")
_SIZE_RE = re.compile(r"[0-9]+")
_STAMP_RE = re.compile(r"^\d{8}_\d{6}(_\d+)?$")


class ArchiveError(ValueError):
    """The pack or unpack request cannot be completed."""


@dataclass
class _Outcome:
    code: int = 0
    ok: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass
class _File:
    path: Path
    rel: str
    size: int = 0
    sha256: str = ""


@dataclass
class Member:
    name: str
    rel: str | None
    reason: str | None
    is_dir: bool
    size: int | None = None
    sha256: str | None = None


@dataclass
class _Plan:
    payload: dict = field(default_factory=dict)
    files: list[_File] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)


def _log_dir() -> Path:
    env = os.environ.get("LAM_LOG_DIR", "").strip()
    if env:
        return Path(env)
    return Path(tempfile.gettempdir()) / "lam-archive-logs"


def _optional_config(explicit: str | None):
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ArchiveError(f"config not found: {path}")
        return load_config(path)
    found = find_default_config()
    if found is None:
        return None
    try:
        return load_config(found)
    except ConfigError:
        return None


def _under_git(path: Path) -> bool:
    candidates = [path]
    try:
        candidates.append(path.absolute())
    except OSError:
        pass
    try:
        candidates.append(path.resolve())
    except OSError:
        pass
    for candidate in candidates:
        if any(part.lower() == ".git" for part in Path(candidate).parts):
            return True
    return False


def _is_example_path(path: Path) -> bool:
    """True for ``C:\\Example`` and anything under that directory."""
    try:
        text = os.path.abspath(os.path.normpath(str(path)))
    except OSError:
        text = os.path.normpath(str(path))
    prefix = "\\\\?\\"
    if text.startswith(prefix):
        text = os.path.normpath(text[len(prefix):])
    normalized = os.path.normcase(text)
    root = os.path.normcase(os.path.normpath(r"C:\Example"))
    if normalized == root:
        return True
    return normalized.startswith(root + os.sep)


def _protected_reason(path: Path, cfg) -> str | None:
    if _under_git(path):
        return "refused: path under .git"
    if _is_example_path(path):
        return "refused: example path"
    if cfg is None:
        return None
    if path_is_under(path, cfg.library_root) or path_is_under(path, cfg.quarantine):
        return None
    for root in cfg.protect_paths:
        if path_is_under(path, root):
            return "refused: protect_paths"
    return None


def _present(path: Path) -> bool:
    try:
        return path.is_symlink() or path.exists()
    except OSError:
        return True


def infer_format(path: Path, explicit: str | None = None) -> str:
    """Return ``zip`` or ``tar.gz``. ``explicit`` wins over the file name."""
    if explicit:
        if explicit not in _FORMATS:
            raise ArchiveError(
                f"unsupported format: {explicit}. zip and tar.gz only; 7z is not implemented"
            )
        return explicit
    name = path.name.lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "tar.gz"
    if name.endswith(".zip"):
        return "zip"
    raise ArchiveError(
        f"cannot infer format from {path.name}; use .zip, .tar.gz, or .tgz, "
        "or pass --format. 7z is not implemented"
    )


def _safe_relative(name: str) -> tuple[str | None, str | None]:
    """Return ``(rel, None)`` or ``(None, reason)`` for one archive member name."""
    if not name or not str(name).strip():
        return None, "empty name"
    if "\x00" in name:
        return None, "nul in name"
    raw = str(name).replace("\\", "/")
    if raw.startswith("/"):
        return None, "absolute path"
    if ":" in raw:
        return None, "drive letter"
    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None, "path traversal"
        parts.append(part)
    if not parts:
        return None, "empty name"
    rel = "/".join(parts)
    if any(ch in rel for ch in "\t\r\n"):
        return None, "illegal character in name"
    return rel, None


def _escapes(target: Path, rel: str) -> bool:
    dest = target.joinpath(*rel.split("/"))
    cur = target
    for part in rel.split("/"):
        cur = cur / part
        try:
            if cur.is_symlink():
                return True
        except OSError:
            return True
    if not path_is_under(dest, target):
        return True
    return False


def _exclude_pattern(rel: str, patterns: tuple[str, ...]) -> str | None:
    name = rel.rsplit("/", 1)[-1]
    for pattern in patterns:
        pat = pattern.replace("\\", "/").strip()
        if not pat:
            continue
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat):
            return pattern
        for part in rel.split("/"):
            if fnmatch.fnmatch(part, pat):
                return pattern
    return None


def _is_reparse(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
    except OSError:
        return True
    is_junction = getattr(entry, "is_junction", None)
    if callable(is_junction):
        try:
            if is_junction():
                return True
        except OSError:
            return True
    return False


def _hash_archived_member(handle) -> tuple[int, str]:
    """Stream one archive member. Tests replace this to force a verify miss."""
    digest = hashlib.sha256()
    size = 0
    while True:
        block = handle.read(1024 * 1024)
        if not block:
            break
        size += len(block)
        digest.update(block)
    return size, digest.hexdigest()


def _zip_special_reason(info: zipfile.ZipInfo) -> str | None:
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode):
        return "device"
    return None


def _tar_special_reason(member: tarfile.TarInfo) -> str | None:
    if member.issym():
        return "symlink"
    if member.islnk():
        return "hardlink"
    if member.ischr() or member.isblk() or member.isdev():
        return "device"
    if member.isfifo():
        return "fifo"
    return None


def _mark_duplicates(members: list[Member]) -> None:
    seen: set[str] = set()
    for member in members:
        if member.reason or member.is_dir or not member.rel:
            continue
        if member.rel in seen:
            member.reason = "duplicate member"
        else:
            seen.add(member.rel)


def _read_zip(path: Path) -> list[Member]:
    members: list[Member] = []
    with zipfile.ZipFile(path) as handle:
        for info in handle.infolist():
            rel, why = _safe_relative(info.filename)
            special = _zip_special_reason(info)
            if why or special:
                members.append(Member(info.filename, rel, why or special, info.is_dir()))
                continue
            if info.is_dir():
                members.append(Member(info.filename, rel, None, True))
                continue
            with handle.open(info, "r") as member:
                size, sha = _hash_archived_member(member)
            members.append(Member(info.filename, rel, None, False, size, sha))
    _mark_duplicates(members)
    return members


def _read_tar(path: Path) -> list[Member]:
    members: list[Member] = []
    with tarfile.open(path, "r:gz") as handle:
        for info in handle.getmembers():
            rel, why = _safe_relative(info.name)
            special = _tar_special_reason(info)
            if why or special:
                members.append(Member(info.name, rel, why or special, info.isdir()))
                continue
            if info.isdir():
                members.append(Member(info.name, rel, None, True))
                continue
            if not info.isreg():
                members.append(Member(info.name, rel, f"unsupported member type {info.type!r}", False))
                continue
            extracted = handle.extractfile(info)
            if extracted is None:
                members.append(Member(info.name, rel, "unreadable member", False))
                continue
            with extracted:
                size, sha = _hash_archived_member(extracted)
            members.append(Member(info.name, rel, None, False, size, sha))
    _mark_duplicates(members)
    return members


def _read_members(path: Path, fmt: str) -> list[Member]:
    if fmt == "zip":
        return _read_zip(path)
    if fmt == "tar.gz":
        return _read_tar(path)
    raise ArchiveError(f"unsupported format: {fmt}")


def _read_named_bytes(path: Path, fmt: str, name: str) -> bytes:
    if fmt == "zip":
        with zipfile.ZipFile(path) as handle:
            return handle.read(name)
    with tarfile.open(path, "r:gz") as handle:
        extracted = handle.extractfile(name)
        if extracted is None:
            raise ArchiveError(f"cannot read member {name}")
        with extracted:
            return extracted.read()


def render_manifest(rows: list[tuple[str, int, str]]) -> bytes:
    """Sorted ``relpath<TAB>size<TAB>sha256`` lines. The manifest is not a row."""
    lines = [f"{rel}\t{size}\t{sha}" for rel, size, sha in sorted(rows, key=lambda item: item[0])]
    if not lines:
        return b""
    return ("\n".join(lines) + "\n").encode("utf-8")


def _parse_manifest(blob: bytes) -> tuple[dict[str, tuple[int, str]], list[str]]:
    try:
        text = blob.decode("utf-8")
    except UnicodeError:
        return {}, ["manifest.tsv is not utf-8"]
    rows: dict[str, tuple[int, str]] = {}
    errors: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if line == "":
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            errors.append(f"manifest line {lineno}: expected 3 fields")
            continue
        rel, size_s, sha = parts
        rel_ok, why = _safe_relative(rel)
        if why or rel_ok != rel:
            errors.append(f"manifest line {lineno}: bad path")
            continue
        if not _SIZE_RE.fullmatch(size_s) or not _SHA_RE.fullmatch(sha):
            errors.append(f"manifest line {lineno}: bad size or sha256")
            continue
        if rel in rows:
            errors.append(f"manifest line {lineno}: duplicate path")
            continue
        rows[rel] = (int(size_s), sha.lower())
    return rows, errors


def _verify_written(path: Path, fmt: str, expected: dict[str, tuple[int, str]], *, with_manifest: bool) -> list[str]:
    try:
        members = _read_members(path, fmt)
    except (OSError, zipfile.BadZipFile, tarfile.TarError, ArchiveError) as exc:
        return [f"re-open failed: {exc}"]
    errors = [f"{member.name}: {member.reason}" for member in members if member.reason]
    content = [
        member for member in members
        if not member.is_dir and member.rel and member.rel != MANIFEST_NAME and not member.reason
    ]
    by_rel = {member.rel: member for member in content}
    if with_manifest:
        manifests = [member for member in members if member.rel == MANIFEST_NAME and not member.is_dir and not member.reason]
        if len(manifests) != 1:
            errors.append("missing manifest.tsv")
        else:
            try:
                rows, perr = _parse_manifest(_read_named_bytes(path, fmt, manifests[0].name))
            except (OSError, zipfile.BadZipFile, tarfile.TarError, ArchiveError, KeyError) as exc:
                rows, perr = {}, [f"manifest read failed: {exc}"]
            errors.extend(perr)
            if not perr and rows != expected:
                errors.append("manifest.tsv rows do not match the packed files")
    elif any(member.rel == MANIFEST_NAME and not member.is_dir for member in members):
        errors.append("unexpected manifest.tsv")
    missing = sorted(set(expected) - set(by_rel))
    extra = sorted(set(by_rel) - set(expected))
    if missing:
        errors.append("missing members: " + ", ".join(missing))
    if extra:
        errors.append("unexpected members: " + ", ".join(extra))
    for rel, (size, sha) in expected.items():
        member = by_rel.get(rel)
        if member is None:
            continue
        if member.size != size or member.sha256 != sha:
            errors.append(f"mismatch {rel}")
    return errors


def _walk(root: Path, patterns: tuple[str, ...]) -> tuple[list[_File], list[dict], list[dict]]:
    files: list[_File] = []
    excludes: list[dict] = []
    skipped: list[dict] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            listing = list(os.scandir(current))
        except OSError as exc:
            raise ArchiveError(f"cannot read {current}: {exc}") from exc
        for entry in listing:
            rel = Path(os.path.relpath(entry.path, root)).as_posix()
            if _is_reparse(entry):
                skipped.append({"rel": rel, "reason": "symlink"})
                continue
            pattern = ".git" if entry.name.lower() == ".git" else _exclude_pattern(rel, patterns)
            if pattern is not None:
                excludes.append({"rel": rel, "pattern": pattern})
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError as exc:
                raise ArchiveError(f"cannot stat {entry.path}: {exc}") from exc
            if is_dir:
                stack.append(Path(entry.path))
            elif is_file:
                files.append(_File(Path(entry.path), rel))
    files.sort(key=lambda item: item.rel)
    excludes.sort(key=lambda item: item["rel"])
    skipped.sort(key=lambda item: item["rel"])
    return files, excludes, skipped


def _write_zip(tmp: Path, files: list[_File], manifest: bytes | None) -> None:
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for item in files:
            handle.write(item.path, arcname=item.rel)
        if manifest is not None:
            handle.writestr(MANIFEST_NAME, manifest)


def _write_tar_gz(tmp: Path, files: list[_File], manifest: bytes | None) -> None:
    with tarfile.open(tmp, "w:gz") as handle:
        for item in files:
            info = tarfile.TarInfo(name=item.rel)
            info.size = item.size
            info.mtime = int(item.path.stat().st_mtime)
            info.mode = 0o644
            info.type = tarfile.REGTYPE
            info.uid = 0
            info.gid = 0
            with item.path.open("rb") as source:
                handle.addfile(info, source)
        if manifest is not None:
            info = tarfile.TarInfo(name=MANIFEST_NAME)
            info.size = len(manifest)
            info.mtime = 0
            info.mode = 0o644
            info.type = tarfile.REGTYPE
            handle.addfile(info, io.BytesIO(manifest))


def _publish_new(tmp: Path, dest: Path) -> None:
    """Rename *tmp* onto *dest* only when *dest* is absent. Never deletes either file."""
    if _present(dest):
        raise ArchiveError(f"refusing to replace existing path: {dest}")
    os.rename(tmp, dest)


def _print_payload(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    command = payload.get("command", "")
    print(f"command: {command}")
    print(f"mode: {'apply' if payload.get('apply') else 'dry-run'}")
    print(f"status: {payload.get('status', '')}")
    for key in ("format", "source", "archive", "target", "manifest", "error"):
        if key in payload and payload[key] is not None:
            print(f"{key}: {payload[key]}")
    if "bytes" in payload:
        print(f"bytes: {payload['bytes']}")
    print(f"files: {len(payload.get('files') or [])}")
    for item in payload.get("excludes") or []:
        print(f"exclude: {item['rel']}\t{item['pattern']}")
    for item in payload.get("skipped_links") or []:
        print(f"skip: {item['rel']}\t{item['reason']}")
    for item in payload.get("files") or []:
        if command == "pack":
            print(f"include: {item['rel']}\t{item['size']}\t{item['sha256']}")
        else:
            reason = item.get("reason") or ""
            suffix = f"\t{reason}" if reason else ""
            print(f"file: {item['name']}\t{item['action']}{suffix}")
    for rel in payload.get("extra") or []:
        print(f"extra: {rel}")
    for message in payload.get("errors") or []:
        print(f"error: {message}")


def _finish(run, outcome: _Outcome) -> int:
    finish_run(run, ok=outcome.ok, skipped=outcome.skipped, failed=outcome.failed)
    return outcome.code


def run_pack(
    src_dir: Path,
    archive: Path,
    *,
    fmt: str | None = None,
    exclude: tuple[str, ...] = (),
    apply: bool = False,
    manifest: bool = True,
    as_json: bool = False,
    config_path: str | None = None,
) -> int:
    """Plan or write one archive. Returns a process exit code."""
    run = start_run("pack", _log_dir())
    outcome = _Outcome(code=1, failed=1)
    try:
        outcome = _pack(
            run,
            Path(src_dir),
            Path(archive),
            fmt=fmt,
            exclude=tuple(exclude),
            apply=apply,
            manifest=manifest,
            as_json=as_json,
            config_path=config_path,
        )
    except Exception as exc:
        message = str(exc) if isinstance(exc, ArchiveError) else f"{type(exc).__name__}: {exc}"
        outcome = _Outcome(code=1, failed=1)
        log_event(run, "error", "fail", msg=message)
        print(message, file=sys.stderr)
        if as_json:
            _print_payload(
                {"command": "pack", "apply": apply, "ok": False, "status": "FAIL", "error": message, "files": []},
                as_json=True,
            )
    return _finish(run, outcome)


def _pack(run, src: Path, dest: Path, *, fmt, exclude, apply, manifest, as_json, config_path) -> _Outcome:
    cfg = _optional_config(config_path)
    try:
        archive_format = infer_format(dest, fmt)
    except ArchiveError as exc:
        _emit_pack(run, _fail_payload("pack", apply, src, dest, str(exc)), as_json, code_status="FAIL")
        return _Outcome(code=2, failed=1)
    if not src.exists() or not src.is_dir():
        _emit_pack(run, _fail_payload("pack", apply, src, dest, f"source is not a directory: {src}"), as_json, code_status="FAIL")
        return _Outcome(code=2, failed=1)
    refusal = _protected_reason(dest, cfg)
    if refusal:
        _emit_pack(run, _fail_payload("pack", apply, src, dest, refusal, archive_format), as_json, code_status="FAIL")
        return _Outcome(code=1, failed=1)
    files, excludes, skipped = _walk(src, exclude)
    for item in files:
        rel_ok, why = _safe_relative(item.rel)
        if why or rel_ok != item.rel:
            message = f"unsafe source path {item.rel}: {why or 'not canonical'}"
            _emit_pack(run, _fail_payload("pack", apply, src, dest, message, archive_format), as_json, code_status="FAIL")
            return _Outcome(code=1, failed=1)
        if manifest and item.rel == MANIFEST_NAME:
            message = "manifest.tsv is reserved; rename that source file or pass --no-manifest"
            _emit_pack(run, _fail_payload("pack", apply, src, dest, message, archive_format), as_json, code_status="FAIL")
            return _Outcome(code=1, failed=1)
        item.size = int(item.path.stat().st_size)
        item.sha256 = sha256_full(item.path)
    expected = {item.rel: (item.size, item.sha256) for item in files}
    payload = {
        "command": "pack",
        "apply": bool(apply),
        "ok": True,
        "status": "PASS",
        "format": archive_format,
        "source": str(src),
        "target": str(dest),
        "manifest": bool(manifest),
        "bytes": sum(item.size for item in files),
        "files": [{"rel": item.rel, "size": item.size, "sha256": item.sha256} for item in files],
        "excludes": excludes,
        "skipped_links": skipped,
        "errors": [],
    }
    log_event(run, "info", "plan", **payload)
    for item in files:
        log_file_event(run, "info", "include", item.rel, size=item.size, ok=True, func="lam.archive.run_pack")
    for item in excludes:
        log_file_event(run, "info", "exclude", item["rel"], ok=True, func="lam.archive.run_pack")
    skipped_n = len(excludes) + len(skipped)
    if not apply:
        _print_payload(payload, as_json=as_json)
        return _Outcome(code=0, ok=len(files), skipped=skipped_n, failed=0)
    if _present(dest):
        payload["ok"] = False
        payload["status"] = "FAIL"
        payload["error"] = f"refusing to replace existing archive: {dest}"
        payload["errors"] = [payload["error"]]
        log_event(run, "error", "fail", msg=payload["error"])
        _print_payload(payload, as_json=as_json)
        print(payload["error"], file=sys.stderr)
        return _Outcome(code=1, ok=0, skipped=skipped_n, failed=1)
    if _present(dest.parent) and not dest.parent.is_dir():
        message = f"archive parent is not a directory: {dest.parent}"
        payload["ok"] = False
        payload["status"] = "FAIL"
        payload["error"] = message
        _print_payload(payload, as_json=as_json)
        print(message, file=sys.stderr)
        return _Outcome(code=1, ok=0, skipped=skipped_n, failed=1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.partial")
    manifest_bytes = render_manifest([(item.rel, item.size, item.sha256) for item in files]) if manifest else None
    if archive_format == "zip":
        _write_zip(tmp, files, manifest_bytes)
    else:
        _write_tar_gz(tmp, files, manifest_bytes)
    errors = _verify_written(tmp, archive_format, expected, with_manifest=manifest)
    if errors:
        message = "verify failed: " + "; ".join(errors)
        payload["ok"] = False
        payload["status"] = "FAIL"
        payload["error"] = message
        payload["errors"] = errors
        payload["temp"] = str(tmp)
        log_event(run, "error", "fail", msg=message, temp=str(tmp))
        _print_payload(payload, as_json=as_json)
        print(message, file=sys.stderr)
        print(f"temp kept: {tmp}", file=sys.stderr)
        return _Outcome(code=1, ok=0, skipped=skipped_n, failed=max(1, len(errors)))
    try:
        _publish_new(tmp, dest)
    except ArchiveError as exc:
        payload["ok"] = False
        payload["status"] = "FAIL"
        payload["error"] = str(exc)
        payload["temp"] = str(tmp)
        log_event(run, "error", "fail", msg=str(exc), temp=str(tmp))
        _print_payload(payload, as_json=as_json)
        print(str(exc), file=sys.stderr)
        print(f"temp kept: {tmp}", file=sys.stderr)
        return _Outcome(code=1, ok=0, skipped=skipped_n, failed=1)
    log_event(run, "info", "result", target=str(dest), files=len(files), verified=True)
    _print_payload(payload, as_json=as_json)
    return _Outcome(code=0, ok=len(files), skipped=skipped_n, failed=0)


def _fail_payload(command: str, apply: bool, src: Path, dest: Path, message: str, fmt: str | None = None) -> dict:
    payload = {
        "command": command,
        "apply": bool(apply),
        "ok": False,
        "status": "FAIL",
        "source": str(src),
        "target": str(dest),
        "error": message,
        "errors": [message],
        "files": [],
        "excludes": [],
        "extra": [],
    }
    if fmt:
        payload["format"] = fmt
    return payload


def _emit_pack(run, payload: dict, as_json: bool, *, code_status: str) -> None:
    payload["status"] = code_status
    log_event(run, "error" if code_status == "FAIL" else "info", "plan", **payload)
    _print_payload(payload, as_json=as_json)
    if payload.get("error"):
        print(payload["error"], file=sys.stderr)


def _list_files(root: Path) -> list[str]:
    if not root.exists() or not root.is_dir():
        return []
    found: list[str] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            listing = list(os.scandir(current))
        except OSError:
            continue
        for entry in listing:
            if _is_reparse(entry):
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                stack.append(Path(entry.path))
            elif is_file:
                found.append(Path(os.path.relpath(entry.path, root)).as_posix())
    found.sort()
    return found


def _stamp(target: Path) -> str:
    base = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    stamp = base
    number = 2
    while _present(target / "_prev" / stamp):
        stamp = f"{base}_{number}"
        number += 1
        if not _STAMP_RE.fullmatch(stamp):
            break
    return stamp


def _stream_to_new_file(reader, dest: Path) -> tuple[int, str]:
    """Write *reader* to a sibling temp and rename it onto *dest* when *dest* is absent."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.partial")
    digest = hashlib.sha256()
    size = 0
    with tmp.open("xb") as out:
        while True:
            block = reader.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            digest.update(block)
            out.write(block)
    if _present(dest):
        raise ArchiveError(f"refusing to replace existing path: {dest}; temp kept: {tmp}")
    os.rename(tmp, dest)
    return size, digest.hexdigest()


def _backup_existing(dest: Path, target: Path, rel: str, stamp: str) -> Path:
    backup = target.joinpath("_prev", stamp, *rel.split("/"))
    if not path_is_under(backup, target):
        raise ArchiveError(f"backup escapes target: {backup}")
    if _present(backup):
        raise ArchiveError(f"refusing to replace existing backup: {backup}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.rename(dest, backup)
    return backup


def run_unpack(
    archive: Path,
    target_dir: Path,
    *,
    manifest_path: Path | None = None,
    on_conflict: str = "skip",
    apply: bool = False,
    as_json: bool = False,
    config_path: str | None = None,
) -> int:
    """Plan or extract one archive. Returns a process exit code."""
    run = start_run("unpack", _log_dir())
    outcome = _Outcome(code=1, failed=1)
    try:
        outcome = _unpack(
            run,
            Path(archive),
            Path(target_dir),
            manifest_path=Path(manifest_path) if manifest_path else None,
            on_conflict=on_conflict,
            apply=apply,
            as_json=as_json,
            config_path=config_path,
        )
    except Exception as exc:
        message = str(exc) if isinstance(exc, ArchiveError) else f"{type(exc).__name__}: {exc}"
        outcome = _Outcome(code=1, failed=1)
        log_event(run, "error", "fail", msg=message)
        print(message, file=sys.stderr)
        if as_json:
            _print_payload(
                {
                    "command": "unpack",
                    "apply": apply,
                    "ok": False,
                    "status": "FAIL",
                    "error": message,
                    "files": [],
                    "extra": [],
                },
                as_json=True,
            )
    return _finish(run, outcome)


def _content_members(members: list[Member]) -> list[Member]:
    return [
        member for member in members
        if not member.is_dir and member.rel and member.rel != MANIFEST_NAME and not member.reason
    ]


def _classify(member: Member, target: Path, on_conflict: str) -> dict:
    assert member.rel is not None
    dest = target.joinpath(*member.rel.split("/"))
    if _escapes(target, member.rel):
        return {"name": member.name, "rel": member.rel, "action": "unsafe", "reason": "escapes target"}
    if not _present(dest):
        return {"name": member.name, "rel": member.rel, "action": "new", "reason": ""}
    if dest.is_symlink():
        return {"name": member.name, "rel": member.rel, "action": "unsafe", "reason": "destination is a symlink"}
    if dest.is_dir():
        return {"name": member.name, "rel": member.rel, "action": "unsafe", "reason": "destination is a directory"}
    try:
        existing = sha256_full(dest)
    except OSError as exc:
        return {"name": member.name, "rel": member.rel, "action": "unsafe", "reason": f"cannot read destination: {exc}"}
    if existing == member.sha256 and dest.stat().st_size == member.size:
        return {"name": member.name, "rel": member.rel, "action": "unchanged", "reason": ""}
    if on_conflict == "backup":
        return {"name": member.name, "rel": member.rel, "action": "conflict-backup", "reason": ""}
    return {"name": member.name, "rel": member.rel, "action": "conflict-skip", "reason": ""}


def _unpack(run, archive: Path, target: Path, *, manifest_path, on_conflict, apply, as_json, config_path) -> _Outcome:
    if on_conflict not in {"skip", "backup"}:
        message = "on-conflict must be skip or backup"
        _emit_unpack_fail(run, archive, target, apply, message, as_json)
        return _Outcome(code=2, failed=1)
    cfg = _optional_config(config_path)
    if not archive.is_file():
        _emit_unpack_fail(run, archive, target, apply, f"archive not found: {archive}", as_json)
        return _Outcome(code=2, failed=1)
    try:
        fmt = infer_format(archive, None)
    except ArchiveError as exc:
        _emit_unpack_fail(run, archive, target, apply, str(exc), as_json)
        return _Outcome(code=2, failed=1)
    refusal = _protected_reason(target, cfg)
    if refusal:
        _emit_unpack_fail(run, archive, target, apply, refusal, as_json)
        return _Outcome(code=1, failed=1)
    if _present(target) and not target.is_dir():
        _emit_unpack_fail(run, archive, target, apply, f"target is not a directory: {target}", as_json)
        return _Outcome(code=2, failed=1)
    try:
        members = _read_members(archive, fmt)
    except (OSError, zipfile.BadZipFile, tarfile.TarError, ArchiveError) as exc:
        _emit_unpack_fail(run, archive, target, apply, f"cannot read archive: {exc}", as_json)
        return _Outcome(code=1, failed=1)
    for member in members:
        if member.reason or member.is_dir or not member.rel:
            continue
        if _escapes(target, member.rel):
            member.reason = "escapes target"
    unsafe = [member for member in members if member.reason]
    rows, manifest_label, manifest_errors = _load_unpack_manifest(archive, fmt, members, manifest_path)
    content = _content_members(members)
    actions = []
    for member in members:
        if member.is_dir:
            continue
        if member.rel == MANIFEST_NAME and not member.reason:
            continue
        if member.reason or not member.rel:
            actions.append({"name": member.name, "rel": member.rel or "", "action": "unsafe", "reason": member.reason or "unsafe"})
            continue
        actions.append(_classify(member, target, on_conflict))
    unsafe_actions = [item for item in actions if item["action"] == "unsafe"]
    extra = [rel for rel in _list_files(target) if rel not in {member.rel for member in content}]
    errors = list(manifest_errors)
    if rows is not None and not unsafe:
        by_rel = {member.rel: member for member in content}
        for rel, (size, sha) in rows.items():
            member = by_rel.get(rel)
            if member is None or member.size != size or member.sha256 != sha:
                errors.append(f"manifest mismatch {rel}")
        for member in content:
            if member.rel not in rows:
                errors.append(f"missing from manifest {member.rel}")
    payload = {
        "command": "unpack",
        "apply": bool(apply),
        "ok": not unsafe and not errors and not unsafe_actions,
        "status": "PASS",
        "format": fmt,
        "archive": str(archive),
        "target": str(target),
        "manifest": manifest_label,
        "on_conflict": on_conflict,
        "files": actions,
        "extra": extra,
        "errors": errors,
    }
    if unsafe or unsafe_actions or errors:
        payload["ok"] = False
        payload["status"] = "FAIL"
        log_event(run, "error", "plan", **payload)
        for item in actions:
            log_file_event(
                run,
                "error" if item["action"] == "unsafe" else "info",
                item["action"],
                item["name"],
                ok=item["action"] != "unsafe",
                func="lam.archive.run_unpack",
            )
        _print_payload(payload, as_json=as_json)
        for message in errors:
            print(message, file=sys.stderr)
        if unsafe or unsafe_actions:
            print("unsafe member; wrote nothing", file=sys.stderr)
        failed = max(1, len(unsafe) + len(unsafe_actions) + len(errors))
        return _Outcome(code=1, ok=0, skipped=0, failed=failed)
    log_event(run, "info", "plan", **payload)
    new_n = sum(1 for item in actions if item["action"] in {"new", "unchanged", "conflict-backup"})
    skip_n = sum(1 for item in actions if item["action"] == "conflict-skip")
    if not apply:
        for item in actions:
            log_file_event(run, "info", item["action"], item["name"], ok=True, func="lam.archive.run_unpack")
        _print_payload(payload, as_json=as_json)
        return _Outcome(code=0, ok=new_n, skipped=skip_n, failed=0)
    stamp = _stamp(target)
    written = 0
    try:
        for member in content:
            action = next(item for item in actions if item["rel"] == member.rel)
            dest = target.joinpath(*member.rel.split("/"))
            if action["action"] == "unchanged":
                log_file_event(run, "info", "unchanged", member.rel, size=member.size, ok=True, func="lam.archive.run_unpack")
                continue
            if action["action"] == "conflict-skip":
                log_file_event(run, "info", "conflict-skip", member.rel, size=member.size, ok=True, func="lam.archive.run_unpack")
                continue
            backed_up: Path | None = None
            if action["action"] == "conflict-backup":
                backed_up = _backup_existing(dest, target, member.rel, stamp)
            try:
                with _open_member(archive, fmt, member.name) as reader:
                    size, sha = _stream_to_new_file(reader, dest)
            except (ArchiveError, OSError):
                if backed_up is not None and not _present(dest) and _present(backed_up):
                    try:
                        os.rename(backed_up, dest)
                    except OSError:
                        pass
                raise
            if size != member.size or sha != member.sha256:
                raise ArchiveError(f"extracted hash mismatch: {member.rel}")
            if sha256_full(dest) != member.sha256:
                raise ArchiveError(f"published file hash mismatch: {member.rel}")
            written += 1
            log_file_event(run, "info", action["action"], member.rel, size=size, ok=True, func="lam.archive.run_unpack")
    except (ArchiveError, OSError) as exc:
        payload["ok"] = False
        payload["status"] = "FAIL"
        payload["error"] = str(exc)
        payload["errors"] = [str(exc)]
        log_event(run, "error", "fail", msg=str(exc))
        _print_payload(payload, as_json=as_json)
        print(str(exc), file=sys.stderr)
        return _Outcome(code=1, ok=written, skipped=skip_n, failed=1)
    for member in content:
        action = next(item for item in actions if item["rel"] == member.rel)
        if action["action"] not in {"new", "unchanged", "conflict-backup"}:
            continue
        dest = target.joinpath(*member.rel.split("/"))
        if not dest.is_file() or sha256_full(dest) != member.sha256:
            message = f"verify failed for {member.rel}"
            payload["ok"] = False
            payload["status"] = "FAIL"
            payload["error"] = message
            log_event(run, "error", "fail", msg=message)
            _print_payload(payload, as_json=as_json)
            print(message, file=sys.stderr)
            return _Outcome(code=1, ok=written, skipped=skip_n, failed=1)
    log_event(run, "info", "result", target=str(target), written=written, verified=True)
    _print_payload(payload, as_json=as_json)
    return _Outcome(code=0, ok=new_n, skipped=skip_n, failed=0)


def _emit_unpack_fail(run, archive: Path, target: Path, apply: bool, message: str, as_json: bool) -> None:
    payload = {
        "command": "unpack",
        "apply": bool(apply),
        "ok": False,
        "status": "FAIL",
        "archive": str(archive),
        "target": str(target),
        "error": message,
        "errors": [message],
        "files": [],
        "extra": [],
    }
    log_event(run, "error", "plan", **payload)
    _print_payload(payload, as_json=as_json)
    print(message, file=sys.stderr)


def _load_unpack_manifest(archive: Path, fmt: str, members: list[Member], manifest_path: Path | None):
    if manifest_path is not None:
        if not manifest_path.is_file():
            return None, "external", [f"manifest not found: {manifest_path}"]
        try:
            rows, errors = _parse_manifest(manifest_path.read_bytes())
        except OSError as exc:
            return None, "external", [f"manifest read failed: {exc}"]
        return rows, "external", errors
    embedded = [
        member for member in members
        if member.rel == MANIFEST_NAME and not member.is_dir and not member.reason
    ]
    if not embedded:
        return None, "none", []
    try:
        rows, errors = _parse_manifest(_read_named_bytes(archive, fmt, embedded[0].name))
    except (OSError, zipfile.BadZipFile, tarfile.TarError, ArchiveError, KeyError) as exc:
        return None, "internal", [f"manifest read failed: {exc}"]
    return rows, "internal", errors


class _MemberReader:
    def __init__(self, closer, handle):
        self._closer = closer
        self._handle = handle

    def read(self, size: int) -> bytes:
        return self._handle.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._handle.close()
        finally:
            self._closer()


def _open_member(path: Path, fmt: str, name: str) -> _MemberReader:
    if fmt == "zip":
        handle = zipfile.ZipFile(path)
        try:
            member = handle.open(name, "r")
        except Exception:
            handle.close()
            raise
        return _MemberReader(handle.close, member)
    handle = tarfile.open(path, "r:gz")
    try:
        member = handle.extractfile(name)
    except Exception:
        handle.close()
        raise
    if member is None:
        handle.close()
        raise ArchiveError(f"cannot read member {name}")
    return _MemberReader(handle.close, member)


def add_archive_parsers(sub) -> None:
    pack = sub.add_parser(
        "pack",
        help="Pack a folder into a new zip or tar.gz (dry-run unless --apply; never overwrites)",
    )
    pack.add_argument("src_dir", help="Folder to pack")
    pack.add_argument("archive", help="New archive path (.zip, .tar.gz, or .tgz)")
    pack.add_argument("--format", choices=_FORMATS, default=None, help="zip or tar.gz. Default: infer from the name")
    pack.add_argument("--exclude", action="append", default=None, help="Glob to leave out. Repeatable")
    pack.add_argument("--no-manifest", action="store_true", help="Do not add manifest.tsv")
    pack.add_argument("--apply", action="store_true", help="Write the archive. Default is a dry run")
    pack.add_argument("--json", action="store_true", help="Print the plan as JSON")

    unpack = sub.add_parser(
        "unpack",
        help="Unpack a zip or tar.gz (dry-run unless --apply; never overwrites)",
    )
    unpack.add_argument("archive", help="Archive to read (.zip, .tar.gz, or .tgz)")
    unpack.add_argument("target_dir", help="Folder to extract into")
    unpack.add_argument("--manifest", default=None, help="External manifest.tsv. Default: manifest.tsv inside the archive, if present")
    unpack.add_argument("--on-conflict", choices=("skip", "backup"), default="skip", help="Different existing file: skip (default) or backup under _prev/")
    unpack.add_argument("--apply", action="store_true", help="Extract. Default is a dry run")
    unpack.add_argument("--json", action="store_true", help="Print the plan as JSON")


def run_archive_command(args: argparse.Namespace) -> int:
    if args.cmd == "pack":
        return run_pack(
            Path(args.src_dir),
            Path(args.archive),
            fmt=args.format,
            exclude=tuple(args.exclude or ()),
            apply=bool(args.apply),
            manifest=not bool(args.no_manifest),
            as_json=bool(args.json),
            config_path=args.config,
        )
    if args.cmd == "unpack":
        return run_unpack(
            Path(args.archive),
            Path(args.target_dir),
            manifest_path=Path(args.manifest) if args.manifest else None,
            on_conflict=args.on_conflict,
            apply=bool(args.apply),
            as_json=bool(args.json),
            config_path=args.config,
        )
    return 2
