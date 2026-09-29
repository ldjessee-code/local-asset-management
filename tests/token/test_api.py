# SPDX-License-Identifier: AGPL-3.0-or-later
"""Library API: get_cookie_header returns Secret and raises typed errors."""

from __future__ import annotations

from pathlib import Path

import pytest

from lam.token import TokenAuthError, TokenChallengeError, get_cookie_header
from lam.token.errors import (
    EXIT_AUTH,
    EXIT_CHALLENGE,
    EXIT_DEPS,
    EXIT_DRIVE,
    EXIT_NETWORK,
    EXIT_RATE,
    EXIT_USAGE,
    EXIT_VERIFICATION,
    TokenDependencyError,
    TokenDriveError,
    TokenNetworkError,
    TokenRateLimitError,
    TokenUsageError,
    TokenVerificationError,
)
from lam.token.secret import Secret
from tests.token.fakes import FakeBrowserLayer


def test_get_cookie_header_returns_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    (local / "lam" / "browser-profiles" / "patreon").mkdir(parents=True)
    fake = FakeBrowserLayer(
        [
            {
                "name": "session_id",
                "value": "lib-token",
                "domain": ".patreon.com",
                "path": "/",
                "expires": -1,
            }
        ]
    )
    monkeypatch.setattr("lam.token.browser._context_factory", fake)
    header = get_cookie_header("patreon")
    assert isinstance(header, Secret)
    assert header.reveal() == "session_id=lib-token"
    assert "lib-token" not in repr(header)
    assert str(header) == "<redacted>"
    secrets = local / "lam" / "secrets" / "patreon_cookie.txt"
    assert not secrets.exists()


def test_get_cookie_header_missing_login_raises_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr("lam.token.browser._context_factory", FakeBrowserLayer())
    with pytest.raises(TokenAuthError) as caught:
        get_cookie_header("patreon")
    assert caught.value.exit_code == EXIT_AUTH
    assert str(caught.value).startswith("GATE auth: no saved login for patreon")


def test_exception_exit_codes():
    assert TokenUsageError("x").exit_code == EXIT_USAGE
    assert TokenAuthError("x").exit_code == EXIT_AUTH
    assert TokenChallengeError("x").exit_code == EXIT_CHALLENGE
    assert TokenVerificationError("x").exit_code == EXIT_VERIFICATION
    assert TokenRateLimitError("x").exit_code == EXIT_RATE
    assert TokenNetworkError("x").exit_code == EXIT_NETWORK
    assert TokenDependencyError("x").exit_code == EXIT_DEPS
    assert TokenDriveError("x").exit_code == EXIT_DRIVE
