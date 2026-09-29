# SPDX-License-Identifier: AGPL-3.0-or-later
"""Site session cookies via a persistent browser profile.

Public API for other tools (``lam patreon`` next)::

    from lam.token import get_cookie_header, Secret, TokenAuthError

``get_cookie_header`` is defined in ``lam.token.session`` and imported lazily
from this package so ``import lam.token`` never imports Playwright.
"""

from __future__ import annotations

from lam.token.errors import (
    EXIT_AUTH,
    EXIT_CHALLENGE,
    EXIT_DEPS,
    EXIT_DRIVE,
    EXIT_NETWORK,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_RATE,
    EXIT_USAGE,
    EXIT_VERIFICATION,
    TokenAuthError,
    TokenChallengeError,
    TokenDependencyError,
    TokenDriveError,
    TokenError,
    TokenNetworkError,
    TokenRateLimitError,
    TokenUsageError,
    TokenVerificationError,
)

__all__ = [
    "EXIT_AUTH",
    "EXIT_CHALLENGE",
    "EXIT_DEPS",
    "EXIT_DRIVE",
    "EXIT_NETWORK",
    "EXIT_OK",
    "EXIT_PARTIAL",
    "EXIT_RATE",
    "EXIT_USAGE",
    "EXIT_VERIFICATION",
    "Secret",
    "TokenAuthError",
    "TokenChallengeError",
    "TokenDependencyError",
    "TokenDriveError",
    "TokenError",
    "TokenNetworkError",
    "TokenRateLimitError",
    "TokenUsageError",
    "TokenVerificationError",
    "get_cookie_header",
    "login",
    "site_status",
]


def __getattr__(name: str):
    if name in {"get_cookie_header", "login", "site_status"}:
        from lam.token import session as _session

        return getattr(_session, name)
    if name == "Secret":
        from lam.token.secret import Secret

        return Secret
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
