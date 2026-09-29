# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 4: local date window."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from lam.patreon.dates import in_window, parse_published, resolve_window

ZONE = ZoneInfo("America/Indianapolis")


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(ZONE)


def test_since_inclusive_and_previous_day_out():
    window = resolve_window(now=_at("2026-09-28T20:00:00-04:00"), since="2025-09-28", until=None, last_months=None, zone=ZONE)
    assert in_window(parse_published("2025-09-28T04:00:00+00:00"), window) is True
    assert in_window(parse_published("2025-09-28T03:59:59+00:00"), window) is False


def test_until_exclusive():
    window = resolve_window(now=_at("2026-09-28T20:00:00-04:00"), since="2025-01-01", until="2026-09-28", last_months=None, zone=ZONE)
    assert in_window(parse_published("2026-09-28T04:00:00+00:00"), window) is False
    assert in_window(parse_published("2026-09-28T03:59:59+00:00"), window) is True


def test_last_months_12_from_frozen_now():
    now = _at("2026-09-28T20:00:00-04:00")
    window = resolve_window(now=now, since=None, until=None, last_months=12, zone=ZONE)
    assert window.start == datetime(2025, 9, 28, 0, 0, tzinfo=ZONE)
    assert window.end > now
    assert window.display_start == "2025-09-28"
    assert window.display_end == "2026-09-28"


def test_month_end_clamp():
    now = _at("2026-03-31T15:00:00-04:00")
    window = resolve_window(now=now, since=None, until=None, last_months=1, zone=ZONE)
    assert window.start == datetime(2026, 2, 28, 0, 0, tzinfo=ZONE)


def test_utc_near_midnight_is_previous_local_day():
    window = resolve_window(now=_at("2026-09-28T20:00:00-04:00"), since="2025-09-28", until=None, last_months=None, zone=ZONE)
    assert in_window(parse_published("2025-09-28T03:30:00+00:00"), window) is False


def test_dst_spring_forward_uses_aware_local():
    window = resolve_window(now=_at("2026-03-31T12:00:00-04:00"), since="2026-03-08", until="2026-03-09", last_months=None, zone=ZONE)
    assert in_window(parse_published("2026-03-08T04:30:00+00:00"), window) is False
    assert in_window(parse_published("2026-03-08T07:30:00+00:00"), window) is True


def test_missing_published_at_does_not_crash():
    assert parse_published(None) is None
    assert parse_published("") is None
    assert parse_published("not-a-date") is None
    window = resolve_window(now=_at("2026-09-28T20:00:00-04:00"), since="2025-09-28", until=None, last_months=None, zone=ZONE)
    assert in_window(None, window) == "missing"
