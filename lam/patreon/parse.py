# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parse a Patreon JSON:API posts page. No network."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

from lam.patreon.dates import parse_published

_HREF = re.compile(r"""href\s*=\s*["'](https?://[^"'>\s]+)["']""", re.IGNORECASE)
_SKIP_HOSTS = ("patreon.com", "patreonusercontent.com")
# Downloadable file relationships. ``HttpPatreonSource.list_posts`` must
# request these names in ``include=`` or the included resources are absent.
FILE_RELATIONSHIPS = ("attachments", "attachments_media", "audio")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_FILE_EXT = (
    ".zip",
    ".rar",
    ".7z",
    ".pdf",
    ".psd",
    ".uvtt",
    ".mp3",
    ".wav",
    ".mp4",
    ".mov",
    ".blend",
)
_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/pdf": ".pdf",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}
_TIER_RELS = ("access_rules", "rewards", "reward", "tiers")


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
    # True tier-gated, False public, None when the payload does not say.
    tier_gated: bool | None = None


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


def _ext_from_name(name: str) -> str:
    lower = (name or "").lower().rsplit("/", 1)[-1]
    dot = lower.rfind(".")
    if dot <= 0:
        return ""
    ext = lower[dot:]
    if 1 < len(ext) <= 8 and ext[1:].isalnum():
        return ext
    return ""


def _url_tail(url: str) -> str:
    path = urlparse(url).path
    return path.rstrip("/").rsplit("/", 1)[-1]


def _ext_from_url(url: str) -> str:
    return _ext_from_name(_url_tail(url))


def _mime_ext(mimetype: str) -> str:
    return _MIME_EXT.get((mimetype or "").split(";", 1)[0].strip().lower(), "")


def _with_extension(name: str, url: str, mimetype: str, *, fallback: str = "") -> str:
    """Keep a real extension, or add one from the URL path or mimetype."""
    base = (name or "").strip()
    if not base:
        tail = _url_tail(url)
        if tail:
            base = tail
    if not base:
        base = (fallback or "").strip() or "file"
    if _ext_from_name(base):
        return base
    extra = _ext_from_url(url) or _mime_ext(mimetype)
    if extra and not base.lower().endswith(extra):
        return base + extra
    return base


def _kind(name: str, source_kind: str, mimetype: str) -> str:
    lower = (name or "").lower()
    mime = (mimetype or "").split(";", 1)[0].strip().lower()
    if lower.endswith(".zip") or mime in {"application/zip", "application/x-zip-compressed"}:
        return "zip"
    if source_kind == "image" or mime.startswith("image/") or lower.endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".webp")
    ):
        return "loose-media"
    return "other"


def _media_from_included(item: dict, source_kind: str) -> MediaItem:
    attrs = item.get("attributes") or {}
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
    name = _with_extension(
        str(attrs.get("name") or attrs.get("file_name") or ""),
        url,
        mimetype,
        fallback=str(item.get("id") or ""),
    )
    return MediaItem(
        media_id=str(item.get("id") or ""),
        name=name,
        url=url,
        kind=_kind(name, source_kind, mimetype),
        source_kind=source_kind,
        size=size,
    )


def _looks_like_image(name: str, mimetype: str = "", url: str = "") -> bool:
    if (mimetype or "").lower().startswith("image/"):
        return True
    if _ext_from_name(name) in _IMAGE_EXT:
        return True
    return bool(url) and _ext_from_url(url) in _IMAGE_EXT


def _downloadable_non_image(item: dict) -> bool:
    """A non-image file on ``media`` that has a URL and a known file type."""
    attrs = item.get("attributes") or {}
    name = str(attrs.get("name") or attrs.get("file_name") or "")
    mimetype = str(attrs.get("mimetype") or "")
    url = str(attrs.get("url") or attrs.get("download_url") or "")
    if not url or _looks_like_image(name, mimetype, url):
        return False
    ext = _ext_from_name(name) or _ext_from_url(url) or _mime_ext(mimetype)
    return ext in _FILE_EXT


def _tier_gated(attrs: dict, resource: dict) -> bool | None:
    """Whether this post is tier-gated rather than public.

    ``None`` means the payload did not say. A viewable post in that state
    must not be treated as proof of membership.
    """
    is_public = attrs.get("is_public")
    is_paid = attrs.get("is_paid")
    min_cents = attrs.get("min_cents_pledged_to_view")
    if isinstance(min_cents, str) and min_cents.strip().isdigit():
        min_cents = int(min_cents.strip())
    gated_rel = any(_rel_ids(resource, name) for name in _TIER_RELS)
    cents_gated = isinstance(min_cents, int) and not isinstance(min_cents, bool) and min_cents > 0
    if is_paid is True or is_public is False or gated_rel or cents_gated:
        return True
    if is_public is True or is_paid is False:
        return False
    return None


def _add_file(bucket: list[MediaItem], seen_urls: set[str], item: MediaItem) -> None:
    url = item.url or ""
    if not url or url in seen_urls:
        return
    seen_urls.add(url)
    bucket.append(item)


def _post_file_item(attrs: dict, post_id: str) -> MediaItem | None:
    raw = attrs.get("post_file")
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "")
    url = str(raw.get("url") or "")
    mimetype = str(raw.get("mimetype") or "")
    if not url or _looks_like_image(name, mimetype, url):
        return None
    name = _with_extension(name, url, mimetype)
    return MediaItem(
        media_id=f"post-file-{post_id}",
        name=name,
        url=url,
        kind=_kind(name, "attachment", mimetype),
        source_kind="post_file",
    )


def _collect_hrefs(node: object, found: list[str]) -> None:
    if isinstance(node, dict):
        attrs = node.get("attrs")
        if isinstance(attrs, dict):
            href = attrs.get("href")
            if isinstance(href, str) and href.startswith(("http://", "https://")):
                found.append(href)
        for value in node.values():
            _collect_hrefs(value, found)
    elif isinstance(node, list):
        for value in node:
            _collect_hrefs(value, found)


def _document_hrefs(content: object, content_json: object) -> list[str]:
    """HTML ``href`` plus link marks in ``content_json_string``.

    The posts list leaves ``content`` null and puts the body in
    ``content_json_string``.
    """
    found: list[str] = []
    if isinstance(content, str) and content:
        found.extend(_HREF.findall(content))
    if isinstance(content_json, str) and content_json.strip().startswith("{"):
        try:
            document = json.loads(content_json)
        except json.JSONDecodeError:
            document = None
        if document is not None:
            _collect_hrefs(document, found)
    return found


def _content_files(hrefs: list[str], post_id: str) -> list[MediaItem]:
    """File links on Patreon hosts. Other hosts stay outside links."""
    found: list[MediaItem] = []
    seen: set[str] = set()
    for url in hrefs:
        if url in seen:
            continue
        host = _host(url)
        name = _with_extension(_url_tail(url), url, "")
        if not name or _looks_like_image(name, "", url) or _ext_from_name(name) not in _FILE_EXT:
            continue
        on_cdn = "patreonusercontent.com" in host
        on_site = host.endswith("patreon.com")
        if not on_cdn and not on_site:
            continue
        seen.add(url)
        found.append(
            MediaItem(
                media_id=f"content-{post_id}-{len(found) + 1}",
                name=name,
                url=url,
                kind=_kind(name, "attachment", ""),
                source_kind="content",
            )
        )
    return found


def _outside_links(hrefs: list[str]) -> list[OutsideLink]:
    found: list[OutsideLink] = []
    seen: set[str] = set()
    for url in hrefs:
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


def _lookup(included_typed: dict[tuple[str, str], dict], included_id: dict[str, dict], type_name: str, media_id: str):
    found = included_typed.get((type_name, media_id))
    if found is not None:
        return found
    return included_id.get(media_id)


def parse_posts_page(payload: dict) -> ParsedPage:
    included_typed: dict[tuple[str, str], dict] = {}
    included_id: dict[str, dict] = {}
    for item in payload.get("included") or []:
        if isinstance(item, dict) and item.get("id") is not None:
            media_id = str(item["id"])
            included_id[media_id] = item
            included_typed[(str(item.get("type") or ""), media_id)] = item
    posts: list[PostRecord] = []
    raw_data = payload.get("data")
    if isinstance(raw_data, dict):
        resources = [raw_data]
    elif isinstance(raw_data, list):
        resources = raw_data
    else:
        resources = []
    for resource in resources:
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
        viewable = bool(can_view)
        # Locked posts keep no downloadable file, even when the relationship is present.
        attachments: list[MediaItem] = []
        seen_urls: set[str] = set()
        if viewable:
            for rel_name in FILE_RELATIONSHIPS:
                for type_name, media_id in _rel_ids(resource, rel_name):
                    item = _lookup(included_typed, included_id, type_name, media_id)
                    if item:
                        source_kind = "attachment" if rel_name == "attachments" else rel_name
                        _add_file(attachments, seen_urls, _media_from_included(item, source_kind))
            post_file = _post_file_item(attrs, post_id)
            if post_file is not None:
                _add_file(attachments, seen_urls, post_file)
            hrefs = _document_hrefs(attrs.get("content"), attrs.get("content_json_string"))
            for content_item in _content_files(hrefs, post_id):
                _add_file(attachments, seen_urls, content_item)
            for type_name, media_id in _rel_ids(resource, "media"):
                item = _lookup(included_typed, included_id, type_name, media_id)
                if item and _downloadable_non_image(item):
                    _add_file(attachments, seen_urls, _media_from_included(item, "attachment"))
        else:
            hrefs = _document_hrefs(attrs.get("content"), attrs.get("content_json_string"))
        images = []
        for type_name, media_id in _rel_ids(resource, "images"):
            item = _lookup(included_typed, included_id, type_name, media_id)
            if item:
                images.append(_media_from_included(item, "image"))
        known = {media.media_id for media in attachments + images}
        extras = []
        skip_rels = set(FILE_RELATIONSHIPS) | {"images"}
        for rel_name in (resource.get("relationships") or {}):
            if rel_name in skip_rels:
                continue
            for type_name, media_id in _rel_ids(resource, rel_name):
                if media_id in known:
                    continue
                item = _lookup(included_typed, included_id, type_name, media_id)
                if item:
                    extras.append(_media_from_included(item, "other"))
                    known.add(media_id)
        posts.append(
            PostRecord(
                post_id=post_id,
                title=str(attrs.get("title") or ""),
                url=str(url),
                published=published,
                can_view=viewable,
                attachments=attachments,
                images=images,
                extras=extras,
                outside_links=_outside_links(hrefs),
                missing_published=missing,
                tier_gated=_tier_gated(attrs, resource),
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
