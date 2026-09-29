# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cookie source order. The value stays inside ``Secret``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from lam.patreon.errors import GATE_AUTH, PatreonAuthError, PatreonUsageError
from lam.patreon.paths import check_cookie_file
from lam.token.errors import gate
from lam.token.paths import default_secrets_file
from lam.token.secret import Secret

_BARE_WARNING = "cookie has no '='; prefixed session_id= (full Cookie header is preferred)"


@dataclass
class LoadedCookie:
    header: Secret
    source: str
    warnings: tuple[str, ...] = ()

    def __repr__(self) -> str:
        return f"LoadedCookie(source={self.source!r}, header=Secret(<redacted>))"


def redact_text(text: str, secret: str) -> str:
    if not text or not secret:
        return text
    return text.replace(secret, "<redacted>")


def normalize_cookie(raw: str) -> LoadedCookie:
    if raw is None:
        raw = ""
    text = str(raw).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    if "\n" in text or "\r" in text:
        raise PatreonUsageError(gate("schema", "cookie value contains a newline"))
    warnings: list[str] = []
    if text and "=" not in text:
        text = "session_id=" + text
        warnings.append(_BARE_WARNING)
    return LoadedCookie(header=Secret(text), source="", warnings=tuple(warnings))


def get_cookie_header(site: str):
    """Indirection so tests can replace the token lookup without importing Playwright."""
    from lam.token import get_cookie_header as token_get

    return token_get(site)


def _try_token() -> Secret | None:
    try:
        secret = get_cookie_header("patreon")
    except Exception:
        return None
    if secret is None:
        return None
    revealed = secret.reveal() if isinstance(secret, Secret) else str(secret)
    if not revealed.strip():
        return None
    return secret if isinstance(secret, Secret) else Secret(revealed)


def load_cookie() -> LoadedCookie:
    """First hit wins: lam.token, LAM_PATREON_COOKIE, PATREON_SESSION_COOKIE, file."""
    token_secret = _try_token()
    if token_secret is not None:
        loaded = normalize_cookie(token_secret.reveal())
        loaded.source = "lam.token"
        return loaded
    for name in ("LAM_PATREON_COOKIE", "PATREON_SESSION_COOKIE"):
        raw = os.environ.get(name)
        if raw and raw.strip():
            loaded = normalize_cookie(raw)
            loaded.source = name
            return loaded
    explicit = os.environ.get("LAM_PATREON_COOKIE_FILE")
    if explicit and explicit.strip():
        path = check_cookie_file(Path(explicit.strip()))
        if path.is_file():
            loaded = normalize_cookie(path.read_text(encoding="utf-8"))
            loaded.source = "LAM_PATREON_COOKIE_FILE"
            return loaded
        raise PatreonAuthError(GATE_AUTH)
    path = check_cookie_file(default_secrets_file("patreon"))
    if path.is_file():
        loaded = normalize_cookie(path.read_text(encoding="utf-8"))
        loaded.source = "LAM_PATREON_COOKIE_FILE"
        return loaded
    raise PatreonAuthError(GATE_AUTH)
