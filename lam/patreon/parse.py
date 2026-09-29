# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parse a Patreon JSON:API posts page. No network."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

from lam.patreon.dates import parse_published

_HREF = re.compile(r"""href\s*=\s*["'](https?://[^"'>\s]+)["']""", re.IGNORECASE)
_SKIP_HOSTS = ("patreon.com", "patreonusercontent.com")


@dataclass
class MediaItem:
    media_id: str
    name: str
    url: str
    kind: str
    source_kind: str
    size: int | None = None


@dataclass
class OutsideLink:
    host: str
    url: str


@dataclass
class PostRecord:
    post_id: str
    title: str
    url: str
    published: datetime | None
    can_view: bool
    attachments: list[MediaItem] = field(default_factory=list)
    images: list[MediaItem] = field(default_factory=list)
    extras: list[MediaItem] = field(default_factory=list)
    outside_links: list[OutsideLink] = field(default_factory=list)
    missing_published: bool = False


@dataclass
class ParsedPage:
    posts: list[PostRecord]
    next_link: str | None
    next_malformed: bool


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _kind(name: str, source_kind: str, mimetype: str) -> str:
    lower = (name or "").lower()
    if lower.endswith(".zip"):
        return "zip"
    if source_kind == "image" or mimetype.startswith("image/") or lower.endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".webp")
    ):
        return "loose-media"
    return "other"


def _media_from_included(item: dict, source_kind: str) -> MediaItem:
    attrs = item.get("attributes") or {}
    name = str(attrs.get("name") or attrs.get("file_name") or item.get("id") or "file")
    image_urls = attrs.get("image_urls") or {}
    url = str(
        attrs.get("url")
        or attrs.get("download_url")
        or (image_urls.get("original") if isinstance(image_urls, dict) else "")
        or (image_urls.get("default") if isinstance(image_urls, dict) else "")
        or ""
    )
    size = attrs.get("size", attrs.get("size_bytes"))
    if isinstance(size, bool) or not isinstance(size, int):
        size = None
    mimetype = str(attrs.get("mimetype") or "")
    return MediaItem(
        media_id=str(item.get("id") or ""),
        name=name,
        url=url,
        kind=_kind(name, source_kind, mimetype),
        source_kind=source_kind,
        size=size,
    )


def _outside_links(content: object) -> list[OutsideLink]:
    if not isinstance(content, str) or not content:
        return []
    found: list[OutsideLink] = []
    seen: set[str] = set()
    for url in _HREF.findall(content):
        host = _host(url)
        if not host or host.endswith(_SKIP_HOSTS) or url in seen:
            continue
        seen.add(url)
        found.append(OutsideLink(host=host, url=url))
    return found


def _rel_ids(resource: dict, name: str) -> list[tuple[str, str]]:
    rel = (resource.get("relationships") or {}).get(name) or {}
    data = rel.get("data")
    if data is None:
        return []
    if isinstance(data, dict):
        data = [data]
    pairs = []
    for item in data:
        if isinstance(item, dict) and item.get("id"):
            pairs.append((str(item.get("type") or ""), str(item["id"])))
    return pairs


def parse_posts_page(payload: dict) -> ParsedPage:
    included: dict[str, dict] = {}
    for item in payload.get("included") or []:
        if isinstance(item, dict) and item.get("id") is not None:
            included[str(item["id"])] = item
    posts: list[PostRecord] = []
    for resource in payload.get("data") or []:
        if not isinstance(resource, dict):
            continue
        attrs = resource.get("attributes") or {}
        post_id = str(resource.get("id") or "")
        raw_published = attrs.get("published_at") if "published_at" in attrs else None
        published = parse_published(raw_published) if "published_at" in attrs else None
        missing = "published_at" not in attrs or published is None and "published_at" in attrs or (
            "published_at" in attrs and not raw_published
        )
        if "published_at" not in attrs:
            missing = True
            published = None
        elif published is None:
            missing = True
        else:
            missing = False
        url = attrs.get("url")
        if not isinstance(url, str) or not url:
            url = f"https://www.patreon.com/posts/{post_id}"
        can_view = attrs.get("current_user_can_view")
        if can_view is None:
            can_view = True
        attachments = []
        for _kind_name, media_id in _rel_ids(resource, "attachments"):
            item = included.get(media_id)
            if item:
                attachments.append(_media_from_included(item, "attachment"))
        images = []
        for _kind_name, media_id in _rel_ids(resource, "images"):
            item = included.get(media_id)
            if item:
                images.append(_media_from_included(item, "image"))
        known = {media.media_id for media in attachments + images}
        extras = []
        for rel_name, rel in (resource.get("relationships") or {}).items():
            if rel_name in {"attachments", "images"}:
                continue
            for _kind_name, media_id in _rel_ids(resource, rel_name):
                if media_id in known:
                    continue
                item = included.get(media_id)
                if item:
                    extras.append(_media_from_included(item, "other"))
                    known.add(media_id)
        posts.append(
            PostRecord(
                post_id=post_id,
                title=str(attrs.get("title") or ""),
                url=str(url),
                published=published,
                can_view=bool(can_view),
                attachments=attachments,
                images=images,
                extras=extras,
                outside_links=_outside_links(attrs.get("content")),
                missing_published=missing,
            )
        )
    links = payload.get("links") or {}
    raw_next = links.get("next") if isinstance(links, dict) else None
    next_link: str | None = None
    malformed = False
    if raw_next in (None, ""):
        next_link = None
    elif isinstance(raw_next, str) and raw_next.startswith(("http://", "https://")):
        next_link = raw_next
    else:
        malformed = True
    return ParsedPage(posts=posts, next_link=next_link, next_malformed=malformed)
