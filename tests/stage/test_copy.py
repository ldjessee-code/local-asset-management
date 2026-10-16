# SPDX-License-Identifier: AGPL-3.0-or-later
"""Temp rename retry. Sleep is recorded, not waited."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lam.stage.copying import copy_verified
from lam.stage.errors import StageError


def _patch_replace(monkeypatch: pytest.MonkeyPatch, fail_times: int, error: BaseException):
    sleeps: list[float] = []
    state = {"n": 0}
    real = os.replace

    def flaky(src, dest):
        state["n"] += 1
        if state["n"] <= fail_times:
            raise error
        real(src, dest)

    monkeypatch.setattr("lam.stage.copying._replace", flaky)
    return sleeps


def test_retries_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "a.txt"
    src.write_bytes(b"hi")
    dest = tmp_path / "b.txt"
    sleeps = _patch_replace(monkeypatch, 5, PermissionError(13, "Access is denied"))
    digest = copy_verified(src, dest, sleep=sleeps.append)
    assert sleeps == [1, 2, 4, 8, 16]
    assert dest.read_bytes() == b"hi"
    assert src.read_bytes() == b"hi"
    assert digest == __import__("lam.hashing", fromlist=["sha256_full"]).sha256_full(src)


def test_retries_winerror_5(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "a.txt"
    src.write_bytes(b"win")
    dest = tmp_path / "b.txt"
    error = OSError(13, "denied", None, 5)
    assert getattr(error, "winerror", None) == 5
    sleeps = _patch_replace(monkeypatch, 5, error)
    copy_verified(src, dest, sleep=sleeps.append)
    assert sleeps == [1, 2, 4, 8, 16]
    assert dest.read_bytes() == b"win"


def test_exhausted_retries_remove_only_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "a.txt"
    src.write_bytes(b"hi")
    dest = tmp_path / "b.txt"

    def always_locked(_src, _dest):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr("lam.stage.copying._replace", always_locked)
    with pytest.raises(StageError):
        copy_verified(src, dest, sleep=lambda _seconds: None)
    assert not dest.exists()
    assert not Path(str(dest) + ".~place.tmp").exists()
    assert src.read_bytes() == b"hi"
