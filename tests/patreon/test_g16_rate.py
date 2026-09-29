# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 16: sequential delay and capped 429 retries."""

from __future__ import annotations

import time

import httpx
import pytest

from lam.patreon.errors import EXIT_RATE, PatreonRateLimitError
from lam.patreon.http_source import HttpPatreonSource, RequestClock
from lam.token.secret import Secret


def _source(handler, clock: RequestClock) -> HttpPatreonSource:
    return HttpPatreonSource(
        Secret("session_id=fixture-session"),
        transport=httpx.MockTransport(handler),
        clock=clock,
        min_delay=0.25,
        retry_cap=5.0,
    )


def test_min_delay_and_no_real_sleep(monkeypatch: pytest.MonkeyPatch):
    def boom(seconds):
        raise AssertionError("real sleep")

    monkeypatch.setattr(time, "sleep", boom)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"data": {"id": "u1"}})

    clock = RequestClock()
    source = _source(handler, clock)
    source.whoami()
    source.whoami()
    assert calls["n"] == 2
    assert clock.sleeps
    assert clock.sleeps[0] >= 0.25


def test_429_retries_at_most_twice(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: (_ for _ in ()).throw(AssertionError("real sleep")))
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "30"}, text="slow")

    clock = RequestClock()
    source = _source(handler, clock)
    with pytest.raises(PatreonRateLimitError) as caught:
        source.whoami()
    assert caught.value.exit_code == EXIT_RATE
    assert calls["n"] == 3
    assert calls["n"] <= 3
    assert clock.sleeps
    assert max(clock.sleeps) <= 5
    assert 5 in clock.sleeps or max(clock.sleeps) == 5
