# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cookie values never appear in stdout, stderr, logs, reports, or repr()."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from lam.cli import main
from lam.token.errors import TokenError
from lam.token.secret import Secret
from tests.token.fakes import FakeBrowserLayer, FakeHttpxClient, FakeResponse

SECRET = "FAKE-SECRET-1234567890xyz"
FRAGMENTS = {SECRET[i : i + 12] for i in range(0, len(SECRET) - 11)}


def _assert_clean(*blobs: str) -> None:
    combined = "\n".join(b for b in blobs if b)
    assert SECRET not in combined
    for frag in FRAGMENTS:
        assert frag not in combined, frag


def _cli(argv: list[str]):
    with pytest.raises(SystemExit) as caught:
        main(["token", *argv])
    code = caught.value.code
    if code is None:
        return 0
    if isinstance(code, str):
        return code
    return int(code)


def _patreon_cookies():
    return [
        {
            "name": "session_id",
            "value": SECRET,
            "domain": ".patreon.com",
            "path": "/",
            "expires": -1,
        }
    ]


@pytest.fixture
def token_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    profile = local / "lam" / "browser-profiles" / "patreon"
    profile.mkdir(parents=True)
    fake = FakeBrowserLayer(_patreon_cookies())
    env_calls: list[tuple[str, str]] = []

    def setter(name: str, value: str) -> None:
        env_calls.append((name, value))

    monkeypatch.setattr("lam.token.browser._context_factory", fake)
    monkeypatch.setattr("lam.token.store._env_setter", setter)
    return {
        "local": local,
        "profile": profile,
        "fake": fake,
        "env_calls": env_calls,
        "secrets": local / "lam" / "secrets" / "patreon_cookie.txt",
        "meta": local / "lam" / "secrets" / "patreon_meta.json",
    }


def test_get_status_check_and_gates_never_leak_secret(token_env, capsys, caplog, monkeypatch, tmp_path):
    caplog.set_level(logging.DEBUG)
    reports: list[str] = []

    code = _cli(["get", "patreon", "--set-user-env"])
    captured = capsys.readouterr()
    reports.append(captured.out)
    reports.append(captured.err)
    assert code == 0
    secrets = token_env["secrets"]
    assert secrets.is_file()
    body = secrets.read_text(encoding="utf-8")
    assert SECRET in body
    assert token_env["env_calls"] == [("PATREON_SESSION_COOKIE", f"session_id={SECRET}")]
    if token_env["meta"].is_file():
        reports.append(token_env["meta"].read_text(encoding="utf-8"))

    code = _cli(["status", "patreon"])
    captured = capsys.readouterr()
    reports.append(captured.out)
    reports.append(captured.err)
    assert code == 0

    client = FakeHttpxClient([FakeResponse(200, json_data={"data": {"id": "user-1"}})])
    monkeypatch.setattr("lam.token.check._client_factory", lambda: client)
    code = _cli(["status", "patreon", "--check"])
    captured = capsys.readouterr()
    reports.append(captured.out)
    reports.append(captured.err)
    assert code == 0

    # Gate: missing profile
    profile = token_env["profile"]
    parked = profile.with_name("patreon.off")
    profile.rename(parked)
    code = _cli(["get", "patreon"])
    captured = capsys.readouterr()
    reports.append(captured.out)
    reports.append(captured.err)
    assert code == 3
    parked.rename(profile)

    # Gate: expired required cookie
    token_env["fake"].cookies = [
        {
            "name": "session_id",
            "value": SECRET,
            "domain": ".patreon.com",
            "path": "/",
            "expires": 1,
        }
    ]
    code = _cli(["get", "patreon"])
    captured = capsys.readouterr()
    reports.append(captured.out)
    reports.append(captured.err)
    assert code == 3

    # Exception path: browser boom (message has no secret; cookie jar still has it)
    token_env["fake"].cookies = _patreon_cookies()
    token_env["fake"].cookies_error = RuntimeError("simulated browser failure")
    code = _cli(["get", "patreon"])
    captured = capsys.readouterr()
    reports.append(captured.out)
    reports.append(captured.err)
    assert code != 0
    try:
        raise TokenError("GATE error: simulated")
    except TokenError as exc:
        reports.append(str(exc))
        reports.append(repr(exc))
    reports.append(repr(Secret(SECRET)))
    reports.append(str(Secret(SECRET)))
    reports.append(caplog.text)
    _assert_clean(*reports)

    assert any(SECRET in value for _, value in token_env["env_calls"])
