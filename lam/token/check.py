# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cheap ``status --check`` who-am-I request. Never logs the Cookie header."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lam.token.errors import (
    TokenAuthError,
    TokenChallengeError,
    TokenNetworkError,
    TokenRateLimitError,
    TokenUsageError,
    TokenVerificationError,
    gate,
)
from lam.token.profiles import SiteProfile
from lam.token.secret import Secret

ClientFactory = Callable[[], Any]

_client_factory: ClientFactory | None = None

MAX_ATTEMPTS = 3  # original + at most 2 retries
_CHALLENGE_MARKERS = (
    "cf-challenge",
    "challenge-platform",
    "just a moment",
    "hcaptcha",
    "g-recaptcha",
    "cf-turnstile",
    "cdn-cgi/challenge",
)
_TWO_FACTOR_MARKERS = (
    "two-factor",
    "two factor",
    "2fa",
    "verification code",
    "identity verification",
    "enter the code",
)


def _client():
    if _client_factory is not None:
        return _client_factory()
    try:
        import httpx
    except ImportError as exc:
        raise TokenNetworkError(gate("network", "httpx is not installed")) from exc
    return httpx.Client(follow_redirects=True, timeout=15.0)


def _json_path(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _looks_like_challenge(text: str, headers: Any) -> bool:
    blob = (text or "").lower()
    server = ""
    if headers:
        server = str(headers.get("server") or headers.get("Server") or "").lower()
    if any(marker in blob for marker in _CHALLENGE_MARKERS):
        return True
    if "cloudflare" in server and ("captcha" in blob or "challenge" in blob):
        return True
    return False


def _looks_like_2fa(text: str) -> bool:
    blob = (text or "").lower()
    return any(marker in blob for marker in _TWO_FACTOR_MARKERS)


def _is_network(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in {"ConnectError", "ConnectTimeout", "ReadTimeout", "TimeoutException", "NetworkError", "TimeoutError"}:
        return True
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    return False


def check_session(site: SiteProfile, header: Secret) -> dict[str, Any]:
    """GET the site's validate URL. Raises a typed ``TokenError`` on failure."""
    spec = site.validate
    if spec is None:
        raise TokenUsageError(gate("usage", f"site {site.id!r} has no validate url"))
    client = _client()
    last_network: BaseException | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.get(
                spec.url,
                headers={"Cookie": header.reveal(), "Accept": "application/json, text/html"},
            )
        except Exception as exc:
            last_network = exc
            if _is_network(exc) and attempt + 1 < MAX_ATTEMPTS:
                continue
            raise TokenNetworkError(gate("network", type(exc).__name__)) from None
        status = int(getattr(response, "status_code", 0) or 0)
        text = getattr(response, "text", "") or ""
        headers = getattr(response, "headers", {}) or {}
        if status == 429:
            if attempt + 1 < MAX_ATTEMPTS:
                continue
            raise TokenRateLimitError(gate("rate", "HTTP 429"))
        return _interpret(site, spec, status, text, headers, response)
    raise TokenNetworkError(gate("network", type(last_network).__name__ if last_network else "error"))


def _interpret(site, spec, status: int, text: str, headers, response) -> dict[str, Any]:
    if _looks_like_challenge(text, headers):
        raise TokenChallengeError(gate("challenge", "captcha or Cloudflare challenge"))
    if _looks_like_2fa(text):
        raise TokenVerificationError(gate("challenge", "2FA or verification required"))
    if status in {401, 403}:
        raise TokenAuthError(gate("auth", f"HTTP {status} for {site.id}"))
    if status == 429:
        raise TokenRateLimitError(gate("rate", "HTTP 429"))
    expect = spec.expect_status or 200
    if status != expect:
        raise TokenAuthError(gate("auth", f"HTTP {status} for {site.id}"))
    if spec.expect_json_path:
        try:
            payload = response.json()
        except Exception:
            raise TokenAuthError(gate("auth", f"validate response is not JSON for {site.id}")) from None
        if _json_path(payload, spec.expect_json_path) in {None, ""}:
            raise TokenAuthError(
                gate("auth", f"validate JSON missing {spec.expect_json_path} for {site.id}")
            )
    return {"ok": True, "status": status, "site": site.id}
