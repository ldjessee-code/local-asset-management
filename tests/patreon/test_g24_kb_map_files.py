# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kidney Boy map files: attachment images count, previews and covers do not.

Sanitized from the 2026-10-06 single-post payloads. No network, no signed URLs.
"""

from __future__ import annotations

import json

from lam.patreon.parse import parse_posts_page

GRID_URL = "https://c10.patreonusercontent.com/fake/grid-map.jpg"
BANNER_URL = "https://c10.patreonusercontent.com/fake/banner.jpg"
VARIATION_URL = "https://c10.patreonusercontent.com/fake/variations.jpg"
COVER_URL = "https://c10.patreonusercontent.com/fake/cover.jpg"
PREVIEW_URL = "https://c10.patreonusercontent.com/fake/embedded-preview.png"
TIER_POST = "https://www.patreon.com/posts/tier-rewards-zip-900"
DROPBOX = "https://www.dropbox.com/scl/fo/example/FOLDER"


def _full_file(item) -> bool:
    """Same rule as the Kidney Boy gatherer: attachment images are files.

    Embedded images (source_kind image) and an image post_file cover are not.
    """
    sk = (item.source_kind or "").lower()
    if sk in ("attachment", "attachments", "content"):
        return True
    if sk == "post_file":
        return item.kind != "loose-media"
    if sk == "image":
        return False
    return item.kind != "loose-media"


def _map_post() -> dict:
    body = {
        "type": "doc",
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 3},
                "content": [
                    {
                        "type": "text",
                        "text": "Tier Rewards.zip",
                        "marks": [
                            {
                                "type": "link",
                                "attrs": {"href": TIER_POST, "target": "_blank"},
                            }
                        ],
                    }
                ],
            },
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "Dropbox pack",
                        "marks": [
                            {
                                "type": "link",
                                "attrs": {"href": DROPBOX, "target": "_blank"},
                            }
                        ],
                    }
                ],
            },
            {
                "type": "image",
                "attrs": {
                    "src": PREVIEW_URL,
                    "media_id": "prev-1",
                    "node_type": "block",
                },
            },
        ],
    }
    return {
        "data": {
            "id": "100369043",
            "type": "post",
            "attributes": {
                "title": "Map #58: Airport",
                "url": "https://example.invalid/posts/100369043",
                "published_at": "2024-03-20T14:00:05.000+00:00",
                "current_user_can_view": True,
                "is_paid": False,
                "content": None,
                "content_json_string": json.dumps(body),
                "post_file": {"url": COVER_URL, "width": 2000, "height": 758},
                "post_type": "image_file",
            },
            "relationships": {
                "attachments": {"data": []},
                "attachments_media": {
                    "data": [{"id": "grid-1", "type": "media"}]
                },
                "audio": {"data": None},
                "images": {"data": [{"id": "banner-1", "type": "media"}]},
                "media": {
                    "data": [
                        {"id": "banner-1", "type": "media"},
                        {"id": "grid-1", "type": "media"},
                        {"id": "var-1", "type": "media"},
                    ]
                },
            },
        },
        "included": [
            {
                "id": "grid-1",
                "type": "media",
                "attributes": {
                    "file_name": "Airport Interior Day Grid 3500x3500 FREE.jpg",
                    "download_url": GRID_URL,
                    "mimetype": "image/jpeg",
                    "size_bytes": 3500001,
                    "owner_relationship": "attachment",
                },
            },
            {
                "id": "banner-1",
                "type": "media",
                "attributes": {
                    "file_name": "Airport Banner.jpg",
                    "download_url": BANNER_URL,
                    "mimetype": "image/jpeg",
                    "size_bytes": 80000,
                    "owner_relationship": "main",
                },
            },
            {
                "id": "var-1",
                "type": "media",
                "attributes": {
                    "file_name": "Airport_Variations.jpg",
                    "download_url": VARIATION_URL,
                    "mimetype": "image/jpeg",
                    "size_bytes": 90000,
                    "owner_relationship": "text-editor",
                },
            },
        ],
    }


def test_attachments_media_image_is_a_full_file_and_previews_are_not():
    post = parse_posts_page(_map_post()).posts[0]
    assert post.can_view is True
    assert len(post.attachments) == 1
    grid = post.attachments[0]
    assert grid.name == "Airport Interior Day Grid 3500x3500 FREE.jpg"
    assert grid.url == GRID_URL
    assert grid.size == 3500001
    assert grid.source_kind == "attachment"
    assert _full_file(grid)
    assert [item.name for item in post.images] == ["Airport Banner.jpg"]
    assert post.images[0].source_kind == "image"
    assert post.images[0].kind == "loose-media"
    assert not _full_file(post.images[0])
    extra_urls = {item.url for item in post.extras}
    assert VARIATION_URL in extra_urls
    assert all(not _full_file(item) for item in post.extras)
    urls = {item.url for item in post.attachments}
    assert BANNER_URL not in urls
    assert COVER_URL not in urls
    assert PREVIEW_URL not in urls
    assert TIER_POST not in urls
    assert [link.url for link in post.outside_links] == [DROPBOX]
    assert all(link.host == "dropbox.com" for link in post.outside_links)


def test_attachments_media_zip_source_kind_is_attachment():
    from pathlib import Path

    payload = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "patreon"
            / "posts-attachment-sources.json"
        ).read_text(encoding="utf-8")
    )
    by_id = {post.post_id: post for post in parse_posts_page(payload).posts}
    ferry = by_id["163923452"]
    assert ferry.attachments[0].source_kind == "attachment"
    assert ferry.attachments[0].kind == "zip"
    assert _full_file(ferry.attachments[0])
    legacy = by_id["legacy-1"]
    assert legacy.attachments[0].source_kind == "attachment"
    cover = by_id["image-post-file"]
    assert cover.attachments == []
    assert by_id["map-88"].attachments == []
    assert by_id["map-88"].images[0].source_kind == "image"


def test_locked_zip_without_url_is_not_an_attachment():
    payload = {
        "data": {
            "id": "locked-1",
            "type": "post",
            "attributes": {
                "title": "Survivor Rewards.zip",
                "url": "https://example.invalid/posts/locked-1",
                "published_at": "2026-09-18T13:00:02.000+00:00",
                "current_user_can_view": False,
                "is_paid": False,
                "content": None,
                "post_type": "text_only",
            },
            "relationships": {
                "audio": {"data": None},
                "images": {"data": []},
                "media": {"data": [{"id": "zip-locked", "type": "media"}]},
            },
        },
        "included": [
            {
                "id": "zip-locked",
                "type": "media",
                "attributes": {
                    "file_name": "Survivor Rewards.zip",
                    "mimetype": "application/zip",
                    "size_bytes": 1000,
                    "owner_relationship": "attachment",
                },
            }
        ],
    }
    post = parse_posts_page(payload).posts[0]
    assert post.can_view is False
    assert post.attachments == []
    assert all(item.source_kind != "attachment" for item in post.extras)
    assert all(not item.url for item in post.extras)
