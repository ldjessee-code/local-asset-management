# SPDX-License-Identifier: AGPL-3.0-or-later
"""Safety fixtures for lam.token tests.

No test in this directory may touch the network, a real browser, the real
Windows registry, %LOCALAPPDATA%, or F:.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest


class _BlockPlaywrightFinder:
    """Fail loudly if a test (or the code under test) imports Playwright."""

    def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002
        if fullname == "playwright" or fullname.startswith("playwright."):
            raise ImportError(
                "real playwright import is blocked in tests; use the fake browser layer"
            )
        return None


class _BlockedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise RuntimeError("network is blocked in token tests")


@pytest.fixture(autouse=True)
def _block_network_and_playwright(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):
    finder = _BlockPlaywrightFinder()
    sys.meta_path.insert(0, finder)
    monkeypatch.setattr(socket, "socket", _BlockedSocket)

    local = tmp_path_factory.mktemp("lam-localappdata")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.delenv("LAM_SITE_PROFILES", raising=False)
    monkeypatch.delenv("LAM_DROPBOX_ROOT", raising=False)

    yield {"localappdata": Path(local)}

    try:
        sys.meta_path.remove(finder)
    except ValueError:
        pass
