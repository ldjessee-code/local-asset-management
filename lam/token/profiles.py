# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load and validate ``lam-site-profiles/v1`` documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from lam.schema_lite import validate_json
from lam.schemas.registry import (
    SITE_PROFILES_SCHEMA_ID,
    SITE_PROFILES_SCHEMAS,
    STATUS_DEPRECATED,
    STATUS_SUPPORTED,
    deprecation_warning,
    unsupported_message,
)
from lam.token.errors import TokenUsageError, gate

Action = Literal["login", "get", "status"]

BUILTIN_PATREON: dict[str, Any] = {
    "id": "patreon",
    "display_name": "Patreon",
    "login_url": "https://www.patreon.com/login",
    "cookie_domains": [".patreon.com"],
    "required_cookies": ["session_id"],
    "header_cookies": "all",
    "validate": {
        "url": "https://www.patreon.com/api/current_user",
        "expect_json_path": "data.id",
        "expect_status": 200,
    },
    "env_var": "PATREON_SESSION_COOKIE",
    "enabled": True,
}


@dataclass(frozen=True)
class ValidateSpec:
    url: str
    expect_json_path: str | None = None
    expect_status: int = 200


@dataclass(frozen=True)
class SiteProfile:
    id: str
    display_name: str
    login_url: str
    cookie_domains: tuple[str, ...]
    required_cookies: tuple[str, ...]
    header_cookies: str | tuple[str, ...]
    validate: ValidateSpec | None
    env_var: str | None
    secrets_file: str | None
    profile_dir: str | None
    enabled: bool


@dataclass(frozen=True)
class SiteProfiles:
    schema: str
    sites: tuple[SiteProfile, ...]
    warnings: tuple[str, ...] = ()

    def get(self, site_id: str) -> SiteProfile:
        for site in self.sites:
            if site.id == site_id:
                return site
        raise KeyError(site_id)


def _readable_entry(schema_id: str):
    entry = SITE_PROFILES_SCHEMAS.get(schema_id)
    if entry is None or entry.status not in {STATUS_SUPPORTED, STATUS_DEPRECATED}:
        return None
    return entry


def validate_profiles_document(data: Any) -> list[str]:
    """Return schema errors. Empty list means the document is valid.

    An unknown or removed ``schema`` id is a single error whose text is
    ``unsupported site-profiles schema ...``.
    """
    if isinstance(data, dict) and isinstance(data.get("schema"), str):
        schema_id = data["schema"]
        entry = _readable_entry(schema_id)
        if entry is None:
            return [unsupported_message("site-profiles", schema_id, SITE_PROFILES_SCHEMAS)]
        schema = entry.load_document()
    else:
        schema = SITE_PROFILES_SCHEMAS[SITE_PROFILES_SCHEMA_ID].load_document()
    errors = validate_json(data, schema)
    if errors:
        return errors
    if not isinstance(data, dict):
        return ["$: expected an object"]
    seen: set[str] = set()
    for i, site in enumerate(data.get("sites") or []):
        if not isinstance(site, dict):
            continue
        site_id = site.get("id")
        if not isinstance(site_id, str):
            continue
        if site_id in seen:
            return [f"duplicate site id {site_id!r}"]
        seen.add(site_id)
    return []


def load_profiles_document(data: Any) -> SiteProfiles:
    """Parse a site-profiles document. Invalid input raises ``TokenUsageError`` (exit 2)."""
    errors = validate_profiles_document(data)
    if errors:
        raise TokenUsageError(gate("schema", errors[0]))
    schema_id = data["schema"]
    warnings: list[str] = []
    entry = SITE_PROFILES_SCHEMAS.get(schema_id)
    if entry is not None and entry.status == STATUS_DEPRECATED:
        warnings.append(deprecation_warning(entry))
    sites = tuple(_parse_site(item) for item in data.get("sites") or [])
    return SiteProfiles(schema=schema_id, sites=sites, warnings=tuple(warnings))


def builtin_profiles() -> SiteProfiles:
    return load_profiles_document(
        {
            "schema": SITE_PROFILES_SCHEMA_ID,
            "sites": [dict(BUILTIN_PATREON)],
        }
    )


def require_site(profiles: SiteProfiles, site_id: str, *, action: Action) -> SiteProfile:
    try:
        site = profiles.get(site_id)
    except KeyError as exc:
        raise TokenUsageError(gate("usage", f"unknown site {site_id!r}")) from exc
    if not site.enabled:
        raise TokenUsageError(
            gate("usage", f"site {site_id!r} is disabled"),
            gate_kind="usage",
        )
    return site


def status_sites(profiles: SiteProfiles, site_id: str | None = None) -> list[SiteProfile]:
    """Enabled sites for ``lam token status``. Disabled sites are omitted."""
    if site_id is None:
        return [site for site in profiles.sites if site.enabled]
    try:
        site = profiles.get(site_id)
    except KeyError:
        return []
    if not site.enabled:
        return []
    return [site]


def _parse_site(item: dict[str, Any]) -> SiteProfile:
    raw_header = item.get("header_cookies", "all")
    if raw_header == "all" or raw_header is None:
        header: str | tuple[str, ...] = "all"
    else:
        header = tuple(str(name) for name in raw_header)
    raw_validate = item.get("validate")
    validate = None
    if isinstance(raw_validate, dict):
        validate = ValidateSpec(
            url=str(raw_validate["url"]),
            expect_json_path=raw_validate.get("expect_json_path"),
            expect_status=int(raw_validate.get("expect_status", 200)),
        )
    return SiteProfile(
        id=str(item["id"]),
        display_name=str(item.get("display_name") or item["id"]),
        login_url=str(item["login_url"]),
        cookie_domains=tuple(str(d) for d in item["cookie_domains"]),
        required_cookies=tuple(str(n) for n in item.get("required_cookies") or ()),
        header_cookies=header,
        validate=validate,
        env_var=item.get("env_var"),
        secrets_file=item.get("secrets_file"),
        profile_dir=item.get("profile_dir"),
        enabled=bool(item.get("enabled", True)),
    )
