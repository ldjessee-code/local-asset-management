# SPDX-License-Identifier: AGPL-3.0-or-later
"""Membership signals and downloadable attachment sources.

Fixtures are fake JSON:API documents. No network and no cookie value.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from lam.patreon.http_source import FixtureSource, HttpPatreonSource, RequestClock, _campaign_from_payload
from lam.patreon.parse import parse_posts_page
from lam.token.secret import Secret
from tests.patreon.builders import creator, write_config
from tests.patreon.conftest import invoke

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "patreon"
FERRY_URL = "https://c10.patreonusercontent.com/fake/car-ferry-legend.zip"
BUNGALOW_URL = "https://c10.patreonusercontent.com/fake/bungalow-legend.zip"
SURVIVOR_URL = "https://c10.patreonusercontent.com/fake/car-ferry-survivor.zip"
CONTENT_URL = "https://c10.patreonusercontent.com/fake/content-pack.zip"
FILE_INCLUDE = ("attachments", "attachments_media", "audio")


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _posts() -> dict:
    return _load("posts-attachment-sources.json")


def _by_id(payload: dict | None = None) -> dict:
    parsed = parse_posts_page(payload if payload is not None else _posts())
    return {post.post_id: post for post in parsed.posts}


def _prepare(tmp_path: Path, vanity: str, campaign: dict, page: dict) -> Path:
    root = tmp_path / f"fx-{vanity}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "whoami.json").write_text(
        json.dumps({"data": {"id": "user-fixture", "attributes": {"full_name": "Fixture Patron"}}}) + "\n",
        encoding="utf-8",
    )
    (root / f"campaign-{vanity}.json").write_text(json.dumps(campaign) + "\n", encoding="utf-8")
    (root / f"posts-{vanity}.json").write_text(json.dumps({"pages": [page]}) + "\n", encoding="utf-8")
    return root


def _run_list(tmp_path: Path, vanity: str, campaign: dict, page: dict, capsys, *, fmt: str = "text", slug: str = "Kidney_Boy"):
    root = _prepare(tmp_path, vanity, campaign, page)
    cfg = write_config(
        tmp_path / f"creators-{vanity}-{fmt}.json",
        [creator(slug, f"https://www.patreon.com/{vanity}")],
        staging_root=str(tmp_path / "stage"),
    )
    argv = [
        "patreon",
        "list",
        "--creators",
        str(cfg),
        "--fixture-dir",
        str(root),
        "--since",
        "2025-09-28",
        "--until",
        "2026-09-29",
    ]
    if fmt == "json":
        argv.extend(["--format", "json"])
    code = invoke(argv)
    return code, capsys.readouterr().out


def _source(handler) -> HttpPatreonSource:
    return HttpPatreonSource(
        Secret("session_id=fixture-session"),
        transport=httpx.MockTransport(handler),
        clock=RequestClock(),
        min_delay=0.0,
    )


def _resolve(user_payload: dict) -> tuple[object, list[tuple[str, str | None]]]:
    campaign = _load("campaign-free-false-no-pledge.json")
    calls: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.url.params.get("include")))
        if request.url.path.rstrip("/").endswith("/current_user"):
            return httpx.Response(200, json=user_payload)
        if "campaigns" in request.url.path:
            return httpx.Response(200, json=campaign)
        return httpx.Response(200, json={"data": [], "included": [], "links": {}})

    info = _source(handler).resolve_campaign("kidneyboy", "9001")
    return info, calls


def _saw_include(calls: list[tuple[str, str | None]], path_part: str, needle: str) -> bool:
    """True when ``include=`` lists ``needle`` or a nested ``needle.something``."""
    for path, include in calls:
        if path_part not in path or not include:
            continue
        for part in include.split(","):
            if part == needle or part.startswith(needle + "."):
                return True
    return False


def test_campaign_included_pledge_is_member():
    info = _campaign_from_payload(_load("campaign-pledge-included.json"), "kidneyboy", None)
    assert info.is_member is True
    assert info.not_a_member is False
    assert info.campaign_id == "9001"


def test_paid_patron_with_pledge_and_free_member_false_is_member():
    payload = _load("campaign-pledge-included.json")
    attrs = payload["data"]["attributes"]
    assert attrs["current_user_is_free_member"] is False
    assert payload["data"]["relationships"]["current_user_pledge"]["data"]["id"] == "pledge-9001"
    info = _campaign_from_payload(payload, "kidneyboy", "9001")
    assert info.is_member is True
    assert info.not_a_member is False


def test_free_member_false_alone_is_not_a_non_member():
    payload = _load("campaign-free-false-no-pledge.json")
    assert payload["data"]["attributes"]["current_user_is_free_member"] is False
    assert "relationships" not in payload["data"]
    info = _campaign_from_payload(payload, "kidneyboy", "9001")
    assert info.not_a_member is False


def test_current_user_active_patron_for_this_campaign_is_member():
    info, calls = _resolve(_load("current-user-active-patron.json"))
    assert _saw_include(calls, "/campaigns/", "current_user_pledge")
    assert _saw_include(calls, "/current_user", "memberships")
    assert info.is_member is True
    assert info.not_a_member is False


def test_current_user_entitled_cents_is_member():
    info, calls = _resolve(_load("current-user-entitled-cents.json"))
    assert _saw_include(calls, "/current_user", "memberships")
    assert info.is_member is True
    assert info.not_a_member is False


def test_current_user_former_patron_is_not_member():
    info, calls = _resolve(_load("current-user-former-patron.json"))
    assert _saw_include(calls, "/current_user", "memberships")
    assert info.is_member is False
    assert info.not_a_member is True


def test_current_user_other_campaign_is_not_member():
    info, calls = _resolve(_load("current-user-other-campaign.json"))
    assert _saw_include(calls, "/current_user", "memberships")
    assert info.is_member is False
    assert info.not_a_member is True


def test_no_pledge_no_membership_no_viewable_gated_posts_is_not_a_member(tmp_path: Path, capsys):
    code, out = _run_list(
        tmp_path,
        "stranger",
        _load("campaign-explicit-null-pledge.json"),
        _load("posts-locked-only.json"),
        capsys,
        slug="Stranger",
    )
    assert code == 0
    assert "not a member" in out
    assert "Hidden rewards" in out


def test_viewable_gated_post_fallback_marks_member(tmp_path: Path, capsys):
    code, out = _run_list(
        tmp_path,
        "kidneyboy",
        _load("campaign-free-false-no-pledge.json"),
        _load("posts-gated-viewable.json"),
        capsys,
    )
    assert code == 0
    assert "Legend drop" in out
    assert "not a member" not in out
    code_json, raw = _run_list(
        tmp_path,
        "kidneyboy",
        _load("campaign-free-false-no-pledge.json"),
        _load("posts-gated-viewable.json"),
        capsys,
        fmt="json",
    )
    assert code_json == 0
    document = json.loads(raw)
    assert document["creators"][0]["not_a_member"] is False
    assert not any("not a member" in item for item in document["warnings"])


def test_indeterminate_posts_do_not_print_not_a_member(tmp_path: Path, capsys):
    code, out = _run_list(
        tmp_path,
        "kidneyboy",
        _load("campaign-free-false-no-pledge.json"),
        _load("posts-indeterminate.json"),
        capsys,
    )
    assert code == 0
    assert "Field Notes" in out
    assert "not a member" not in out
    _code_json, raw = _run_list(
        tmp_path,
        "kidneyboy",
        _load("campaign-free-false-no-pledge.json"),
        _load("posts-indeterminate.json"),
        capsys,
        fmt="json",
    )
    document = json.loads(raw)
    assert document["creators"][0]["not_a_member"] is False
    assert document["creators"][0]["posts"][0]["post_title"] == "Field Notes"
    assert not any("not a member" in item for item in document["warnings"])


def test_public_viewable_post_does_not_grant_membership(tmp_path: Path, capsys):
    code, out = _run_list(
        tmp_path,
        "stranger",
        _load("campaign-explicit-null-pledge.json"),
        _load("posts-public-viewable.json"),
        capsys,
        slug="Stranger",
    )
    assert code == 0
    assert "Public note" in out
    assert "not a member" in out


def test_attachments_media_zip_url_and_filename():
    by_id = _by_id()
    ferry = by_id["163923452"]
    assert ferry.can_view is True
    assert len(ferry.attachments) == 1
    item = ferry.attachments[0]
    assert item.name == "Car Ferry Legend Rewards.zip"
    assert item.url == FERRY_URL
    assert item.kind == "zip"
    chart = by_id["map-88"]
    assert len(chart.images) == 1
    assert chart.images[0].name == "map-88.png"
    assert len(chart.attachments) == 0


def test_legacy_attachments_still_counted():
    legacy = _by_id()["legacy-1"]
    assert len(legacy.attachments) == 1
    assert legacy.attachments[0].name == "Harbor Legacy.zip"
    assert legacy.attachments[0].url == "https://c10.patreonusercontent.com/fake/harbor-legacy.zip"
    assert legacy.attachments[0].kind == "zip"


def test_post_file_non_image_counted_once_when_duplicate_url():
    by_id = _by_id()
    only = by_id["only-post-file"]
    assert len(only.attachments) == 1
    assert only.attachments[0].name == "Only Post File.zip"
    assert only.attachments[0].url == "https://c10.patreonusercontent.com/fake/only-post-file.zip"
    assert only.attachments[0].kind == "zip"
    duplicate = by_id["bungalow-dup"]
    assert len(duplicate.attachments) == 1
    assert duplicate.attachments[0].url == BUNGALOW_URL
    assert duplicate.attachments[0].name.endswith(".zip")
    preview = by_id["image-post-file"]
    assert preview.attachments == []


def test_content_file_link_on_patreon_host_is_attachment():
    post = _by_id()["content-1"]
    assert len(post.attachments) == 1
    assert post.attachments[0].url == CONTENT_URL
    assert post.attachments[0].name == "content-pack.zip"
    assert post.attachments[0].kind == "zip"
    hosts = [link.host for link in post.outside_links]
    assert "dropbox.com" in hosts
    assert all(link.url != CONTENT_URL for link in post.outside_links)


def test_locked_post_has_zero_downloadable_attachments(tmp_path: Path, capsys):
    locked = _by_id()["locked-surv"]
    assert locked.can_view is False
    assert locked.attachments == []
    assert all(item.url != SURVIVOR_URL for item in locked.attachments)
    FixtureSource.downloaded = []
    page = _posts()
    resource = next(item for item in page["data"] if item["id"] == "locked-surv")
    kept = {"att-locked", "med-locked"}
    slim = {
        "data": [resource],
        "included": [item for item in page["included"] if str(item.get("id")) in kept],
        "links": {},
    }
    root = _prepare(tmp_path, "kidneyboy", _load("campaign-pledge-included.json"), slim)
    (root / "files").mkdir()
    cfg = write_config(
        tmp_path / "creators-locked.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy")],
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
            "2025-09-28",
            "--until",
            "2026-09-29",
            "--results",
            str(results),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert FixtureSource.downloaded == []
    assert SURVIVOR_URL not in " ".join(FixtureSource.downloaded)
    document = json.loads(results.read_text(encoding="utf-8"))
    assert document["posts"][0]["status"] == "locked"
    assert document["posts"][0]["files"] == []
    assert SURVIVOR_URL not in results.read_text(encoding="utf-8")
    assert "failed" not in captured.out.lower()


def test_list_posts_include_covers_parser_relationships():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("include"):
            calls.append(str(request.url.params.get("include")))
        return httpx.Response(200, json={"data": [], "included": [], "links": {}})

    _source(handler).list_posts("kidneyboy", "9001")
    assert calls
    names = set(calls[0].split(","))
    for name in (*FILE_INCLUDE, "images", "media"):
        assert name in names
    audio = _by_id()["audio-1"]
    assert len(audio.attachments) == 1
    assert audio.attachments[0].name == "session.mp3"
    assert audio.attachments[0].url == "https://c10.patreonusercontent.com/fake/session.mp3"


def test_list_text_and_json_member_shows_attachment_and_not_non_member(tmp_path: Path, capsys):
    campaign = _load("campaign-pledge-included.json")
    page = _posts()
    code, out = _run_list(tmp_path, "kidneyboy", campaign, page, capsys)
    assert code == 0
    assert "not a member" not in out
    legend = next(line for line in out.splitlines() if "Car Ferry Legend Rewards.zip" in line)
    assert "accessible" in legend
    assert "1 attachments" in legend
    locked = next(line for line in out.splitlines() if "Car Ferry Survivor Rewards.zip" in line)
    assert "locked" in locked
    assert "0 attachments" in locked
    chart = next(line for line in out.splitlines() if "Map #88: Car Ferry" in line)
    assert "1 images" in chart
    code_json, raw = _run_list(tmp_path, "kidneyboy", campaign, page, capsys, fmt="json")
    assert code_json == 0
    document = json.loads(raw)
    assert document["schema"] == "patreon-post-list/v1"
    creator_row = document["creators"][0]
    assert creator_row["not_a_member"] is False
    assert not any("not a member" in item for item in document["warnings"])
    rows = {item["post_id"]: item for item in creator_row["posts"]}
    assert rows["163923452"]["attachments"] >= 1
    assert rows["163923452"]["access"] == "accessible"
    assert rows["locked-surv"]["attachments"] == 0
    assert rows["locked-surv"]["access"] == "locked"
    assert rows["map-88"]["images"] == 1
