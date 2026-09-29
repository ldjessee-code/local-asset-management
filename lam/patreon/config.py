# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load ``patreon-creators/v1``."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from lam.patreon.errors import PatreonUsageError, schema_gate
from lam.patreon.names import folder_safe
from lam.schema_lite import validate_json
from lam.schemas.registry import (
    PATREON_CREATORS_SCHEMA_ID,
    PATREON_CREATORS_SCHEMAS,
    STATUS_DEPRECATED,
    STATUS_REMOVED,
    STATUS_SUPPORTED,
    deprecation_warning,
    unsupported_message,
)

DEFAULT_STAGING_ROOT = r"F:\PatreonDL"
DEFAULT_CHRONOS = r"(?i)chronos\s*builder"
DEFAULT_MEDIA_TYPES = ("attachment", "image")
_IGNORED_TAIL = {"posts", "shop", "membership", "about"}


@dataclass(frozen=True)
class Filters:
    include_title_regex: tuple[str, ...]
    exclude_title_regex: tuple[str, ...]
    published_after: str | None
    media_types: tuple[str, ...]
    max_file_mb: float | None


@dataclass(frozen=True)
class Creator:
    slug: str
    display_name: str
    patreon_url: str
    vanity: str
    campaign_id: str | None
    staging_folder: str
    enabled: bool
    filters: Filters


@dataclass(frozen=True)
class CreatorsConfig:
    schema: str
    staging_root: str
    creators: tuple[Creator, ...]
    warnings: tuple[str, ...] = ()


def vanity_from_url(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if parts and parts[0] in {"c", "cw", "user"}:
        parts = parts[1:]
    if parts and parts[-1] in _IGNORED_TAIL:
        parts = parts[:-1]
    return parts[-1] if parts else ""


def _readable(schema_id: str):
    entry = PATREON_CREATORS_SCHEMAS.get(schema_id)
    if entry is None or entry.status == STATUS_REMOVED:
        return None
    if entry.status not in {STATUS_SUPPORTED, STATUS_DEPRECATED}:
        return None
    return entry


def _semantic(data: dict) -> list[str]:
    errors: list[str] = []
    creators = data.get("creators")
    if not isinstance(creators, list):
        return errors
    seen: set[str] = set()
    for item in creators:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if isinstance(slug, str):
            if slug in seen:
                errors.append(f"duplicate slug {slug!r}")
            seen.add(slug)
            if not folder_safe(slug):
                errors.append(f"slug is not folder-safe: {slug!r}")
        folder = item.get("staging_folder")
        if isinstance(folder, str) and folder and not folder_safe(folder):
            errors.append(f"staging_folder is not folder-safe: {folder!r}")
        filters = item.get("filters") if isinstance(item.get("filters"), dict) else {}
        for key in ("include_title_regex", "exclude_title_regex"):
            patterns = filters.get(key) or []
            if not isinstance(patterns, list):
                continue
            for pattern in patterns:
                if not isinstance(pattern, str):
                    errors.append(f"invalid {key} regex")
                    continue
                try:
                    re.compile(pattern)
                except re.error:
                    errors.append(f"invalid {key} regex")
    return errors


def validate_creators_document(data: Any) -> list[str]:
    if isinstance(data, dict) and isinstance(data.get("schema"), str):
        schema_id = data["schema"]
        entry = _readable(schema_id)
        if entry is None:
            return [unsupported_message("patreon-creators", schema_id, PATREON_CREATORS_SCHEMAS)]
        schema = entry.load_document()
    else:
        schema = PATREON_CREATORS_SCHEMAS[PATREON_CREATORS_SCHEMA_ID].load_document()
    errors = validate_json(data, schema)
    if isinstance(data, dict):
        errors.extend(_semantic(data))
    return errors


def _filters(raw: dict | None) -> Filters:
    raw = raw or {}
    excludes = [str(item) for item in raw.get("exclude_title_regex") or []]
    if not any("chronos" in item for item in excludes):
        excludes.append(DEFAULT_CHRONOS)
    includes = tuple(str(item) for item in raw.get("include_title_regex") or [])
    media = raw.get("media_types")
    if not media:
        media_types = DEFAULT_MEDIA_TYPES
    else:
        media_types = tuple(str(item) for item in media)
    max_file = raw.get("max_file_mb")
    return Filters(
        include_title_regex=includes,
        exclude_title_regex=tuple(excludes),
        published_after=raw.get("published_after"),
        media_types=media_types,
        max_file_mb=float(max_file) if isinstance(max_file, (int, float)) else None,
    )


def _creator(item: dict) -> Creator:
    slug = str(item["slug"])
    url = str(item["patreon_url"])
    campaign = item.get("campaign_id")
    if campaign is not None:
        campaign = str(campaign)
    return Creator(
        slug=slug,
        display_name=str(item.get("display_name") or slug),
        patreon_url=url,
        vanity=vanity_from_url(url),
        campaign_id=campaign,
        staging_folder=str(item.get("staging_folder") or slug),
        enabled=bool(item.get("enabled", True)),
        filters=_filters(item.get("filters") if isinstance(item.get("filters"), dict) else None),
    )


def load_creators_document(data: Any) -> CreatorsConfig:
    errors = validate_creators_document(data)
    if errors:
        raise PatreonUsageError(schema_gate(errors[0]))
    schema_id = data["schema"]
    warnings: list[str] = []
    entry = PATREON_CREATORS_SCHEMAS.get(schema_id)
    if entry is not None and entry.status == STATUS_DEPRECATED:
        warnings.append(deprecation_warning(entry))
    creators = tuple(_creator(item) for item in data.get("creators") or [])
    staging = str(data.get("staging_root") or DEFAULT_STAGING_ROOT)
    return CreatorsConfig(schema=schema_id, staging_root=staging, creators=creators, warnings=tuple(warnings))


def load_creators_file(path: Path) -> CreatorsConfig:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PatreonUsageError(schema_gate(f"creators file is not readable JSON: {path.name}")) from None
    return load_creators_document(data)
