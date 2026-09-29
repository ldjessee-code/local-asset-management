# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hand-built Patreon JSON:API fixtures. Fake ids only."""

from __future__ import annotations

import json
from pathlib import Path


def attachment(media_id: str, name: str, *, size: int = 128, url: str | None = None) -> dict:
    return {
        "id": media_id,
        "type": "attachment",
        "attributes": {
            "name": name,
            "url": url or f"https://example.invalid/files/{media_id}/{name}",
            "size": size,
        },
    }


def image(media_id: str, name: str, *, size: int = 64, url: str | None = None) -> dict:
    return {
        "id": media_id,
        "type": "media",
        "attributes": {
            "file_name": name,
            "download_url": url or f"https://example.invalid/files/{media_id}/{name}",
            "mimetype": "image/png",
            "size_bytes": size,
        },
    }


def other_media(media_id: str, name: str) -> dict:
    return {
        "id": media_id,
        "type": "widget",
        "attributes": {"name": name, "url": f"https://example.invalid/files/{media_id}/{name}"},
    }


def post(
    post_id: str,
    title: str,
    published: str | None,
    *,
    can_view: bool = True,
    content: str = "",
    attachments: list[dict] | None = None,
    images: list[dict] | None = None,
    extras: list[dict] | None = None,
    omit: tuple[str, ...] = (),
) -> tuple[dict, list[dict]]:
    included = list(attachments or []) + list(images or []) + list(extras or [])
    attributes = {
        "title": title,
        "url": f"https://example.invalid/posts/{post_id}",
        "published_at": published,
        "current_user_can_view": can_view,
        "content": content,
    }
    for key in omit:
        attributes.pop(key, None)
    resource = {
        "id": post_id,
        "type": "post",
        "attributes": attributes,
        "relationships": {
            "attachments": {
                "data": [{"id": item["id"], "type": "attachment"} for item in (attachments or [])]
            },
            "images": {"data": [{"id": item["id"], "type": "media"} for item in (images or [])]},
        },
    }
    if extras:
        resource["relationships"]["media"] = {
            "data": [{"id": item["id"], "type": item["type"]} for item in extras]
        }
    return resource, included


def page(items: list[tuple[dict, list[dict]]], *, next_link: str | None = None) -> dict:
    data = []
    included = []
    for resource, extra in items:
        data.append(resource)
        included.extend(extra)
    body: dict = {"data": data, "included": included, "links": {}}
    if next_link is not None:
        body["links"]["next"] = next_link
    return body


def write_fixture(
    root: Path,
    vanity: str,
    pages: list[dict],
    *,
    campaign_id: str = "9001",
    member: bool = True,
    free: bool = False,
    whoami: bool = True,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if whoami and not (root / "whoami.json").exists():
        (root / "whoami.json").write_text(
            json.dumps({"data": {"id": "user-fixture", "attributes": {"full_name": "Fixture Patron"}}})
            + "\n",
            encoding="utf-8",
        )
    pledge: dict
    if member:
        pledge = {"data": {"id": f"pledge-{vanity}", "type": "pledge"}}
    else:
        pledge = {"data": None}
    campaign = {
        "data": {
            "id": campaign_id,
            "type": "campaign",
            "attributes": {
                "vanity": vanity,
                "name": vanity,
                "current_user_is_free_member": free,
            },
            "relationships": {"current_user_pledge": pledge},
        }
    }
    (root / f"campaign-{vanity}.json").write_text(json.dumps(campaign) + "\n", encoding="utf-8")
    (root / f"posts-{vanity}.json").write_text(
        json.dumps({"pages": pages}) + "\n",
        encoding="utf-8",
    )


def creator(
    slug: str,
    url: str,
    *,
    enabled: bool = True,
    campaign_id: str | None = None,
    filters: dict | None = None,
    staging_folder: str | None = None,
) -> dict:
    item = {
        "slug": slug,
        "display_name": slug.replace("_", " "),
        "patreon_url": url,
        "campaign_id": campaign_id,
        "enabled": enabled,
    }
    if staging_folder:
        item["staging_folder"] = staging_folder
    if filters is not None:
        item["filters"] = filters
    return item


def write_config(path: Path, creators: list[dict], *, staging_root: str) -> Path:
    doc = {
        "schema": "patreon-creators/v1",
        "staging_root": staging_root,
        "creators": creators,
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path
