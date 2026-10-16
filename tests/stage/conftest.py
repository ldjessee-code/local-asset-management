# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stage tests stay on tmp_path, off the network, off the Recycle Bin, and off court-run."""

from __future__ import annotations

import socket

import pytest


class _BlockedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise RuntimeError("network is blocked in stage tests")


@pytest.fixture(autouse=True)
def _guards(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory):
    monkeypatch.setattr(socket, "socket", _BlockedSocket)

    def boom_recycle(path):
        raise AssertionError(f"real recycle_path called for {path}")

    def boom_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run called")

    monkeypatch.setattr("lam.actions.recycle_path", boom_recycle)
    monkeypatch.setattr("lam.stage.doorbell.subprocess.run", boom_run)
    scratch = tmp_path_factory.mktemp("staging-unused")
    monkeypatch.setenv("LAM_STAGING_ROOT", str(scratch))
    monkeypatch.setenv("LAM_PLACE_JOBS_DIR", str(scratch / "jobs"))
