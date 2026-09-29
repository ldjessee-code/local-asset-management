# SPDX-License-Identifier: AGPL-3.0-or-later
"""Safety fixtures for lam.patreon tests.

No test here may open a socket, read a real cookie, or touch %LOCALAPPDATA% or F:.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from lam.patreon.zones import install_zoneinfo

install_zoneinfo()

COOKIE_ENV = (
    "LAM_PATREON_COOKIE",
    "PATREON_SESSION_COOKIE",
    "LAM_PATREON_COOKIE_FILE",
    "LAM_PATREON_NOW",
)


class _BlockPlaywrightFinder:
    def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002
        if fullname == "playwright" or fullname.startswith("playwright."):
            raise ImportError("real playwright import is blocked in patreon tests")
        return None


class _BlockedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise RuntimeError("network is blocked in patreon tests")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):
    finder = _BlockPlaywrightFinder()
    sys.meta_path.insert(0, finder)
    monkeypatch.setattr(socket, "socket", _BlockedSocket)
    local = tmp_path_factory.mktemp("lam-localappdata")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("LAM_PATREON_TZ", "America/Indianapolis")
    monkeypatch.delenv("LAM_DROPBOX_ROOT", raising=False)
    monkeypatch.delenv("LAM_SITE_PROFILES", raising=False)
    for name in COOKIE_ENV:
        monkeypatch.delenv(name, raising=False)
    yield {"localappdata": Path(local)}
    try:
        sys.meta_path.remove(finder)
    except ValueError:
        pass


def invoke(argv: list[str]) -> int:
    from lam.cli import main

    try:
        main(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None or code == 0:
            return 0
        return int(code)
    return 0


def gate_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("GATE ")]


def assert_no_secret(text: str, secret: str) -> None:
    if not secret:
        return
    assert secret not in text
    for index in range(0, max(0, len(secret) - 11)):
        assert secret[index : index + 12] not in text
