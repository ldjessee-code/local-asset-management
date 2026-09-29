# SPDX-License-Identifier: AGPL-3.0-or-later
"""Walk one folder and write ``lam-tags/v1`` plus a metadata sidecar.

Batch precedence (most specific wins):

1. Zip pairing: an archive, a same-folder file with the same stem, and files
   under a sibling directory with that stem.
2. Download burst: files in one directory whose consecutive mtimes fall
   within the window (default 5 minutes, inclusive). A cluster needs 2 files.
3. Parent folder: files in that directory not claimed above share one batch.

``.git`` directories are skipped. Symlinks and junctions are not followed.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from lam.hashing import sha256_full
from lam.schemas.registry import (
    TAG_SIDECAR_SCHEMAS,
    TAGS_SCHEMA_ID,
    TAGS_SCHEMAS,
)
from lam.tag.bins import (
    DEFAULT_BURST_SECONDS,
    DEFAULT_MAX_IMAGE_BYTES,
    DEFAULT_MODEL_URL,
    DEFAULT_WEIGHT_MIN_BYTES,
    LEVEL1_BINS,
    UNSURE,
)
from lam.tag.errors import TagError
from lam.tag.io import local_iso_now, mtime_iso, require_known_schema, write_json_no_clobber
from lam.tag.model import (
    LEVEL1_LABELS,
    ModelUnavailable,
    TagRequest,
    TransientModelError,
    prompt_confirm,
    prompt_primary,
)
from lam.tag.rules import RuleHit, classify_rules, image_kind, sendable_image
from lam.tag.sidecar import build_sidecar, export_xmp, meta_path_for, xmp_dir_for
from lam.tag.subbins import load_subbins

ARCHIVE_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2"}
HEADER_BYTES = 64


@dataclass
class Item:
    rel: str
    abs: str
    size: int
    extension: str
    mtime: str
    mtime_epoch: float
    sha256: str
    parent_rel: str
    stem: str
    name: str
    header: bytes
    copy_group: str | None = None
    batch: str | None = None
    bin: str = "_Unknown"
    subbin: str | None = None
    confidence: str = "unsure"
    rule: str = "none"
    model_label: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rel": self.rel,
            "abs": self.abs,
            "size": self.size,
            "extension": self.extension,
            "mtime": self.mtime,
            "sha256": self.sha256,
            "copy_group": self.copy_group,
            "batch": self.batch,
            "bin": self.bin,
            "subbin": self.subbin,
            "confidence": self.confidence,
            "rule": self.rule,
            "model_label": self.model_label,
            "notes": list(self.notes),
        }


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


def _read_header(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(HEADER_BYTES)


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


def _assign_batches(items: list[Item], window: int) -> None:
    by_parent: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        by_parent[item.parent_rel].append(item)
    extracted: dict[str, str] = {}
    for parent, group in by_parent.items():
        archives = sorted(
            (item for item in group if item.extension in ARCHIVE_EXT),
            key=lambda item: item.rel.lower(),
        )
        for archive in archives:
            if archive.batch is not None:
                continue
            batch = f"zip:{parent}/{archive.stem}" if parent else f"zip:{archive.stem}"
            archive.batch = batch
            for other in group:
                if other is archive or other.batch is not None:
                    continue
                if other.stem.lower() == archive.stem.lower():
                    other.batch = batch
            extracted_key = f"{parent}/{archive.stem}" if parent else archive.stem
            extracted[extracted_key.lower()] = batch
    for item in items:
        if item.batch is not None or not item.parent_rel:
            continue
        parts = item.parent_rel.split("/")
        for index in range(len(parts), 0, -1):
            key = "/".join(parts[:index]).lower()
            if key in extracted:
                item.batch = extracted[key]
                break
    for parent, group in by_parent.items():
        pending = [item for item in group if item.batch is None]
        pending.sort(key=lambda item: (item.mtime_epoch, item.rel))
        clusters: list[list[Item]] = []
        current: list[Item] = []
        for item in pending:
            if not current or item.mtime_epoch - current[-1].mtime_epoch <= window:
                current.append(item)
            else:
                clusters.append(current)
                current = [item]
        if current:
            clusters.append(current)
        burst_index = 0
        parent_key = parent or "."
        for cluster in clusters:
            if len(cluster) >= 2:
                batch = f"burst:{parent_key}:{burst_index}"
                burst_index += 1
                for item in cluster:
                    item.batch = batch
        dir_batch = f"dir:{parent_key}"
        for item in pending:
            if item.batch is None:
                item.batch = dir_batch


def _assign_copy_groups(items: list[Item]) -> None:
    groups: dict[tuple[str, int], list[Item]] = defaultdict(list)
    for item in items:
        groups[(item.sha256, item.size)].append(item)
    for (digest, _size), group in groups.items():
        if len(group) < 2:
            group[0].copy_group = None
            continue
        copy_id = f"cg-{digest[:12]}"
        for item in group:
            item.copy_group = copy_id


def _combine(rule: RuleHit, answers: list[str] | None) -> tuple[str, str]:
    if not answers:
        return rule.bin, rule.confidence
    first = answers[0]
    if first == UNSURE:
        return rule.bin, "unsure"
    if rule.confidence != "unsure" and first == rule.bin:
        return first, "high"
    if len(answers) >= 2:
        second = answers[1]
        if second == UNSURE:
            return rule.bin, "unsure"
        if second == first:
            return first, "high"
        return rule.bin, "low"
    return rule.bin, "low"


def _prompt_kwargs(item: Item, rule: RuleHit, labels: tuple[str, ...], descriptions: dict[str, str]) -> dict:
    folder = item.parent_rel.split("/")[-1] if item.parent_rel else ""
    return {
        "filename": item.name,
        "extension": item.extension,
        "size": item.size,
        "folder": folder,
        "rule_bin": rule.bin,
        "rule_id": rule.rule_id,
        "labels": labels,
        "descriptions": descriptions,
    }


def run_tag_scan(
    folder: Path | str,
    *,
    out: Path | str,
    no_model: bool = False,
    tagger: Any = None,
    model_url: str | None = None,
    level: int = 1,
    bin_name: str | None = None,
    subbins_path: Path | str | None = None,
    burst_window: int = DEFAULT_BURST_SECONDS,
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    weight_min_bytes: int = DEFAULT_WEIGHT_MIN_BYTES,
    xmp: bool = False,
    exiftool_runner=None,
    exiftool_which=None,
    stderr: TextIO | None = None,
) -> dict[str, Any]:
    """Scan *folder* and write tags plus ``<stem>.meta.json``. Returns the tags document."""
    err = stderr or sys.stderr
    source = Path(folder)
    if not source.is_dir():
        raise TagError(f"folder not found: {source}")
    if level not in (1, 2):
        raise TagError("level must be 1 or 2")
    root = source.resolve()
    subbins = None
    descriptions: dict[str, str] = {}
    parent_bin = bin_name
    if level == 2:
        if not bin_name or subbins_path is None:
            raise TagError("level 2 scan requires --bin and --subbins")
        if bin_name not in LEVEL1_BINS:
            raise TagError(f"unknown bin {bin_name}")
        file_bin, subbins = load_subbins(Path(subbins_path))
        if file_bin != bin_name:
            raise TagError(f"subbins bin {file_bin} does not match --bin {bin_name}")
        parent_bin = bin_name
        labels = tuple(sub.name for sub in subbins) + (UNSURE,)
        descriptions = {sub.name: sub.description for sub in subbins if sub.description}
    else:
        labels = LEVEL1_LABELS
    if no_model:
        tagger = None
    elif tagger is None:
        raise TagError("model requested but no tagger was provided")

    items: list[Item] = []
    for path, stat in iter_files(root):
        absolute = path.resolve()
        rel = absolute.relative_to(root).as_posix()
        ext = path.suffix.lower()
        items.append(
            Item(
                rel=rel,
                abs=str(absolute),
                size=int(stat.st_size),
                extension=ext,
                mtime=mtime_iso(stat.st_mtime),
                mtime_epoch=float(stat.st_mtime),
                sha256=sha256_full(path),
                parent_rel=Path(rel).parent.as_posix() if Path(rel).parent.as_posix() != "." else "",
                stem=path.stem,
                name=path.name,
                header=_read_header(path),
            )
        )
    items.sort(key=lambda item: item.rel)
    _assign_copy_groups(items)
    _assign_batches(items, int(burst_window))

    model_state = "disabled" if no_model or tagger is None else getattr(tagger, "model_name", "model")
    model_down = False
    warned = False

    def mark_down() -> None:
        nonlocal model_down, warned, model_state
        model_down = True
        model_state = "unavailable"
        if not warned:
            print(
                "warning: Ollama is unreachable; continuing with rules only (model: unavailable)",
                file=err,
            )
            warned = True

    for item in items:
        rule = classify_rules(
            item.name,
            item.extension,
            item.size,
            item.header,
            weight_min_bytes=weight_min_bytes,
            subbins=subbins,
        )
        kind = image_kind(item.header, item.extension)
        send, skip_note = sendable_image(item.header, item.extension, item.size, max_image_bytes)
        wants_model = tagger is not None and not model_down and (rule.confidence == "unsure" or kind is not None)
        image: bytes | None = None
        answers: list[str] | None = None
        if wants_model and kind is not None and not send:
            wants_model = False
            if skip_note:
                item.notes.append(skip_note)
        if wants_model and tagger is not None:
            if send:
                image = Path(item.abs).read_bytes()
            kwargs = _prompt_kwargs(item, rule, labels, descriptions)
            request = TagRequest(
                prompt=prompt_primary(**kwargs),
                labels=labels,
                image=image,
                filename=item.name,
                rule_bin=rule.bin,
                rule_id=rule.rule_id,
            )
            try:
                first = tagger.classify(request)
                item.model_label = first.label
                if first.label == UNSURE or (rule.confidence != "unsure" and first.label == rule.bin):
                    answers = [first.label]
                else:
                    confirm = TagRequest(
                        prompt=prompt_confirm(**kwargs),
                        labels=labels,
                        image=image,
                        filename=item.name,
                        rule_bin=rule.bin,
                        rule_id=rule.rule_id,
                    )
                    try:
                        second = tagger.classify(confirm)
                    except ModelUnavailable:
                        mark_down()
                        answers = [first.label]
                    except TransientModelError:
                        item.notes.append("model error; rules kept")
                        answers = [first.label]
                    else:
                        answers = [first.label, second.label]
                        item.notes.append(f"model confirm: {second.label}")
            except ModelUnavailable:
                mark_down()
                answers = None
                item.notes.append("model unavailable; rules kept")
            except TransientModelError:
                item.notes.append("model error; rules kept")
                answers = None
        label, confidence = _combine(rule, answers)
        if level == 1:
            item.bin = label if label in LEVEL1_BINS else "_Unknown"
            item.subbin = None
        else:
            item.bin = parent_bin or "_Unknown"
            if confidence == "unsure" or label not in labels or label == UNSURE:
                item.subbin = None
                if confidence != "unsure":
                    confidence = "unsure"
            else:
                item.subbin = label
        item.confidence = confidence
        item.rule = rule.rule_id

    created = local_iso_now()
    out_path = Path(out)
    document = {
        "schema": TAGS_SCHEMA_ID,
        "root": str(root),
        "level": level,
        "bin": parent_bin if level == 2 else None,
        "created": created,
        "model": model_state,
        "model_url": None if no_model else (model_url or DEFAULT_MODEL_URL),
        "burst_window_seconds": int(burst_window),
        "max_image_bytes": int(max_image_bytes),
        "weight_min_bytes": int(weight_min_bytes),
        "files": [item.to_dict() for item in items],
    }
    require_known_schema(document, TAGS_SCHEMAS, "tags")
    sidecar = build_sidecar(
        tags_path=out_path,
        created=created,
        files=document["files"],
        model_state=model_state,
    )
    require_known_schema(sidecar, TAG_SIDECAR_SCHEMAS, "tag-sidecar")
    write_json_no_clobber(out_path, document)
    write_json_no_clobber(meta_path_for(out_path), sidecar)
    if xmp:
        export_xmp(
            sidecar["files"],
            [item.rel for item in items],
            xmp_dir_for(out_path),
            runner=exiftool_runner,
            which=exiftool_which,
            stderr=err,
        )
    return document
