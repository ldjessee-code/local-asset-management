# SPDX-License-Identifier: AGPL-3.0-or-later
"""status --check with a mocked httpx client."""

from __future__ import annotations

from pathlib import Path

import pytest

from lam.cli import main
from lam.token.check import MAX_ATTEMPTS
from lam.token.errors import (
    EXIT_AUTH,
    EXIT_CHALLENGE,
    EXIT_NETWORK,
    EXIT_OK,
    EXIT_RATE,
    EXIT_VERIFICATION,
)
from tests.token.fakes import FakeBrowserLayer, FakeHttpxClient, FakeResponse


def _cli(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as caught:
        main(["token", *argv])
    return int(caught.value.code or 0)


@pytest.fixture
def saved_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    profile = local / "lam" / "browser-profiles" / "patreon"
    profile.mkdir(parents=True)
    fake = FakeBrowserLayer(
        [
            {
                "name": "session_id",
                "value": "ok-token",
                "domain": ".patreon.com",
                "path": "/",
                "expires": -1,
            }
        ]
    )
    monkeypatch.setattr("lam.token.browser._context_factory", fake)
    return {"fake": fake}


def _run_check(monkeypatch, client) -> int:
    monkeypatch.setattr("lam.token.check._client_factory", lambda: client)
    return _cli(["status", "patreon", "--check"])


def test_check_200_with_data_id(saved_login, monkeypatch):
    client = FakeHttpxClient([FakeResponse(200, json_data={"data": {"id": "user-1"}})])
    assert _run_check(monkeypatch, client) == EXIT_OK
    assert len(client.calls) == 1


def test_check_401_and_403(saved_login, monkeypatch):
    client = FakeHttpxClient([FakeResponse(401, text="nope")])
    assert _run_check(monkeypatch, client) == EXIT_AUTH
    client = FakeHttpxClient([FakeResponse(403, text="nope")])
    assert _run_check(monkeypatch, client) == EXIT_AUTH


def test_check_cloudflare_exit_4(saved_login, monkeypatch):
    html = "<html><body>Just a moment... cf-challenge</body></html>"
    client = FakeHttpxClient([FakeResponse(200, text=html)])
    assert _run_check(monkeypatch, client) == EXIT_CHALLENGE


def test_check_2fa_exit_5(saved_login, monkeypatch):
    html = "<html>Enter your two-factor verification code</html>"
    client = FakeHttpxClient([FakeResponse(200, text=html)])
    assert _run_check(monkeypatch, client) == EXIT_VERIFICATION


def test_check_429_exit_6(saved_login, monkeypatch):
    client = FakeHttpxClient([FakeResponse(429, text="slow down"), FakeResponse(429, text="slow down"), FakeResponse(429, text="slow down")])
    assert _run_check(monkeypatch, client) == EXIT_RATE
    assert len(client.calls) <= MAX_ATTEMPTS


def test_check_network_error_exit_7(saved_login, monkeypatch):
    err = ConnectionError("dns")
    client = FakeHttpxClient([], errors=[err, ConnectionError("dns"), ConnectionError("dns")])
    assert _run_check(monkeypatch, client) == EXIT_NETWORK
    assert len(client.calls) <= MAX_ATTEMPTS
    assert len(client.calls) >= 1
