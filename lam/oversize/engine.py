# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scan a folder for oversize images, record them, and optionally proxy them.

Dry-run is the default. ``--apply`` writes a proxy. A zip of the original is
recycled only after the zip member's SHA-256 matches the scan hash. Nothing
in this module calls ``os.remove`` or ``Path.unlink`` on an original, a proxy,
or a zip.
"""

from __future__ import annotations

import csv
import gc
import hashlib
import math
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
from lam.oversize.locate import (
    Analysis,
    Candidate,
    analyze_map,
    collapse_markers,
    family_key,
    score_file_at,
    standin_size,
    write_previews,
    write_recreated_part,
)
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
    part_scope: str,
    min_score: float,
    min_margin: float,
    min_coverage: float,
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
    if part_scope not in ("pack", "set"):
        raise OversizeError("--part-scope must be pack or set")
    if not 0 <= float(min_score) <= 1:
        raise OversizeError("--min-score must be from 0 to 1")
    if float(min_margin) < 0:
        raise OversizeError("--min-margin must be 0 or more")
    if not 0 <= float(min_coverage) <= 1:
        raise OversizeError("--min-coverage must be from 0 to 1")


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


def _observation(
    item: Seen,
    *,
    combined: bool,
    found: str,
    evidence: str,
    part_paths: list[str],
    area: int,
    now: str,
    phase2: dict | None = None,
) -> dict:
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
        **(phase2 or {}),
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


def _describe_action(*, zip_needed: bool, confidence: str | None, plan_count: int) -> str:
    """Planned file work. A confident recreate names the missing-cell count."""
    del confidence
    if plan_count:
        return f"recreate {plan_count} parts + overview + zip"
    if zip_needed:
        return "overview + zip"
    return "stand-in only"


def _blank_phase(part_scope: str) -> dict:
    return {
        "part_scope": part_scope,
        "scale": None,
        "coverage_pct": None,
        "padding_px": 0,
        "located_parts": [],
        "rejected_candidates": [],
        "duplicate_candidates": [],
        "missing_cells": [],
        "recreated_parts": [],
        "misnamed_parts": [],
        "confidence": None,
        "action": "stand-in only",
    }


def _located_row(part) -> dict:
    return {
        "path": part.path,
        "marker": part.marker,
        "x": int(part.x),
        "y": int(part.y),
        "w": int(part.w),
        "h": int(part.h),
        "score": part.score,
        "margin": part.margin,
    }


def _missing_row(cell) -> dict:
    return {
        "marker": cell.marker,
        "x": int(cell.x),
        "y": int(cell.y),
        "w": int(cell.w),
        "h": int(cell.h),
        "target_path": cell.target_path,
        "target_width": cell.target_width,
        "target_height": cell.target_height,
        "preview_path": cell.preview_path,
    }


def _phase_from(analysis: Analysis | None, duplicates: list[dict], part_scope: str, action: str) -> dict:
    phase = _blank_phase(part_scope)
    phase["duplicate_candidates"] = duplicates
    phase["action"] = action
    if analysis is None:
        return phase
    phase["scale"] = analysis.scale
    phase["coverage_pct"] = analysis.coverage_pct
    phase["padding_px"] = int(analysis.padding_px or 0)
    phase["located_parts"] = [_located_row(part) for part in analysis.located]
    phase["rejected_candidates"] = [
        {"path": item.path, "reason": item.reason, "score": item.score} for item in analysis.rejected
    ]
    phase["missing_cells"] = [_missing_row(cell) for cell in analysis.missing]
    phase["misnamed_parts"] = [
        {
            "path": item.path,
            "marker": item.marker,
            "x": int(item.x),
            "y": int(item.y),
            "w": int(item.w),
            "h": int(item.h),
            "score": item.score,
        }
        for item in analysis.misnamed
    ]
    phase["confidence"] = analysis.confidence
    return phase


def _write_recreations(item: Seen, analysis: Analysis, quality: int) -> tuple[list[dict], bool, str]:
    """Crop each planned cell. Refuse when the target file is already there.

    A new file that scores under 0.98 is kept. The caller must not zip.
    """
    made: list[dict] = []
    scale = analysis.scale if analysis.scale else 1.0
    verified = True
    message = ""
    for plan in analysis.plans:
        if plan.target.exists():
            raise FileExistsError(f"target exists: {plan.target}")
        write_recreated_part(item.path, plan, quality=quality)
        width, height = read_dimensions(plan.target)
        score = score_file_at(
            item.path,
            item.width,
            item.height,
            plan.target,
            width,
            height,
            scale,
            plan.x,
            plan.y,
            plan.w,
            plan.h,
        )
        if isinstance(score, float) and (math.isnan(score) or math.isinf(score)):
            score = 0.0
        preview = None
        for cell in analysis.missing:
            if cell.marker == plan.marker:
                preview = cell.preview_path
                break
        record = {
            "path": str(plan.target.resolve()),
            "marker": plan.marker,
            "sha256": sha256_full(plan.target),
            "width": int(width),
            "height": int(height),
            "source_box": {
                "x": int(plan.x),
                "y": int(plan.y),
                "w": int(plan.w),
                "h": int(plan.h),
            },
            "verify_score": round(float(score), 4),
            "preview_path": preview,
        }
        made.append(record)
        if record["verify_score"] < 0.98:
            verified = False
            message = f"verify score {record['verify_score']} below 0.98 for {plan.marker}"
    gc.collect()
    return made, verified, message


_PLAN_FIELDS = (
    "path",
    "mb",
    "wxh",
    "combined",
    "parts_found",
    "confidence",
    "action",
    "recreate_markers",
    "recreate_targets",
    "standin_wxh",
    "estimated_standin_mb",
    "standin_mb_note",
    "zip_target",
    "expected_zip_mb",
)


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
    part_scope: str = "pack",
    min_score: float = 0.90,
    min_margin: float = 0.05,
    min_coverage: float = 0.97,
    preview_dir: str | Path | None = None,
    preview_standins: bool = False,
    plan_out: str | Path | None = None,
    locate: bool | None = None,
) -> tuple[dict, int]:
    """Walk *folder*. Write the register only when *record* or *apply* is set.

    ``part_scope="pack"`` (the default) searches the whole folder and locates
    parts in the combined image. ``"set"`` keeps the phase-1 search inside
    ``--set-depth`` and does not locate. Pass ``locate=False`` to collect
    pack name matches without reading pixels.
    """
    _check_args(
        min_mb=min_mb,
        min_mp=min_mp,
        max_dim=max_dim,
        set_depth=set_depth,
        quality=quality,
        proxy_suffix=proxy_suffix,
        zip_mode=zip_mode,
        part_scope=part_scope,
        min_score=min_score,
        min_margin=min_margin,
        min_coverage=min_coverage,
    )
    do_locate = (part_scope == "pack") if locate is None else bool(locate)
    root = Path(folder)
    if not root.is_dir():
        raise OversizeError(f"folder not found: {root}")
    preview_path = Path(preview_dir).expanduser() if preview_dir else None
    plan_path = Path(plan_out).expanduser() if plan_out else None
    if preview_path is not None and is_inside(preview_path, root):
        raise OversizeError(f"preview dir must not be inside the scanned folder: {preview_path}")
    if plan_path is not None and plan_path.exists():
        raise OversizeError(f"refusing to overwrite plan: {plan_path}")
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
    plan_rows: list[dict] = []
    touched: set[str] = set()
    error_count = 0
    family_hints: dict[tuple[str, ...], tuple[float, dict]] = {}
    candidate_tokens = [(other, normalize_tokens(other.path.stem)) for other in candidates]
    for item in sorted(oversize, key=lambda row: str(row.path).casefold()):
        home = root.resolve() if part_scope == "pack" else set_directory(item.path, root, set_depth)
        overview_tokens = normalize_tokens(item.path.stem)
        matched: list[tuple[Seen, str]] = []
        # Cheap checks first (pixels, name tokens); Path.resolve() is slow on Windows/Dropbox
        # and the pack holds thousands of candidates, so resolve only real name matches.
        for other, other_tokens in candidate_tokens:
            if other.pixels >= item.pixels:
                continue
            marker = extra_marker(overview_tokens, other_tokens)
            if marker is None:
                continue
            if other.path.resolve() == item.path.resolve():
                continue
            if not is_inside(other.path, home):
                continue
            matched.append((other, marker))
        matched.sort(key=lambda pair: str(pair[0].path).casefold())
        duplicates: list[dict] = []
        if part_scope == "pack":
            pixel_of = {_norm(other.path): other.pixels for other, _marker in matched}
            kept, duplicates = collapse_markers(
                [
                    (Candidate(other.path.resolve(), marker, other.width, other.height), other.pixels)
                    for other, marker in matched
                ]
            )
            markers = [cand.marker for cand in kept]
            part_paths = [str(Path(cand.path).resolve()) for cand in kept]
            raw_area, area = area_percent(
                sum(pixel_of.get(_norm(cand.path), 0) for cand in kept),
                item.pixels,
            )
            locate_candidates = kept
        else:
            markers = [marker for _other, marker in matched]
            part_paths = [str(other.path.resolve()) for other, _marker in matched]
            raw_area, area = area_percent(sum(other.pixels for other, _marker in matched), item.pixels)
            locate_candidates = [
                Candidate(other.path.resolve(), marker, other.width, other.height) for other, marker in matched
            ]
        found = parts_found(markers, raw_area)
        name_combined = path_says_combined(item.path, root)
        combined = name_combined or bool(matched)
        analysis: Analysis | None = None
        if do_locate and locate_candidates:
            family = family_key(overview_tokens)
            hint = family_hints.get(family)
            analysis = analyze_map(
                item.path,
                item.width,
                item.height,
                locate_candidates,
                name_combined=name_combined,
                min_score=min_score,
                min_margin=min_margin,
                min_coverage=min_coverage,
                scale_hint=None if hint is None else hint[0],
                position_seeds=None if hint is None else hint[1],
                proxy_suffix=proxy_suffix,
            )
            found = analysis.parts_found
            combined = bool(analysis.combined)
            if hint is None and analysis.scale and analysis.located:
                family_hints[family] = (float(analysis.scale), dict(analysis.seeds))
        evidence = evidence_text(
            name_combined=name_combined,
            count=len(markers),
            markers=markers,
            area=area,
            status=found,
        )
        if analysis is not None:
            coverage = "n/a" if analysis.coverage_pct is None else analysis.coverage_pct
            evidence += (
                f"; scope=pack; coverage={coverage}%; padding={analysis.padding_note}; "
                f"confidence={analysis.confidence}"
            )
        confidence = analysis.confidence if analysis is not None else None
        will_recreate = (
            analysis is not None
            and confidence == "confident"
            and bool(analysis.plans)
            and zip_mode != "none"
        )
        plan_count = len(analysis.plans) if will_recreate and analysis is not None else 0
        found_for_zip = "yes" if plan_count else found
        zip_needed = should_zip(zip_mode, combined, found_for_zip)
        if not zip_needed:
            plan_count = 0
            will_recreate = False
        action = _describe_action(zip_needed=zip_needed, confidence=confidence, plan_count=plan_count)
        fresh = _observation(
            item,
            combined=combined,
            found=found,
            evidence=evidence,
            part_paths=part_paths,
            area=area,
            now=now,
            phase2=_phase_from(analysis, duplicates, part_scope, action),
        )
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
        if preview_path is not None and combined:
            located = [] if analysis is None else list(analysis.located)
            missing = [] if analysis is None else list(analysis.missing)
            write_previews(
                preview_dir=preview_path,
                combined=item.path,
                combined_w=item.width,
                combined_h=item.height,
                stem=item.path.stem,
                located=located,
                missing=missing,
                standin=False,
            )
            if analysis is not None:
                entry["missing_cells"] = [_missing_row(cell) for cell in analysis.missing]
        elif preview_path is not None and preview_standins:
            write_previews(
                preview_dir=preview_path,
                combined=item.path,
                combined_w=item.width,
                combined_h=item.height,
                stem=item.path.stem,
                located=[],
                missing=[],
                standin=True,
                standin_only=True,
            )
        if plan_path is not None:
            plans = list(analysis.plans) if will_recreate and analysis is not None else []
            stand_w, stand_h = standin_size(item.width, item.height, max_dim)
            estimated = item.size * (stand_w * stand_h) / max(1, item.pixels) / (1024 * 1024)
            zip_target = ""
            expected_zip = ""
            if zip_needed:
                zip_target = str(item.path.with_name(item.path.name + ".zip").resolve())
                expected_zip = str(entry["mb"])
            plan_rows.append(
                {
                    "path": entry["original_path"],
                    "mb": str(entry["mb"]),
                    "wxh": f"{item.width}x{item.height}",
                    "combined": "true" if combined else "false",
                    "parts_found": found,
                    "confidence": confidence or "",
                    "action": action,
                    "recreate_markers": " ".join(plan.marker for plan in plans),
                    "recreate_targets": " | ".join(
                        f"{plan.target} {plan.width}x{plan.height}" for plan in plans
                    ),
                    "standin_wxh": f"{stand_w}x{stand_h}",
                    "estimated_standin_mb": f"{estimated:.3f}",
                    "standin_mb_note": "estimate from pixel ratio",
                    "zip_target": zip_target,
                    "expected_zip_mb": expected_zip,
                }
            )
        if apply and not _outputs_present(entry, zip_needed):
            verify_message = ""
            if confidence and "target exists" in confidence:
                entry["status"] = "error"
                entry["error"] = confidence
                error_count += 1
            else:
                if will_recreate and analysis is not None:
                    try:
                        made, verified, verify_message = _write_recreations(item, analysis, quality)
                        entry["recreated_parts"] = made
                        if verified:
                            entry["parts_found"] = "yes"
                        else:
                            zip_needed = False
                    except Exception as exc:
                        entry["status"] = "error"
                        entry["error"] = f"{type(exc).__name__}: {exc}"
                        error_count += 1
                if entry.get("status") != "error":
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
                    if verify_message and entry.get("status") != "error":
                        entry["status"] = "error"
                        entry["error"] = verify_message
                        error_count += 1
                    elif verify_message:
                        entry["error"] = verify_message
        rows.append(dict(entry))

    if record:
        kept = [entry for sha, entry in by_sha.items() if sha not in touched]
        entries = list(by_sha[sha] for sha in touched) + kept
        entries.sort(key=lambda entry: entry["original_path"].casefold())
        save_register(reg_path, {"schema": document["schema"], "entries": entries})
    if plan_path is not None:
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        with plan_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(_PLAN_FIELDS))
            writer.writeheader()
            writer.writerows(plan_rows)

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
