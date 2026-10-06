# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scan a folder for oversize images, record them, and optionally proxy them.

Dry-run is the default. ``--apply`` writes a proxy. A zip of the original is
recycled only after the zip member's SHA-256 matches the scan hash. Nothing
in this module calls ``os.remove`` or ``Path.unlink`` on an original, a proxy,
or a zip.
"""

from __future__ import annotations

import gc
import hashlib
import os
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lam.hashing import sha256_full
from lam.oversize.classify import (
    area_percent,
    evidence_text,
    extra_marker,
    is_inside,
    normalize_tokens,
    parts_found,
    path_says_combined,
    set_directory,
    should_zip,
)
from lam.oversize.errors import OversizeError, ZipVerifyError
from lam.oversize.images import (
    IMAGE_EXTENSIONS,
    proxy_extension,
    read_dimensions,
    require_pyvips,
    write_proxy,
)
from lam.oversize.register import (
    FLAG,
    empty_register,
    load_register,
    resolve_register_path,
    save_register,
)

ZIP_MODES = ("combined-with-parts", "combined", "all", "none")
RecycleFn = Callable[[Path], None]


@dataclass
class Seen:
    path: Path
    size: int
    width: int
    height: int
    sha256: str = ""

    @property
    def pixels(self) -> int:
        return self.width * self.height


def _default_recycle(path: Path) -> None:
    from lam import actions

    actions.recycle_path(path)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _norm(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


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


def iter_files(root: Path):
    """Yield files under *root*. Skip ``.git``, symlinks, and junctions."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            iterator = os.scandir(current)
        except OSError:
            continue
        with iterator as listing:
            entries = list(listing)
        for entry in entries:
            if entry.name.lower() == ".git":
                continue
            try:
                if _is_reparse(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path), entry.stat(follow_symlinks=False)
            except OSError:
                continue


def _is_proxy_name(path: Path, suffix: str) -> bool:
    return path.stem.lower().endswith(suffix.lower())


def _check_args(
    *,
    min_mb: float,
    min_mp: float,
    max_dim: int,
    set_depth: int,
    quality: int,
    proxy_suffix: str,
    zip_mode: str,
) -> None:
    if min_mb < 0 or min_mp < 0:
        raise OversizeError("thresholds must be 0 or more")
    if max_dim < 1:
        raise OversizeError("--max-dim must be at least 1")
    if set_depth < 0:
        raise OversizeError("--set-depth must be 0 or more")
    if not 1 <= int(quality) <= 100:
        raise OversizeError("--quality must be from 1 to 100")
    if not proxy_suffix or any(sep in proxy_suffix for sep in "/\\"):
        raise OversizeError("--proxy-suffix must be a non-empty name fragment")
    if zip_mode not in ZIP_MODES:
        raise OversizeError(f"--zip must be one of: {', '.join(ZIP_MODES)}")


def _over_threshold(size: int, pixels: int, min_mb: float, min_mp: float) -> bool:
    return size > min_mb * 1024 * 1024 or pixels > min_mp * 1_000_000


def _proxy_names(document: dict) -> set[str]:
    names: set[str] = set()
    for entry in document.get("entries", []):
        raw = entry.get("proxy_path")
        if raw:
            names.add(_norm(Path(raw)))
    return names


def _blank_work() -> dict:
    return {
        "proxy_path": None,
        "proxy_width": None,
        "proxy_height": None,
        "zip_path": None,
        "zip_verified": False,
        "original_recycled": False,
        "error": None,
    }


def _observation(item: Seen, *, combined: bool, found: str, evidence: str, part_paths: list[str], area: int, now: str) -> dict:
    return {
        "original_path": str(item.path.resolve()),
        "sha256": item.sha256,
        "bytes": item.size,
        "mb": round(item.size / (1024 * 1024), 3),
        "width": item.width,
        "height": item.height,
        "megapixels": round(item.pixels / 1_000_000, 3),
        "combined": combined,
        "combined_evidence": evidence,
        "parts_found": found,
        "part_paths": part_paths,
        "parts_area_pct": area,
        "flag": FLAG,
        "status": "planned",
        "first_seen": now,
        "updated": now,
        "paths_seen": [str(item.path.resolve())],
        **_blank_work(),
    }


def _outputs_present(entry: dict, zip_needed: bool) -> bool:
    if entry.get("status") != "done":
        return False
    proxy = entry.get("proxy_path")
    if not proxy or not Path(proxy).is_file():
        return False
    if not zip_needed:
        return True
    zip_path = entry.get("zip_path")
    if not zip_path or not Path(zip_path).is_file():
        return False
    if not entry.get("zip_verified"):
        return False
    return bool(entry.get("original_recycled"))


def _merge(old: dict | None, fresh: dict, now: str) -> dict:
    if old is None:
        return fresh
    paths = list(old["paths_seen"])
    if fresh["original_path"] not in paths:
        paths.append(fresh["original_path"])
    merged = dict(fresh)
    merged["paths_seen"] = paths
    merged["first_seen"] = old["first_seen"]
    for key in ("proxy_path", "proxy_width", "proxy_height", "zip_path", "zip_verified", "original_recycled"):
        merged[key] = old[key]
    merged["status"] = old["status"]
    merged["error"] = old["error"]
    merged["updated"] = now
    return merged


def _allocate(preferred: Path, keep: Path | None) -> Path:
    if keep is not None and keep.is_file():
        return keep
    if not preferred.exists():
        return preferred
    number = 2
    while number <= 1000:
        candidate = preferred.with_name(f"{preferred.stem}_{number}{preferred.suffix}")
        if not candidate.exists():
            return candidate
        number += 1
    raise OversizeError(f"no free name near {preferred}")


def _write_zip(src: Path, dest: Path) -> None:
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as handle:
        handle.write(src, arcname=src.name)


def verify_zip(zip_path: Path, original: Path, expected_sha: str) -> None:
    """Reopen the zip, run ``testzip()``, and stream the member's SHA-256."""
    with zipfile.ZipFile(zip_path, "r") as handle:
        bad = handle.testzip()
        if bad is not None:
            raise ZipVerifyError(f"zip verify failed: {bad}")
        names = handle.namelist()
        if names != [original.name]:
            raise ZipVerifyError(f"zip member mismatch: {names}")
        digest = hashlib.sha256()
        with handle.open(original.name, "r") as member:
            while True:
                block = member.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        if digest.hexdigest() != expected_sha:
            raise ZipVerifyError("zip sha256 mismatch")


def _apply_one(entry: dict, item: Seen, *, max_dim: int, quality: int, proxy_suffix: str, zip_needed: bool, recycle: RecycleFn) -> None:
    keep_proxy = Path(entry["proxy_path"]) if entry.get("proxy_path") else None
    preferred_proxy = item.path.with_name(item.path.stem + proxy_suffix + proxy_extension(item.path))
    proxy_path = _allocate(preferred_proxy, keep_proxy if keep_proxy and keep_proxy.is_file() else None)
    if proxy_path.is_file() and keep_proxy is not None and _norm(proxy_path) == _norm(keep_proxy):
        width, height = read_dimensions(proxy_path)
    else:
        width, height = write_proxy(item.path, proxy_path, max_dim=max_dim, quality=quality)
    entry["proxy_path"] = str(proxy_path.resolve())
    entry["proxy_width"] = width
    entry["proxy_height"] = height
    if not zip_needed:
        entry["status"] = "done"
        entry["error"] = None
        return
    keep_zip = None
    if entry.get("zip_verified") and entry.get("zip_path"):
        keep_zip = Path(entry["zip_path"])
    preferred_zip = item.path.with_name(item.path.name + ".zip")
    zip_path = _allocate(preferred_zip, keep_zip if keep_zip and keep_zip.is_file() else None)
    # Drop libvips file handles before we read the original again on Windows.
    gc.collect()
    if not zip_path.is_file():
        _write_zip(item.path, zip_path)
    verify_zip(zip_path, item.path, item.sha256)
    entry["zip_path"] = str(zip_path.resolve())
    entry["zip_verified"] = True
    recycle(item.path)
    if item.path.exists():
        raise OversizeError("recycle did not remove source")
    entry["original_recycled"] = True
    entry["status"] = "done"
    entry["error"] = None


def _action(zip_needed: bool) -> str:
    if zip_needed:
        return "proxy, zip original, recycle original after verify"
    return "proxy, keep original"


def run_oversize_scan(
    folder: str | Path,
    *,
    min_mb: float = 100,
    min_mp: float = 89,
    max_dim: int = 8000,
    set_depth: int = 1,
    register: str | Path | None = None,
    record: bool = False,
    apply: bool = False,
    zip_mode: str = "combined-with-parts",
    proxy_suffix: str = "_max8000",
    quality: int = 90,
    recycle: RecycleFn | None = None,
) -> tuple[dict, int]:
    """Walk *folder*. Write the register only when *record* or *apply* is set."""
    _check_args(
        min_mb=min_mb,
        min_mp=min_mp,
        max_dim=max_dim,
        set_depth=set_depth,
        quality=quality,
        proxy_suffix=proxy_suffix,
        zip_mode=zip_mode,
    )
    root = Path(folder)
    if not root.is_dir():
        raise OversizeError(f"folder not found: {root}")
    if apply:
        record = True
    reg_path = resolve_register_path(register)
    document = load_register(reg_path) if reg_path.exists() else empty_register()
    require_pyvips()
    recycler = recycle if recycle is not None else _default_recycle
    known_proxies = _proxy_names(document)
    by_sha = {entry["sha256"]: entry for entry in document["entries"]}

    candidates: list[Seen] = []
    oversize: list[Seen] = []
    for path, stat in iter_files(root):
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if _is_proxy_name(path, proxy_suffix):
            continue
        if _norm(path) in known_proxies:
            continue
        try:
            width, height = read_dimensions(path)
        except Exception:
            continue
        seen = Seen(path=path, size=int(stat.st_size), width=width, height=height)
        candidates.append(seen)
        if _over_threshold(seen.size, seen.pixels, min_mb, min_mp):
            seen.sha256 = sha256_full(path)
            oversize.append(seen)

    now = _now()
    rows: list[dict] = []
    touched: set[str] = set()
    error_count = 0
    for item in sorted(oversize, key=lambda row: str(row.path).casefold()):
        home = set_directory(item.path, root, set_depth)
        overview_tokens = normalize_tokens(item.path.stem)
        matched: list[tuple[Seen, str]] = []
        for other in candidates:
            if other.path.resolve() == item.path.resolve():
                continue
            if not is_inside(other.path, home):
                continue
            if other.pixels >= item.pixels:
                continue
            marker = extra_marker(overview_tokens, normalize_tokens(other.path.stem))
            if marker is None:
                continue
            matched.append((other, marker))
        matched.sort(key=lambda pair: str(pair[0].path).casefold())
        markers = [marker for _other, marker in matched]
        part_paths = [str(other.path.resolve()) for other, _marker in matched]
        raw_area, area = area_percent(sum(other.pixels for other, _marker in matched), item.pixels)
        found = parts_found(markers, raw_area)
        name_combined = path_says_combined(item.path, root)
        combined = name_combined or bool(matched)
        evidence = evidence_text(
            name_combined=name_combined,
            count=len(matched),
            markers=markers,
            area=area,
            status=found,
        )
        fresh = _observation(
            item,
            combined=combined,
            found=found,
            evidence=evidence,
            part_paths=part_paths,
            area=area,
            now=now,
        )
        zip_needed = should_zip(zip_mode, combined, found)
        old = by_sha.get(item.sha256)
        if old is not None and item.sha256 in touched:
            paths = list(old["paths_seen"])
            if fresh["original_path"] not in paths:
                paths.append(fresh["original_path"])
            old["paths_seen"] = paths
            old["original_path"] = fresh["original_path"]
            old["updated"] = now
            entry = old
        else:
            entry = _merge(old, fresh, now)
            if old is not None and _outputs_present(old, zip_needed):
                entry["status"] = "done"
                entry["error"] = None
            elif apply:
                entry["status"] = "planned"
                entry["error"] = None
            by_sha[item.sha256] = entry
            touched.add(item.sha256)
        if apply and not _outputs_present(entry, zip_needed):
            try:
                _apply_one(
                    entry,
                    item,
                    max_dim=max_dim,
                    quality=quality,
                    proxy_suffix=proxy_suffix,
                    zip_needed=zip_needed,
                    recycle=recycler,
                )
            except Exception as exc:
                entry["status"] = "error"
                entry["error"] = f"{type(exc).__name__}: {exc}"
                error_count += 1
        public = dict(entry)
        public["action"] = _action(zip_needed)
        rows.append(public)

    if record:
        kept = [entry for sha, entry in by_sha.items() if sha not in touched]
        entries = list(by_sha[sha] for sha in touched) + kept
        entries.sort(key=lambda entry: entry["original_path"].casefold())
        save_register(reg_path, {"schema": document["schema"], "entries": entries})

    mode = "apply" if apply else "record" if record else "dry-run"
    payload = {
        "mode": mode,
        "folder": str(root.resolve()),
        "register": str(reg_path),
        "count": len(rows),
        "errors": error_count,
        "files": rows,
    }
    code = 4 if apply and error_count else 0
    return payload, code


def format_table(payload: dict) -> str:
    files = payload["files"]
    if not files:
        return "no oversize images\n"
    lines = []
    for item in files:
        lines.append(
            f"{item['status']:8} {item['width']}x{item['height']} "
            f"combined={str(item['combined']).lower()} parts={item['parts_found']} "
            f"{item['action']}  {item['original_path']}"
        )
    return "\n".join(lines) + "\n"
