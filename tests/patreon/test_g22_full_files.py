# SPDX-License-Identifier: AGPL-3.0-or-later
"""Party of Two full-file shapes from the 2026-09-30 capture. No network."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx

from lam.patreon.config import load_creators_file
from lam.patreon.dates import in_window, local_zone, parse_published, resolve_window
from lam.patreon.engine import run_sync
from lam.patreon.http_source import FixtureSource, HttpPatreonSource, RequestClock
from lam.patreon.parse import parse_posts_page
from lam.token.secret import Secret
from tests.patreon.builders import attachment, creator, page, post, write_config, write_fixture
from tests.patreon.conftest import invoke

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "patreon"
DROP_2026 = (
    "https://www.dropbox.com/scl/fo/oq7b4f0d3injctpzq3vwa/ADH8eb5zQJ_ph5ymclI-Qy0?<query-redacted>",
    "https://www.dropbox.com/scl/fo/tiq5vjzp50ovwiq5pnnsi/ACm4m2kK9n4t9w64u5jPcv0?<query-redacted>",
    "https://www.dropbox.com/scl/fo/d0gdf5j3y9uos48fmrqia/ADOF5jGTL0p8nmRdSxzJ0Vs?<query-redacted>",
)
DROP_2020 = (
    "https://www.dropbox.com/sh/d9ubgf5uwksjzsy/AABRs4HSVimvMWgBSPLEMT_wa?<query-redacted>",
    "https://www.dropbox.com/sh/d34n2dx32ibjis0/AACH6Jt6bzzUDQwjk6vrOyCMa?<query-redacted>",
    "https://www.dropbox.com/sh/pozrppry9lccu8r/AABfoHR9u3KckqporEZvXJyHa?<query-redacted>",
)
MASTER = "https://www.patreon.com/posts/30364003"
COVER_URL = (
    "https://c10.patreonusercontent.com/4/patreon-media/p/post/168120643/"
    "b671bcdd8d95431cab3ed2d6807a273d/eyJ3Ijo2MjB9/1.jpg?<query-redacted>"
)
ZIP_URL = "https://c10.patreonusercontent.com/fake/MagicWorlds_Set4.zip"
TOWER_URL = "https://c10.patreonusercontent.com/fake/1_InfiniteTower_Set7.zip"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _by_id(name: str) -> dict:
    parsed = parse_posts_page(_load(name))
    return {item.post_id: item for item in parsed.posts}


def _window(since: str, until: str):
    zone = local_zone()
    return resolve_window(
        now=datetime(2026, 9, 30, 12, tzinfo=zone),
        since=since,
        until=until,
        last_months=None,
        zone=zone,
    )


def _config(tmp_path: Path):
    path = write_config(
        tmp_path / "creators.json",
        [creator("Party_of_Two", "https://www.patreon.com/partyoftwo", campaign_id="9001")],
        staging_root=str(tmp_path / "stage"),
    )
    return load_creators_file(path)


def _source(handler) -> HttpPatreonSource:
    return HttpPatreonSource(
        Secret("session_id=fixture-session"),
        transport=httpx.MockTransport(handler),
        clock=RequestClock(),
        min_delay=0.0,
    )


def test_rewards_2026_dropbox_urls_from_content_json():
    post = _by_id("partyoftwo-rewards-5.json")["171028257"]
    assert post.can_view is True
    assert post.attachments == []
    assert [link.url for link in post.outside_links] == list(DROP_2026)
    assert all(link.host == "dropbox.com" for link in post.outside_links)
    assert MASTER not in [link.url for link in post.outside_links]
    assert all(item.name != "file" for item in post.attachments)


def test_rewards_2020_same_shape_dropbox_sh_links():
    post = _by_id("partyoftwo-rewards-5.json")["45652282"]
    assert post.attachments == []
    assert [link.url for link in post.outside_links] == list(DROP_2020)
    assert all(link.host == "dropbox.com" for link in post.outside_links)
    assert MASTER not in [link.url for link in post.outside_links]


def test_sync_records_dropbox_and_does_not_fetch_detail_or_download(tmp_path: Path):
    page = _load("partyoftwo-rewards-5.json")
    campaign = _load("campaign-pledge-included.json")
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/current_user"):
            return httpx.Response(200, json={"data": {"id": "u1", "attributes": {"full_name": "Fixture"}}})
        if "/campaigns/" in request.url.path:
            return httpx.Response(200, json=campaign)
        if request.url.path.rstrip("/").endswith("/posts"):
            return httpx.Response(200, json=page)
        return httpx.Response(500, json={"data": []})

    results = tmp_path / "sync.json"
    code = run_sync(
        _config(tmp_path),
        _source(handler),
        window=_window("2020-01-01", "2027-01-01"),
        only_slug="Party_of_Two",
        max_posts=None,
        apply=True,
        results_path=results,
        csv=False,
    )
    assert code == 0
    assert not any("/posts/" in path for path in paths)
    document = json.loads(results.read_text(encoding="utf-8"))
    by_id = {item["post_id"]: item for item in document["posts"]}
    row = by_id["171028257"]
    assert row["status"] == "deferred"
    assert "dropbox.com" in row["reason"]
    hosts = {item["host"] for item in row["files"]}
    assert hosts == {"dropbox.com"}
    assert all(item["status"] == "deferred" for item in row["files"])
    joined = json.dumps(document)
    assert "dropbox.com" in joined
    assert not any(item["status"] == "downloaded" for item in row["files"])


def test_detail_fetch_stages_zip_when_list_post_has_no_file(tmp_path: Path):
    campaign = _load("campaign-pledge-included.json")
    empty = {
        "data": [
            {
                "id": "detail-only",
                "type": "post",
                "attributes": {
                    "title": "Pack only on detail",
                    "url": "https://www.patreon.com/partyoftwo/posts/detail-only",
                    "published_at": "2026-06-01T15:00:00+00:00",
                    "current_user_can_view": True,
                    "content": None,
                    "post_file": None,
                    "post_type": "text_only",
                },
                "relationships": {
                    "attachments": {"data": []},
                    "attachments_media": {"data": []},
                    "images": {"data": []},
                    "media": {"data": []},
                },
            }
        ],
        "included": [],
        "links": {},
    }
    detail = {
        "data": {
            "id": "detail-only",
            "type": "post",
            "attributes": empty["data"][0]["attributes"],
            "relationships": {
                "attachments_media": {"data": [{"id": "zip-detail", "type": "media"}]}
            },
        },
        "included": [
            {
                "id": "zip-detail",
                "type": "media",
                "attributes": {
                    "file_name": "MagicWorlds_Set4.zip",
                    "download_url": ZIP_URL,
                    "mimetype": "application/zip",
                    "size_bytes": 11,
                },
            }
        ],
    }
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/current_user"):
            return httpx.Response(200, json={"data": {"id": "u1", "attributes": {"full_name": "Fixture"}}})
        if "/campaigns/" in request.url.path:
            return httpx.Response(200, json=campaign)
        if request.url.path.rstrip("/").endswith("/posts"):
            return httpx.Response(200, json=empty)
        if request.url.path.rstrip("/").endswith("/posts/detail-only"):
            return httpx.Response(200, json=detail)
        if request.url.path.endswith("MagicWorlds_Set4.zip"):
            return httpx.Response(200, content=b"PKZIPDATA!!")
        return httpx.Response(500, text="unexpected")

    results = tmp_path / "detail.json"
    code = run_sync(
        _config(tmp_path),
        _source(handler),
        window=_window("2026-01-01", "2027-01-01"),
        only_slug="Party_of_Two",
        max_posts=None,
        apply=True,
        results_path=results,
        csv=False,
    )
    assert code == 0
    assert any(path.endswith("/posts/detail-only") for path in paths)
    assert not any("dropbox.com" in path for path in paths)
    staged = list((tmp_path / "stage").rglob("MagicWorlds_Set4.zip"))
    assert len(staged) == 1
    assert staged[0].read_bytes() == b"PKZIPDATA!!"
    document = json.loads(results.read_text(encoding="utf-8"))
    files = document["posts"][0]["files"]
    assert files[0]["name"] == "MagicWorlds_Set4.zip"
    assert files[0]["kind"] == "zip"
    assert files[0]["status"] == "downloaded"


def test_post_file_cover_is_not_bare_file(tmp_path: Path):
    FixtureSource.downloaded = []
    root = tmp_path / "fx"
    write_fixture(root, "partyoftwo", [_load("partyoftwo-post-file-cover.json")])
    (root / "files").mkdir()
    (root / "files" / "img-bridge").write_bytes(b"jpeg-bytes-cover-test")
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Party_of_Two", "https://www.patreon.com/partyoftwo", campaign_id="9001")],
        staging_root=str(tmp_path / "stage"),
    )
    results = tmp_path / "cover.json"
    code = invoke(
        [
            "patreon",
            "sync",
            "--creators",
            str(cfg),
            "--fixture-dir",
            str(root),
            "--apply",
            "--since",
            "2026-01-01",
            "--until",
            "2027-01-01",
            "--results",
            str(results),
        ]
    )
    assert code == 0
    parsed = _by_id("partyoftwo-post-file-cover.json")["168120643"]
    assert parsed.attachments == []
    assert all(item.name != "file" for item in parsed.attachments + parsed.images + parsed.extras)
    assert COVER_URL not in FixtureSource.downloaded
    names = [path.name for path in (tmp_path / "stage").rglob("*") if path.is_file()]
    assert "file" not in names
    assert "Online_Bridge of the Stars_Day_Gridless.jpg" in names
    document = json.loads(results.read_text(encoding="utf-8"))
    file_rows = document["posts"][0]["files"]
    assert [item["name"] for item in file_rows] == ["Online_Bridge of the Stars_Day_Gridless.jpg"]
    assert all(item["kind"] != "other" for item in file_rows)


def test_extension_from_name_url_or_mimetype():
    payload = page(
        [
            post(
                "by-url",
                "By URL",
                "2026-06-01T15:00:00+00:00",
                attachments=[
                    attachment(
                        "m-url",
                        "MapPack",
                        url="https://c10.patreonusercontent.com/fake/somewhere/pack.zip",
                    )
                ],
            ),
            (
                {
                    "id": "by-mime",
                    "type": "post",
                    "attributes": {
                        "title": "By mime",
                        "url": "https://example.invalid/posts/by-mime",
                        "published_at": "2026-06-02T15:00:00+00:00",
                        "current_user_can_view": True,
                        "content": "",
                    },
                    "relationships": {
                        "attachments_media": {"data": [{"id": "m-mime", "type": "media"}]}
                    },
                },
                [
                    {
                        "id": "m-mime",
                        "type": "media",
                        "attributes": {
                            "file_name": "MapPack",
                            "download_url": "https://c10.patreonusercontent.com/fake/noext/pack",
                            "mimetype": "application/zip",
                        },
                    }
                ],
            ),
        ]
    )
    by_id = {item.post_id: item for item in parse_posts_page(payload).posts}
    assert by_id["by-url"].attachments[0].name == "MapPack.zip"
    assert by_id["by-url"].attachments[0].kind == "zip"
    assert by_id["by-mime"].attachments[0].name == "MapPack.zip"
    assert by_id["by-mime"].attachments[0].kind == "zip"
    assert by_id["by-url"].attachments[0].name != "file"
    assert by_id["by-mime"].attachments[0].name != "file"


def test_dedup_same_url_once():
    url = "https://c10.patreonusercontent.com/fake/same-pack.zip"
    drop = "https://www.dropbox.com/scl/fo/example/SAMEID?<query-redacted>"
    doc = json.dumps(
        {
            "type": "doc",
            "content": [
                {
                    "type": "text",
                    "marks": [{"type": "link", "attrs": {"href": url}}],
                    "text": "zip",
                },
                {
                    "type": "text",
                    "marks": [{"type": "link", "attrs": {"href": drop}}],
                    "text": "Folder",
                },
                {
                    "type": "text",
                    "marks": [{"type": "link", "attrs": {"href": drop}}],
                    "text": "Direct download",
                },
            ],
        }
    )
    payload = {
        "data": [
            {
                "id": "dup-1",
                "type": "post",
                "attributes": {
                    "title": "Dup",
                    "url": "https://example.invalid/posts/dup-1",
                    "published_at": "2026-06-01T15:00:00+00:00",
                    "current_user_can_view": True,
                    "content": None,
                    "content_json_string": doc,
                },
                "relationships": {
                    "attachments_media": {"data": [{"id": "med-dup", "type": "media"}]}
                },
            }
        ],
        "included": [
            {
                "id": "med-dup",
                "type": "media",
                "attributes": {
                    "file_name": "same-pack.zip",
                    "download_url": url,
                    "mimetype": "application/zip",
                },
            }
        ],
        "links": {},
    }
    parsed = parse_posts_page(payload).posts[0]
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].url == url
    assert [link.url for link in parsed.outside_links] == [drop]


def test_sha256_duplicate_still_skipped(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LAM_PATREON_NOW", "2026-10-01T12:00:00-04:00")
    root = tmp_path / "fx"
    items = [
        post("p1", "Harbor", "2026-06-01T15:00:00+00:00", attachments=[attachment("m1", "map.zip")]),
        post("p2", "Other", "2026-06-02T15:00:00+00:00", attachments=[attachment("m2", "other.zip")]),
    ]
    write_fixture(root, "kidneyboy", [page(items)])
    (root / "files").mkdir()
    (root / "files" / "m1").write_bytes(b"same-bytes")
    (root / "files" / "m2").write_bytes(b"same-bytes")
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy")],
        staging_root=str(tmp_path / "stage"),
    )
    results = tmp_path / "sha.json"
    code = invoke(
        [
            "patreon",
            "sync",
            "--creators",
            str(cfg),
            "--fixture-dir",
            str(root),
            "--apply",
            "--since",
            "2025-01-01",
            "--results",
            str(results),
        ]
    )
    assert code == 0
    zips = [path for path in (tmp_path / "stage").rglob("*.zip") if path.is_file()]
    assert len(zips) == 1
    document = json.loads(results.read_text(encoding="utf-8"))
    statuses = [item["status"] for post in document["posts"] for item in post["files"]]
    assert "skipped-duplicate" in statuses
    assert "downloaded" in statuses


def test_locked_zip_preview_downloads_nothing(tmp_path: Path):
    FixtureSource.downloaded = []
    parsed = _by_id("partyoftwo-locked-zip.json")["147126125"]
    assert parsed.can_view is False
    assert parsed.attachments == []
    root = tmp_path / "fx"
    write_fixture(root, "partyoftwo", [_load("partyoftwo-locked-zip.json")])
    (root / "files").mkdir()
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Party_of_Two", "https://www.patreon.com/partyoftwo", campaign_id="9001")],
        staging_root=str(tmp_path / "stage"),
    )
    results = tmp_path / "locked.json"
    code = invoke(
        [
            "patreon",
            "sync",
            "--creators",
            str(cfg),
            "--fixture-dir",
            str(root),
            "--apply",
            "--since",
            "2025-01-01",
            "--until",
            "2026-02-01",
            "--results",
            str(results),
        ]
    )
    assert code == 0
    assert FixtureSource.downloaded == []
    document = json.loads(results.read_text(encoding="utf-8"))
    assert document["posts"][0]["post_id"] == "147126125"
    assert document["posts"][0]["status"] == "locked"
    assert document["posts"][0]["files"] == []


def test_year_2020_window_includes_jan1_local_midnight():
    window = _window("2020-01-01", "2021-01-01")
    assert in_window(parse_published("2020-01-01T05:00:00+00:00"), window) is True
    assert in_window(parse_published("2020-01-01T04:59:59+00:00"), window) is False
    assert in_window(parse_published("2021-01-01T05:00:00+00:00"), window) is False
    assert in_window(parse_published("2020-12-31T04:59:59+00:00"), window) is True


def test_year_2026_window_includes_jan1_excludes_next_jan1():
    window = _window("2026-01-01", "2027-01-01")
    assert in_window(parse_published("2026-01-01T05:00:00+00:00"), window) is True
    assert in_window(parse_published("2025-12-31T04:59:59+00:00"), window) is False
    assert in_window(parse_published("2027-01-01T04:59:59+00:00"), window) is True
    assert in_window(parse_published("2027-01-01T05:00:00+00:00"), window) is False


def test_media_relationship_zip_keeps_real_filename():
    payload = {
        "data": [
            {
                "id": "viewable-tower",
                "type": "post",
                "attributes": {
                    "title": "Infinite Tower 7-9 : $5 Rewards Post",
                    "url": "https://www.patreon.com/partyoftwo/posts/viewable-tower",
                    "published_at": "2026-01-02T15:00:00+00:00",
                    "current_user_can_view": True,
                    "content": None,
                    "post_file": None,
                },
                "relationships": {
                    "attachments": {"data": []},
                    "attachments_media": {"data": []},
                    "images": {"data": []},
                    "media": {"data": [{"id": "589526183", "type": "media"}]},
                },
            }
        ],
        "included": [
            {
                "id": "589526183",
                "type": "media",
                "attributes": {
                    "file_name": "1_InfiniteTower_Set7.zip",
                    "mimetype": "application/zip",
                    "download_url": TOWER_URL,
                    "owner_relationship": "attachment",
                    "size_bytes": 176691830,
                },
            }
        ],
        "links": {},
    }
    parsed = parse_posts_page(payload).posts[0]
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].name == "1_InfiniteTower_Set7.zip"
    assert parsed.attachments[0].kind == "zip"
    assert parsed.attachments[0].url == TOWER_URL
