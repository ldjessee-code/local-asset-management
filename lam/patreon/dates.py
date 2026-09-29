# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local date windows. Default zone is America/Indianapolis."""

from __future__ import annotations

import calendar
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_ZONE = "America/Indianapolis"


@dataclass(frozen=True)
class Window:
    start: datetime | None
    end: datetime
    display_start: str
    display_end: str
    zone_name: str
    zone: ZoneInfo


def local_zone() -> ZoneInfo:
    from lam.patreon.zones import install_zoneinfo

    install_zoneinfo()
    name = os.environ.get("LAM_PATREON_TZ") or DEFAULT_ZONE
    return ZoneInfo(name)


def now_local(zone: ZoneInfo | None = None) -> datetime:
    zone = zone or local_zone()
    raw = os.environ.get("LAM_PATREON_NOW")
    if raw:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(zone)
    return datetime.now(zone)


def parse_published(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed


def _date_midnight(text: str, zone: ZoneInfo) -> datetime:
    year, month, day = (int(part) for part in text.split("-"))
    return datetime(year, month, day, tzinfo=zone)


def subtract_months(now: datetime, months: int, zone: ZoneInfo) -> datetime:
    local = now.astimezone(zone)
    year = local.year
    month = local.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(local.day, calendar.monthrange(year, month)[1])
    return datetime(year, month, day, tzinfo=zone)


def resolve_window(
    *,
    now: datetime,
    since: str | None,
    until: str | None,
    last_months: int | None,
    zone: ZoneInfo,
) -> Window:
    if last_months is not None and since is None:
        start = subtract_months(now, last_months, zone)
    elif since:
        start = _date_midnight(since, zone)
    else:
        start = None
    if until:
        end = _date_midnight(until, zone)
    else:
        local = now.astimezone(zone)
        end = datetime(local.year, local.month, local.day, tzinfo=zone) + timedelta(days=1)
    display_start = start.strftime("%Y-%m-%d") if start is not None else "begin"
    display_end = (end - timedelta(microseconds=1)).astimezone(zone).strftime("%Y-%m-%d")
    zone_name = getattr(zone, "key", None) or DEFAULT_ZONE
    return Window(
        start=start,
        end=end,
        display_start=display_start,
        display_end=display_end,
        zone_name=zone_name,
        zone=zone,
    )


def in_window(published: datetime | None, window: Window) -> bool | str:
    """True/False, or ``missing`` when the post has no usable timestamp."""
    if published is None:
        return "missing"
    local = published.astimezone(window.zone)
    if window.start is not None and local < window.start:
        return False
    if local >= window.end:
        return False
    return True
