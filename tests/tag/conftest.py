# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tag tests must not open a real socket (Ollama included)."""

from __future__ import annotations

import socket

import pytest


class _BlockedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise RuntimeError("network is blocked in tag tests")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(socket, "socket", _BlockedSocket)
