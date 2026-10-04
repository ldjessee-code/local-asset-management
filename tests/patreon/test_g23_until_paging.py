# SPDX-License-Identifier: AGPL-3.0-or-later
"""Newest-first feeds must skip posts at or after --until and keep paging.

The 2026-09-30 Party of Two year runs asked for 2020-2025 with
``--until`` set to the next 1 January. Page 1 is September 2026, so
treating "not in the window" as "stop" returned 0 posts.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx

from lam.patreon.config import load_creators_file
from lam.patreon.dates import local_zone, resolve_window
from lam.patreon.engine import collect_posts
from lam.patreon.http_source import HttpPatreonSource, RequestClock
from lam.token.secret import Secret
from tests.patreon.builders import creator, page, post, write_config

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "patreon"
NEXT_2 = "https://www.patreon.com/api/posts?page[cursor]=p2"
NEXT_3 = "https://www.patreon.com/api/posts?page[cursor]=p3"


def _window(since: str, until: str):
    zone = local_zone()
    return resolve_window(
        now=datetime(2026, 9, 30, 12, tzinfo=zone),
        since=since,
        until=until,
        last_months=None,
        zone=zone,
    )


def _item(post_id: str, title: str, published: str | None):
    """An outside link keeps list parsing from fetching post detail."""
    body = f'<a href="https://example.invalid/packs/{post_id}">pack</a>'
    if published is None:
        return post(post_id, title, None, content=body, omit=("published_at",))
    return post(post_id, title, published, content=body)


def _walk(tmp_path: Path, pages: dict[str, dict], *, since: str, until: str, max_posts: int | None = None):
    campaign = json.loads((FIXTURES / "campaign-pledge-included.json").read_text(encoding="utf-8"))
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        if path.rstrip("/").endswith("/current_user"):
            return httpx.Response(200, json={"data": {"id": "u1", "attributes": {"full_name": "Fixture"}}})
        if "/campaigns/" in path:
            return httpx.Response(200, json=campaign)
        cursor = request.url.params.get("page[cursor]") or ""
        if path.rstrip("/").endswith("/posts") and cursor in pages:
            return httpx.Response(200, json=pages[cursor])
        return httpx.Response(500, json={"errors": [{"title": "unexpected"}]})

    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Party_of_Two", "https://www.patreon.com/partyoftwo", campaign_id="9001")],
        staging_root=str(tmp_path / "stage"),
    )
    creator_row = load_creators_file(cfg).creators[0]
    source = HttpPatreonSource(
        Secret("session_id=fixture-session"),
        transport=httpx.MockTransport(handler),
        clock=RequestClock(),
        min_delay=0.0,
    )
    found, warnings, _info = collect_posts(source, creator_row, _window(since, until), max_posts)
    return found, warnings, calls


def _cursors(calls: list[str]) -> list[str]:
    found = []
    for url in calls:
        if "/posts" not in url:
            continue
        if "page[cursor]=p3" in url or "page%5Bcursor%5D=p3" in url:
            found.append("p3")
        elif "page[cursor]=p2" in url or "page%5Bcursor%5D=p2" in url:
            found.append("p2")
        else:
            found.append("")
    return found


def test_until_skips_newer_posts_and_keeps_paging(tmp_path: Path):
    pages = {
        "": page(
            [
                _item("y2026a", "Too New A", "2026-09-01T15:00:00+00:00"),
                _item("y2026b", "Too New B", "2026-06-01T15:00:00+00:00"),
            ],
            next_link=NEXT_2,
        ),
        "p2": page(
            [
                _item("y2025", "In Window", "2025-06-15T15:00:00+00:00"),
                _item("y2024", "Too Old", "2024-12-01T15:00:00+00:00"),
            ],
            next_link=NEXT_3,
        ),
        "p3": page([_item("y2023", "Should Not Read", "2023-01-01T15:00:00+00:00")]),
    }
    found, _warnings, calls = _walk(tmp_path, pages, since="2025-01-01", until="2026-01-01")
    assert [post.post_id for post in found] == ["y2025"]
    assert _cursors(calls) == ["", "p2"]


def test_until_exclusive_same_page_continues(tmp_path: Path):
    pages = {
        "": page(
            [
                _item("at-until", "Exclusive End", "2026-01-01T05:00:00+00:00"),
                _item("inside", "November Pack", "2025-11-30T18:00:00+00:00"),
                _item("before", "Prior Year", "2024-12-31T18:00:00+00:00"),
            ],
            next_link=NEXT_2,
        ),
        "p2": page([_item("later-page", "Not Read", "2024-06-01T15:00:00+00:00")]),
    }
    found, _warnings, calls = _walk(tmp_path, pages, since="2025-01-01", until="2026-01-01")
    assert [post.post_id for post in found] == ["inside"]
    assert _cursors(calls) == [""]


def test_newer_than_until_does_not_consume_max_posts(tmp_path: Path):
    pages = {
        "": page(
            [
                _item("y2026a", "Too New A", "2026-09-01T15:00:00+00:00"),
                _item("y2026b", "Too New B", "2026-03-01T15:00:00+00:00"),
            ],
            next_link=NEXT_2,
        ),
        "p2": page(
            [
                _item("first", "June", "2025-06-15T15:00:00+00:00"),
                _item("second", "March", "2025-03-15T15:00:00+00:00"),
            ]
        ),
    }
    found, _warnings, calls = _walk(
        tmp_path, pages, since="2025-01-01", until="2026-01-01", max_posts=1
    )
    assert [post.post_id for post in found] == ["first"]
    assert _cursors(calls) == ["", "p2"]


def test_stop_at_older_than_since_unchanged(tmp_path: Path):
    pages = {
        "": page(
            [
                _item("inside", "March", "2026-03-01T15:00:00+00:00"),
                _item("older", "Previous June", "2025-06-01T15:00:00+00:00"),
            ],
            next_link=NEXT_2,
        ),
        "p2": page([_item("ancient", "Not Read", "2024-01-01T15:00:00+00:00")]),
    }
    found, _warnings, calls = _walk(tmp_path, pages, since="2026-01-01", until="2027-01-01")
    assert [post.post_id for post in found] == ["inside"]
    assert _cursors(calls) == [""]


def test_missing_published_at_not_outside_until(tmp_path: Path):
    pages = {
        "": page(
            [
                _item("too-new", "September", "2026-09-01T15:00:00+00:00"),
                _item("undated", "No Timestamp", None),
                _item("too-old", "Way Back", "2024-01-01T15:00:00+00:00"),
            ],
            next_link=NEXT_2,
        ),
        "p2": page([_item("next-page", "Not Read", "2023-01-01T15:00:00+00:00")]),
    }
    found, warnings, calls = _walk(tmp_path, pages, since="2025-01-01", until="2026-01-01")
    assert [post.post_id for post in found] == ["undated"]
    assert any("undated" in warning and "published_at" in warning for warning in warnings)
    assert _cursors(calls) == [""]
