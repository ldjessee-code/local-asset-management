# SPDX-License-Identifier: AGPL-3.0-or-later
"""Conservative HTTP gate signals for JSON APIs (Patreon who-am-I and list).

False positives are worse than a missed gate. A 2xx JSON body is never
treated as 2FA or a Cloudflare challenge because it contains words like
``2fa``, ``captcha``, or ``verification code``. Markers are a last pass
on HTML only. There is no documented JSON:API error code for 2FA in this
codebase.
"""

from __future__ import annotations

from urllib.parse import urlparse

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
_VERIFICATION_FIRST = {
    "login",
    "two-factor",
    "two_factor",
    "2fa",
    "mfa",
    "verify",
    "verification",
    "identity-verification",
}
_VERIFICATION_ANY = {"two-factor", "two_factor", "2fa", "mfa"}


def header_get(headers, name: str) -> str:
    if headers is None:
        return ""
    try:
        value = headers.get(name)
        if value is None and hasattr(headers, "get"):
            value = headers.get(name.title()) or headers.get(name.lower())
        return str(value or "")
    except Exception:
        return ""


def content_type(headers) -> str:
    return header_get(headers, "content-type").lower().split(";")[0].strip()


def is_json_content(ctype: str) -> bool:
    if not ctype:
        return False
    if ctype in {"application/json", "application/vnd.api+json"}:
        return True
    return ctype.endswith("+json")


def is_html_content(ctype: str, text: str) -> bool:
    if ctype == "text/html" or ctype.startswith("text/html"):
        return True
    blob = (text or "").lstrip().lower()
    return blob.startswith("<!doctype html") or blob.startswith("<html")


def path_of(url: str) -> str:
    if not url:
        return ""
    raw = url.strip()
    if "://" in raw:
        return urlparse(raw).path.lower()
    return raw.split("?", 1)[0].split("#", 1)[0].lower()


def is_verification_url(url: str) -> bool:
    """True for a login / 2FA / verification page. Never for ``/api/*``."""
    path = path_of(url).rstrip("/")
    segments = [part for part in path.split("/") if part]
    if not segments:
        return False
    if segments[0] == "api":
        return False
    if segments[0] in _VERIFICATION_FIRST:
        return True
    return any(part in _VERIFICATION_ANY for part in segments)


def safe_url_for_log(url: str) -> str:
    if not url:
        return ""
    if "://" in url:
        parsed = urlparse(url)
        return parsed._replace(query="", fragment="").geturl()
    return url.split("?", 1)[0]


def snippet_for_log(text: str, limit: int = 200) -> str:
    blob = (text or "").replace("\r", " ").replace("\n", " ")
    if len(blob) > limit:
        return blob[:limit]
    return blob


def classify_response(
    *,
    status: int,
    text: str,
    headers,
    url: str = "",
) -> tuple[str, str] | None:
    """Return ``(kind, reason)`` or ``None``.

    Order: headers and status first; body markers last and only on HTML.
    Kinds: ``challenge``, ``verification``, ``auth``, ``rate``.
    """
    ctype = content_type(headers)
    jsonish = is_json_content(ctype)
    htmlish = is_html_content(ctype, text)
    location = header_get(headers, "location")
    cf_mitigated = header_get(headers, "cf-mitigated").lower()
    blob = (text or "").lower()

    if cf_mitigated == "challenge":
        return "challenge", "header cf-mitigated=challenge"

    if status == 429:
        return "rate", "status 429"

    if 300 <= status < 400 and is_verification_url(location):
        return "verification", f"redirect {safe_url_for_log(location)}"
    if is_verification_url(url) and not (jsonish and 200 <= status < 300):
        return "verification", f"url {safe_url_for_log(url)}"

    if status in {403, 503} and htmlish and _html_challenge(blob, headers):
        return "challenge", f"status {status} html cloudflare"
    if not (200 <= status < 300) and htmlish and "cdn-cgi/challenge" in blob:
        return "challenge", "html cdn-cgi/challenge"

    if status == 401:
        return "auth", "status 401"
    if status == 403:
        return "auth", "status 403"

    if 200 <= status < 300 and jsonish:
        return None

    if htmlish and not (200 <= status < 300):
        if any(marker in blob for marker in _TWO_FACTOR_MARKERS):
            return "verification", f"html status {status} 2fa marker"
        if _html_challenge(blob, headers):
            return "challenge", f"html status {status} challenge marker"
    return None


def _html_challenge(blob: str, headers) -> bool:
    if any(marker in blob for marker in _CHALLENGE_MARKERS):
        return True
    if "captcha" in blob:
        return True
    server = header_get(headers, "server").lower()
    return "cloudflare" in server and ("captcha" in blob or "challenge" in blob)
