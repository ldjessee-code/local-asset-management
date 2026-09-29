# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build a Cookie header from a browser cookie jar. Values stay inside ``Secret``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lam.token.errors import TokenAuthError, gate
from lam.token.profiles import SiteProfile
from lam.token.secret import Secret


@dataclass(frozen=True)
class CookieSelection:
    names: tuple[str, ...]
    count: int
    total_length: int
    earliest_expiry: datetime | None
    required_present: bool
    header: Secret


def domain_matches(cookie_domain: str, allowed: tuple[str, ...]) -> bool:
    host = (cookie_domain or "").lower().lstrip(".")
    for raw in allowed:
        root = raw.lower().lstrip(".")
        if host == root or host.endswith("." + root):
            return True
    return False


def _is_session(expires: Any) -> bool:
    if expires is None:
        return True
    try:
        return float(expires) <= 0
    except (TypeError, ValueError):
        return True


def _is_expired(expires: Any, now: float) -> bool:
    if _is_session(expires):
        return False
    return float(expires) < now


def _sort_key(cookie: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(cookie.get("name") or ""),
        str(cookie.get("domain") or ""),
        str(cookie.get("path") or ""),
    )


def build_cookie_header(
    cookies: list[dict[str, Any]],
    site: SiteProfile,
    *,
    now: float | None = None,
) -> CookieSelection:
    """Select cookies for *site* and join ``name=value; ...`` in stable order."""
    import time

    clock = time.time() if now is None else now
    matched = [
        cookie
        for cookie in cookies
        if domain_matches(str(cookie.get("domain") or ""), site.cookie_domains)
    ]
    if site.header_cookies != "all":
        allow = set(site.header_cookies)
        matched = [cookie for cookie in matched if cookie.get("name") in allow]
    matched.sort(key=_sort_key)

    by_name = {str(cookie.get("name") or ""): cookie for cookie in matched}
    for required in site.required_cookies:
        cookie = by_name.get(required)
        if cookie is None:
            raise TokenAuthError(
                gate("auth", f"required cookie {required} missing for {site.id}")
            )
        if _is_expired(cookie.get("expires"), clock):
            raise TokenAuthError(
                gate("auth", f"required cookie {required} expired for {site.id}")
            )

    parts = [f"{cookie['name']}={cookie.get('value') or ''}" for cookie in matched]
    raw = "; ".join(parts)
    expiries = [
        float(cookie["expires"])
        for cookie in matched
        if not _is_session(cookie.get("expires"))
    ]
    earliest = None
    if expiries:
        earliest = datetime.fromtimestamp(min(expiries)).astimezone().replace(microsecond=0)
    names = tuple(str(cookie["name"]) for cookie in matched)
    return CookieSelection(
        names=names,
        count=len(names),
        total_length=len(raw),
        earliest_expiry=earliest,
        required_present=True,
        header=Secret(raw),
    )
