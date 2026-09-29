# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exit codes and typed errors for ``lam token``.

GATE lines are the user-facing fix. Cookie values never appear in messages.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_CHALLENGE = 4
EXIT_VERIFICATION = 5
EXIT_RATE = 6
EXIT_NETWORK = 7
EXIT_DEPS = 8
EXIT_DRIVE = 9

PLAYWRIGHT_INSTALL_HINT = (
    ".venv\\Scripts\\python.exe -m pip install playwright; "
    ".venv\\Scripts\\python.exe -m playwright install chromium"
)


def gate(kind: str, text: str) -> str:
    """One short line: ``GATE <kind>: <text>``."""
    return f"GATE {kind}: {text}"


class TokenError(Exception):
    """Base error. ``exit_code`` maps to the ``lam token`` / ``lam patreon`` table."""

    exit_code = EXIT_PARTIAL
    gate_kind = "error"

    def __init__(self, message: str, *, exit_code: int | None = None, gate_kind: str | None = None):
        super().__init__(message)
        self.exit_code = type(self).exit_code if exit_code is None else exit_code
        self.gate_kind = type(self).gate_kind if gate_kind is None else gate_kind


class TokenUsageError(TokenError):
    """Schema, flags, disabled site, or refused path. Exit 2."""

    exit_code = EXIT_USAGE
    gate_kind = "schema"


class TokenAuthError(TokenError):
    """No profile, required cookie missing or expired, or HTTP 401/403. Exit 3."""

    exit_code = EXIT_AUTH
    gate_kind = "auth"


class TokenChallengeError(TokenError):
    """Captcha or Cloudflare challenge. Exit 4."""

    exit_code = EXIT_CHALLENGE
    gate_kind = "challenge"


class TokenVerificationError(TokenError):
    """2FA or extra verification page. Exit 5."""

    exit_code = EXIT_VERIFICATION
    gate_kind = "challenge"


class TokenRateLimitError(TokenError):
    """HTTP 429. Exit 6."""

    exit_code = EXIT_RATE
    gate_kind = "rate"


class TokenNetworkError(TokenError):
    """DNS, timeout, or connection failure. Exit 7."""

    exit_code = EXIT_NETWORK
    gate_kind = "network"


class TokenDependencyError(TokenError):
    """Optional Playwright extra is missing. Exit 8."""

    exit_code = EXIT_DEPS
    gate_kind = "deps"


class TokenDriveError(TokenError):
    """Profile or secrets drive is unavailable. Exit 9."""

    exit_code = EXIT_DRIVE
    gate_kind = "drive"
