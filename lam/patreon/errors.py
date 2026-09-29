# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exit codes for ``lam patreon``. Numbers match ``lam token``."""

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
    gate,
)

GATE_AUTH = (
    "GATE auth: Patreon session expired - run: lam token login patreon "
    "(or refresh PATREON_SESSION_COOKIE) and rerun (no files written)"
)
GATE_CHALLENGE = "GATE challenge: captcha or Cloudflare challenge - rerun after it clears (no files written)"
GATE_VERIFICATION = (
    "GATE verification: 2FA or verification required - finish it in the browser and rerun (no files written)"
)
GATE_RATE = "GATE rate: HTTP 429 - wait and rerun (no files written)"
GATE_NETWORK = "GATE network: Patreon request failed - check the connection and rerun (no files written)"
GATE_DEPS = "GATE deps: patreon-dl backend needs Node and patreon-dl on PATH (no install attempted)"
GATE_DEPS_STUB = (
    "GATE deps: patreon-dl subprocess backend is not enabled in v1 "
    "(http is the default; no files written)"
)
GATE_DRIVE = "GATE drive: staging root is unavailable - mount the drive and rerun (no files written)"

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
    "GATE_AUTH",
    "GATE_CHALLENGE",
    "GATE_DEPS",
    "GATE_DEPS_STUB",
    "GATE_DRIVE",
    "GATE_NETWORK",
    "GATE_RATE",
    "GATE_VERIFICATION",
    "PatreonAuthError",
    "PatreonChallengeError",
    "PatreonDependencyError",
    "PatreonDriveError",
    "PatreonError",
    "PatreonItemError",
    "PatreonNetworkError",
    "PatreonRateLimitError",
    "PatreonUsageError",
    "PatreonVerificationError",
]


class PatreonError(Exception):
    exit_code = EXIT_PARTIAL

    def __init__(self, message: str):
        super().__init__(message)
        self.exit_code = type(self).exit_code


class PatreonUsageError(PatreonError):
    exit_code = EXIT_USAGE


class PatreonItemError(PatreonError):
    """One bad item (for example a malformed next link). Exit 1."""

    exit_code = EXIT_PARTIAL


class PatreonAuthError(PatreonError):
    exit_code = EXIT_AUTH


class PatreonChallengeError(PatreonError):
    exit_code = EXIT_CHALLENGE


class PatreonVerificationError(PatreonError):
    exit_code = EXIT_VERIFICATION


class PatreonRateLimitError(PatreonError):
    exit_code = EXIT_RATE


class PatreonNetworkError(PatreonError):
    exit_code = EXIT_NETWORK


class PatreonDependencyError(PatreonError):
    exit_code = EXIT_DEPS


class PatreonDriveError(PatreonError):
    exit_code = EXIT_DRIVE


def schema_gate(text: str) -> str:
    if text.startswith("GATE "):
        return text
    return gate("schema", text)
