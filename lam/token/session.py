# SPDX-License-Identifier: AGPL-3.0-or-later
"""login / get / status orchestration. Cookie values stay in ``Secret``."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from lam.token.browser import ensure_browser_available, persistent_context, wait_for_login
from lam.token.cookies import CookieSelection, build_cookie_header
from lam.token.errors import TokenAuthError, TokenDriveError, TokenError, TokenUsageError, gate
from lam.token.paths import (
    check_allowed_path,
    resolve_meta_file,
    resolve_profile_dir,
    resolve_secrets_file,
)
from lam.token.profiles import SiteProfile, SiteProfiles, builtin_profiles, load_profiles_document, require_site, status_sites
from lam.token.secret import Secret
from lam.token.store import read_meta_file, secrets_mtime_iso, set_user_env_var, write_meta_file, write_secrets_file

log = logging.getLogger("lam.token")

CLOSED_WINDOW_MSG = (
    'login window closed - session saved in profile; run "lam token status patreon --check" to confirm'
)


def _is_target_closed(exc: BaseException) -> bool:
    if type(exc).__name__ == "TargetClosedError":
        return True
    return "target page, context or browser has been closed" in str(exc).lower()


def load_active_profiles(path: Path | str | None = None) -> SiteProfiles:
    raw = path if path is not None else os.environ.get("LAM_SITE_PROFILES")
    if not raw:
        return builtin_profiles()
    file_path = Path(raw)
    try:
        data = json.loads(file_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise TokenUsageError(gate("schema", f"site-profiles file not found: {file_path}")) from exc
    except json.JSONDecodeError as exc:
        raise TokenUsageError(gate("schema", f"site-profiles file is not valid JSON: {exc}")) from exc
    profiles = load_profiles_document(data)
    return profiles


def _ensure_drive(path: Path, *, kind: str) -> None:
    drive = path.drive
    if not drive:
        return
    root = Path(drive + os.sep)
    try:
        exists = root.exists()
    except OSError as exc:
        raise TokenDriveError(gate("drive", f"{kind} path/drive unavailable: {path}")) from exc
    if not exists:
        raise TokenDriveError(gate("drive", f"{kind} path/drive unavailable: {path}"))


def _selection_meta(site: SiteProfile, selection: CookieSelection, last_check: dict | None = None) -> dict[str, Any]:
    expiry = None
    if selection.earliest_expiry is not None:
        expiry = selection.earliest_expiry.isoformat()
    payload = {
        "site": site.id,
        "cookie_names": list(selection.names),
        "cookie_count": selection.count,
        "header_length": selection.total_length,
        "earliest_expiry": expiry,
        "required_present": selection.required_present,
        "written_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "last_check": last_check,
    }
    return payload


def _write_meta(site: SiteProfile, payload: dict[str, Any]) -> None:
    path = resolve_meta_file(site)
    _ensure_drive(path, kind="secrets")
    write_meta_file(path, payload, replace_own=True)


def _read_cookies(site: SiteProfile) -> CookieSelection:
    profile_dir = resolve_profile_dir(site)
    _ensure_drive(profile_dir, kind="profile")
    if not profile_dir.exists():
        raise TokenAuthError(
            gate("auth", f"no saved login for {site.id} - run: lam token login {site.id}")
        )
    ensure_browser_available()
    with persistent_context(profile_dir, headless=True) as ctx:
        try:
            cookies = ctx.cookies()
        except TokenError:
            raise
        except Exception:
            raise TokenError(gate("error", "failed to read cookies from the browser profile")) from None
        return build_cookie_header(list(cookies), site)


def login(site: str, *, profiles: SiteProfiles | None = None, wait_fn=None) -> None:
    """Open a visible Chromium window on the site's persistent profile.

    The operator logs in by hand. This function never fills forms or types
    credentials.
    """
    bundle = profiles or load_active_profiles()
    site_profile = require_site(bundle, site, action="login")
    profile_dir = resolve_profile_dir(site_profile)
    _ensure_drive(profile_dir, kind="profile")
    check_allowed_path(profile_dir, kind="profile")
    ensure_browser_available()
    profile_dir.mkdir(parents=True, exist_ok=True)
    cookies = None
    closed_early = False
    with persistent_context(profile_dir, headless=False) as ctx:
        pages = getattr(ctx, "pages", None) or []
        page = pages[0] if pages else ctx.new_page()
        page.goto(site_profile.login_url)
        waiter = wait_fn or wait_for_login
        waiter(ctx)
        try:
            cookies = list(ctx.cookies())
        except TokenError:
            raise
        except Exception as exc:
            if _is_target_closed(exc):
                closed_early = True
            else:
                raise TokenError(gate("error", "failed to read cookies from the browser profile")) from None
    if cookies is None and closed_early:
        try:
            with persistent_context(profile_dir, headless=True) as ctx:
                cookies = list(ctx.cookies())
        except TokenError:
            raise
        except Exception as exc:
            if not _is_target_closed(exc):
                raise TokenError(gate("error", "failed to read cookies from the browser profile")) from None
            cookies = None
    if cookies is None:
        if closed_early:
            raise TokenAuthError(
                gate("auth", f"no saved login for {site_profile.id} - run: lam token login {site_profile.id}")
            )
        return
    try:
        selection = build_cookie_header(list(cookies), site_profile)
        _write_meta(site_profile, _selection_meta(site_profile, selection))
        if closed_early:
            print(CLOSED_WINDOW_MSG, file=sys.stderr)
        log.debug("login saved cookie metadata for %s count=%s", site_profile.id, selection.count)
    except TokenAuthError:
        if closed_early:
            raise
        log.debug("login finished without required cookies for %s", site_profile.id)


def fetch_cookies(
    site: str,
    *,
    write_secrets: bool = False,
    set_user_env: bool = False,
    profiles: SiteProfiles | None = None,
) -> tuple[SiteProfile, CookieSelection]:
    """Read cookies once. Optionally write the secrets file / User env var."""
    bundle = profiles or load_active_profiles()
    site_profile = require_site(bundle, site, action="get")
    selection = _read_cookies(site_profile)
    log.debug(
        "built cookie header for %s names=%s count=%s length=%s",
        site_profile.id,
        ",".join(selection.names),
        selection.count,
        selection.total_length,
    )
    _write_meta(site_profile, _selection_meta(site_profile, selection))
    if write_secrets:
        path = resolve_secrets_file(site_profile)
        _ensure_drive(path, kind="secrets")
        write_secrets_file(path, selection.header, replace_own=True)
    if set_user_env:
        if not site_profile.env_var:
            raise TokenUsageError(gate("usage", f"site {site_profile.id!r} has no env_var"))
        set_user_env_var(site_profile.env_var, selection.header)
    return site_profile, selection


def get_cookie_header(
    site: str,
    *,
    write_secrets: bool = False,
    set_user_env: bool = False,
    profiles: SiteProfiles | None = None,
) -> Secret:
    """Read cookies from the persistent profile and return the Cookie header.

    The return value is a ``Secret``. Call ``.reveal()`` only at the point of
    use (an HTTP header, the secrets file, or an env setter).
    """
    _site_profile, selection = fetch_cookies(
        site,
        write_secrets=write_secrets,
        set_user_env=set_user_env,
        profiles=profiles,
    )
    return selection.header


def collect_cookie_selection(
    site: str,
    *,
    profiles: SiteProfiles | None = None,
) -> tuple[SiteProfile, CookieSelection]:
    return fetch_cookies(site, write_secrets=False, set_user_env=False, profiles=profiles)


def site_status(
    site: str | None = None,
    *,
    check: bool = False,
    profiles: SiteProfiles | None = None,
) -> list[dict[str, Any]]:
    """Offline status rows. ``check=True`` performs one who-am-I request."""
    bundle = profiles or load_active_profiles()
    if site is not None:
        try:
            bundle.get(site)
        except KeyError as exc:
            raise TokenUsageError(gate("usage", f"unknown site {site!r}")) from exc
        chosen = status_sites(bundle, site)
        if not chosen:
            return []
    else:
        chosen = status_sites(bundle)
    rows = []
    for item in chosen:
        rows.append(_status_row(item, check=check))
    return rows


def _status_row(site: SiteProfile, *, check: bool) -> dict[str, Any]:
    profile_dir = resolve_profile_dir(site)
    secrets_path = resolve_secrets_file(site)
    meta_path = resolve_meta_file(site)
    profile_exists = profile_dir.exists()
    meta = read_meta_file(meta_path) or {}
    last_check = meta.get("last_check")
    row: dict[str, Any] = {
        "site": site.id,
        "display_name": site.display_name,
        "profile_exists": profile_exists,
        "required_cookies": list(site.required_cookies),
        "required_present": meta.get("required_present"),
        "cookie_names": list(meta.get("cookie_names") or ()),
        "cookie_count": meta.get("cookie_count"),
        "header_length": meta.get("header_length"),
        "earliest_expiry": meta.get("earliest_expiry"),
        "secrets_mtime": secrets_mtime_iso(secrets_path),
        "last_check": last_check,
    }
    if not profile_exists:
        row["message"] = f"no saved login for {site.id}"
        row["required_present"] = False
        return row
    row["message"] = "saved login"
    if check:
        from lam.token.check import check_session

        site_profile, selection = collect_cookie_selection(site.id)
        result = check_session(site_profile, selection.header)
        last_check = {
            "at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
            "ok": True,
            "status": result.get("status"),
        }
        payload = _selection_meta(site_profile, selection, last_check=last_check)
        _write_meta(site_profile, payload)
        row["last_check"] = last_check
        row["required_present"] = selection.required_present
        row["cookie_names"] = list(selection.names)
        row["cookie_count"] = selection.count
        row["header_length"] = selection.total_length
        row["earliest_expiry"] = (
            selection.earliest_expiry.isoformat() if selection.earliest_expiry else None
        )
        row["message"] = "saved login; check ok"
    return row
