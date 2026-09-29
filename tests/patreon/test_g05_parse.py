# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 5: parse a recorded posts page."""

from __future__ import annotations

import json
from pathlib import Path

from lam.patreon.parse import parse_posts_page

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "patreon" / "posts-kidneyboy.json"


def test_parse_recorded_page():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))["pages"][0]
    parsed = parse_posts_page(payload)
    by_id = {post.post_id: post for post in parsed.posts}
    harbor = by_id["kb-181"]
    assert harbor.title == "Harbor Keep"
    assert harbor.url == "https://example.invalid/posts/kb-181"
    assert harbor.published is not None
    assert harbor.can_view is True
    assert len(harbor.attachments) == 1
    assert harbor.attachments[0].name == "harbor.zip"
    assert harbor.attachments[0].kind == "zip"
    river = by_id["kb-602"]
    assert len(river.images) == 1
    assert river.images[0].kind == "loose-media"
    assert by_id["kb-1111"].can_view is False


def test_missing_optional_fields_and_unknown_type():
    payload = {
        "data": [
            {"id": "bare", "type": "post", "attributes": {}, "relationships": {}},
            {
                "id": "odd",
                "type": "post",
                "attributes": {
                    "title": "Odd",
                    "published_at": "2026-01-01T00:00:00+00:00",
                    "current_user_can_view": True,
                    "url": "https://example.invalid/posts/odd",
                },
                "relationships": {"media": {"data": [{"id": "w1", "type": "widget"}]}},
            },
        ],
        "included": [
            {
                "id": "w1",
                "type": "widget",
                "attributes": {"name": "notes.bin", "url": "https://example.invalid/files/notes.bin"},
            }
        ],
        "links": {},
    }
    parsed = parse_posts_page(payload)
    bare = parsed.posts[0]
    assert bare.post_id == "bare"
    assert bare.title == ""
    assert bare.url.startswith("https://")
    assert bare.published is None
    odd = parsed.posts[1]
    assert odd.extras[0].kind == "other"


def test_outside_links_from_content():
    payload = {
        "data": [
            {
                "id": "lnk",
                "type": "post",
                "attributes": {
                    "title": "Links",
                    "published_at": "2026-05-01T12:00:00+00:00",
                    "current_user_can_view": True,
                    "url": "https://example.invalid/posts/lnk",
                    "content": (
                        '<a href="https://www.dropbox.com/s/fake/a.zip">a</a>'
                        '<a href="https://mega.nz/file/abc">b</a>'
                    ),
                },
                "relationships": {},
            }
        ],
        "included": [],
        "links": {"next": "https://example.invalid/api/posts?page=2"},
    }
    parsed = parse_posts_page(payload)
    hosts = sorted(link.host for link in parsed.posts[0].outside_links)
    assert "dropbox.com" in hosts
    assert "mega.nz" in hosts
    assert parsed.next_link == "https://example.invalid/api/posts?page=2"
