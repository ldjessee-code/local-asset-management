# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build and check ``lam-place-manifest/v1`` against ``out/``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from lam.hashing import sha256_full
from lam.oversize.engine import zip_archive_name
from lam.oversize.images import IMAGE_EXTENSIONS
from lam.schemas.registry import PLACE_MANIFEST_SCHEMAS
from lam.stage.errors import StageError
from lam.stage.job import require_document
from lam.stage.paths import abs_path, path_key

MANIFEST_KIND = "place-manifest"
MANIFEST_NAME = "manifest.json"


def iter_out_files(out: Path) -> list[Path]:
    if not out.is_dir():
        raise StageError(f"out folder not found: {out}")
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(out, followlinks=False):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            found.append(path)
    found.sort(key=lambda item: path_key(item))
    return found


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise StageError(f"not valid JSON: {path}: {exc}") from exc
    require_document(data, PLACE_MANIFEST_SCHEMAS, MANIFEST_KIND)
    return data


def save_manifest(path: Path, document: dict[str, Any]) -> None:
    require_document(document, PLACE_MANIFEST_SCHEMAS, MANIFEST_KIND)
    path = Path(path)
    text = json.dumps(document, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _expected_dest(target: Path, original: Path, out: Path, role: str) -> Path:
    relative = original.relative_to(out)
    if role == "original-zip":
        return target / "_Originals_Zipped" / relative.parent / zip_archive_name(original.name)
    return target / relative


def check_manifest(document: dict[str, Any], out: Path, target: Path) -> None:
    """Refuse when ``out`` and the manifest disagree. Exit code 2."""
    require_document(document, PLACE_MANIFEST_SCHEMAS, MANIFEST_KIND)
    covered: set[str] = set()
    for entry in document["entries"]:
        src = abs_path(Path(entry["src"]))
        if not src.is_file():
            raise StageError(f"manifest src missing: {src}")
        if src.stat().st_size != entry["size"] or sha256_full(src) != entry["sha256"]:
            raise StageError(f"manifest hash mismatch: {src}")
        original_raw = entry["original"]
        if original_raw:
            original = abs_path(Path(original_raw))
            if not original.is_file():
                raise StageError(f"manifest original missing: {original}")
            try:
                original.relative_to(abs_path(out))
            except ValueError as exc:
                raise StageError(f"manifest original is outside out: {original}") from exc
            covered.add(path_key(original))
            expected = _expected_dest(abs_path(target), original, abs_path(out), entry["role"])
        else:
            try:
                src.relative_to(abs_path(out))
            except ValueError as exc:
                raise StageError(f"manifest src is outside out: {src}") from exc
            covered.add(path_key(src))
            expected = _expected_dest(abs_path(target), src, abs_path(out), entry["role"])
        if path_key(Path(entry["dest"])) != path_key(expected):
            raise StageError(f"manifest dest mismatch: {entry['dest']}")
    for path in iter_out_files(out):
        if path_key(path) not in covered:
            raise StageError(f"out file not in manifest: {path}")


def plan_from_out(
    files: list[Path],
    *,
    out: Path,
    target: Path,
    cap: int | None,
) -> list[dict[str, Any]]:
    """Copy plan. Over-cap images have no hash until the stand-in and zip exist."""
    planned: list[dict[str, Any]] = []
    out = abs_path(out)
    target = abs_path(target)
    for path in files:
        path = abs_path(path)
        relative = path.relative_to(out)
        size = path.stat().st_size
        if cap is not None and size > cap:
            if path.suffix.lower() not in IMAGE_EXTENSIONS:
                raise StageError(
                    f"over byte cap and not an image: {path}",
                    code=1,
                )
            planned.append(
                {
                    "role": "stand-in",
                    "src": None,
                    "dest": target / relative,
                    "original": path,
                    "sha256": None,
                    "size": None,
                    "work": None,
                }
            )
            zip_name = zip_archive_name(path.name)
            planned.append(
                {
                    "role": "original-zip",
                    "src": None,
                    "dest": target / "_Originals_Zipped" / relative.parent / zip_name,
                    "original": path,
                    "sha256": None,
                    "size": None,
                    "work": None,
                }
            )
            continue
        planned.append(
            {
                "role": "file",
                "src": path,
                "dest": target / relative,
                "original": None,
                "sha256": sha256_full(path),
                "size": size,
                "work": None,
            }
        )
    return planned


def entries_from_plan(planned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in planned:
        if item["src"] is None or item["sha256"] is None or item["size"] is None:
            raise StageError("manifest entry is missing bytes", code=1)
        entries.append(
            {
                "src": str(abs_path(Path(item["src"]))),
                "dest": str(abs_path(Path(item["dest"]))),
                "sha256": item["sha256"],
                "size": item["size"],
                "role": item["role"],
                "original": None if item["original"] is None else str(abs_path(Path(item["original"]))),
            }
        )
    return entries


def plan_from_manifest(document: dict[str, Any]) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    for entry in document["entries"]:
        planned.append(
            {
                "role": entry["role"],
                "src": abs_path(Path(entry["src"])),
                "dest": abs_path(Path(entry["dest"])),
                "original": None if entry["original"] is None else abs_path(Path(entry["original"])),
                "sha256": entry["sha256"],
                "size": entry["size"],
                "work": None,
            }
        )
    return planned
