# SPDX-License-Identifier: AGPL-3.0-or-later
"""List and stage Patreon posts. Dry-run writes no staging folders."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from lam.patreon.config import Creator, CreatorsConfig
from lam.patreon.dates import Window, in_window, now_local
from lam.patreon.errors import PatreonError, PatreonItemError
from lam.patreon.names import sanitize_component
from lam.patreon.parse import MediaItem, PostRecord
from lam.schemas.registry import (
    PATREON_INDEX_SCHEMA_ID,
    PATREON_MANIFEST_SCHEMA_ID,
    PATREON_POST_LIST_SCHEMA_ID,
)

MAX_PAGES = 40
_FILE_STATUSES = {
    "downloaded",
    "deferred",
    "failed",
    "skipped-duplicate",
    "locked",
    "excluded",
    "planned",
}


def _blank_index(slug: str) -> dict:
    return {
        "schema": PATREON_INDEX_SCHEMA_ID,
        "creator_slug": slug,
        "campaign_id": None,
        "updated": "",
        "media": {},
        "sha256": {},
        "posts": {},
    }


def _load_index(path: Path, slug: str) -> dict:
    if not path.is_file():
        return _blank_index(slug)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return _blank_index(slug)
    base = _blank_index(slug)
    for key in ("media", "sha256", "posts"):
        if isinstance(data.get(key), dict):
            base[key] = data[key]
    if isinstance(data.get("campaign_id"), str) or data.get("campaign_id") is None:
        base["campaign_id"] = data.get("campaign_id")
    return base


def _save_index(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".lam-tmp-{uuid.uuid4().hex}-patreon-index.json"
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)


def _fresh_manifest(inbox: Path, stamp: str, suffix: str) -> Path:
    """Pick a name that does not exist. Same-second reruns get a numeric suffix."""
    candidate = inbox / f"drop-manifest_{stamp}{suffix}"
    if not candidate.exists():
        return candidate
    for number in range(2, 100):
        alt = inbox / f"drop-manifest_{stamp}_{number}{suffix}"
        if not alt.exists():
            return alt
    raise PatreonItemError("error: could not pick a new manifest name")


def _exclude_pattern(title: str, creator: Creator) -> str | None:
    for pattern in creator.filters.exclude_title_regex:
        if re.search(pattern, title or ""):
            return pattern
    return None


def _include_ok(title: str, creator: Creator) -> bool:
    patterns = creator.filters.include_title_regex
    if not patterns:
        return True
    return any(re.search(pattern, title or "") for pattern in patterns)


def _selected_media(post: PostRecord, creator: Creator) -> list[MediaItem]:
    chosen: list[MediaItem] = []
    types = set(creator.filters.media_types)
    if "attachment" in types:
        chosen.extend(post.attachments)
    if "image" in types:
        chosen.extend(post.images)
    return chosen


def access_label(post: PostRecord, creator: Creator) -> str:
    pattern = _exclude_pattern(post.title, creator)
    if pattern is not None or not _include_ok(post.title, creator):
        if pattern and "chronos" in pattern.lower():
            return "excluded (chronos)"
        return "excluded"
    if not post.can_view:
        return "locked"
    if not _selected_media(post, creator) and post.outside_links:
        return "deferred (outside link)"
    return "accessible"


def _too_big(media: MediaItem, creator: Creator) -> bool:
    limit = creator.filters.max_file_mb
    if limit is None or media.size is None:
        return False
    return media.size > limit * 1024 * 1024


def _before_published_after(post: PostRecord, creator: Creator, zone) -> bool:
    raw = creator.filters.published_after
    if not raw or post.published is None:
        return False
    try:
        year, month, day = (int(part) for part in raw[:10].split("-"))
        start = datetime(year, month, day, tzinfo=zone)
    except (TypeError, ValueError):
        return False
    return post.published.astimezone(zone) < start


def collect_posts(source, creator: Creator, window: Window, max_posts: int | None):
    """Return ``(posts, warnings)``. Stops at the first post older than the window."""
    info = source.resolve_campaign(creator.vanity, creator.campaign_id)
    warnings: list[str] = []
    if info.not_a_member:
        warnings.append(f"{creator.slug}: not a member")
    posts: list[PostRecord] = []
    cursor = None
    pages = 0
    while pages < MAX_PAGES:
        page = source.list_posts(creator.vanity, info.campaign_id, cursor=cursor)
        pages += 1
        stop = False
        for post in page.posts:
            state = in_window(post.published, window)
            if state is False or _before_published_after(post, creator, window.zone):
                stop = True
                break
            if state == "missing":
                warnings.append(f"{creator.slug}: post {post.post_id} has no published_at")
                post.missing_published = True
            posts.append(post)
            if max_posts is not None and len(posts) >= max_posts:
                stop = True
                break
        if stop:
            break
        if page.next_malformed:
            raise PatreonItemError("error: malformed next link")
        if not page.next_link:
            break
        cursor = page.next_link
    else:
        warnings.append(f"{creator.slug}: stopped after {MAX_PAGES} pages")
    return posts, warnings, info


def _local_day(post: PostRecord, window: Window) -> str:
    if post.published is None:
        return "unknown"
    return post.published.astimezone(window.zone).strftime("%Y-%m-%d")


def _published_text(post: PostRecord) -> str | None:
    if post.published is None:
        return None
    return post.published.isoformat()


def _hosts(post: PostRecord) -> list[str]:
    hosts: list[str] = []
    seen: set[str] = set()
    for link in post.outside_links:
        if link.host not in seen:
            seen.add(link.host)
            hosts.append(link.host)
    return hosts


def _counts(labels: list[str]) -> dict[str, int]:
    return {
        "accessible": labels.count("accessible"),
        "locked": labels.count("locked"),
        "excluded": sum(1 for label in labels if label.startswith("excluded")),
        "deferred": sum(1 for label in labels if label.startswith("deferred")),
        "listed": len(labels),
    }


def _summary_line(slug: str, counts: dict[str, int]) -> str:
    listed = counts["listed"]
    if listed == 0:
        return f"{slug}: 0 posts in window"
    if counts["accessible"] == 0 and counts["locked"] == listed:
        return (
            f"{slug}: 0 accessible posts in window "
            f"({listed} listed, {counts['locked']} locked for your tier)"
        )
    return (
        f"{slug}: {counts['accessible']} accessible, {counts['locked']} locked, "
        f"{counts['excluded']} excluded, {counts['deferred']} deferred ({listed} listed)"
    )


def _list_post(post: PostRecord, creator: Creator, window: Window) -> dict:
    links = [{"host": link.host, "url": link.url} for link in post.outside_links]
    return {
        "post_id": post.post_id,
        "post_title": post.title,
        "post_url": post.url,
        "published": _published_text(post),
        "access": access_label(post, creator),
        "attachments": len(post.attachments),
        "images": len(post.images),
        "outside_links": links,
    }


def _format_creator_text(block: dict, window: Window) -> list[str]:
    slug = block["slug"]
    if block.get("skipped"):
        return [f"{slug}: skipped (enabled: false)"]
    lines = [
        f"{slug} ({block['vanity']}) - posts {window.display_start}..{window.display_end} ({window.zone_name})"
    ]
    for post in block["posts"]:
        hosts = ", ".join(link["host"] for link in post["outside_links"]) or "none"
        day = "unknown"
        if post["published"]:
            try:
                day = datetime.fromisoformat(post["published"]).astimezone(window.zone).strftime("%Y-%m-%d")
            except ValueError:
                day = "unknown"
        lines.append(
            f"{day}  {post['access']}  {post['post_title']}  "
            f"{post['attachments']} attachments / {post['images']} images / outside links: {hosts}  "
            f"{post['post_url']}"
        )
    lines.append(block["summary"])
    if block.get("not_a_member"):
        lines.append(f"{slug}: not a member")
    for warning in block.get("warnings") or []:
        if warning.endswith("not a member"):
            continue
        lines.append(f"warning: {warning}")
    return lines


def run_list(
    config: CreatorsConfig,
    source,
    *,
    window: Window,
    only_slug: str | None,
    max_posts: int | None,
    fmt: str,
    results_path: Path | None,
) -> int:
    creators = _selected_creators(config, only_slug)
    source.whoami()
    blocks = []
    warnings: list[str] = list(config.warnings)
    held: PatreonError | None = None
    try:
        for creator in creators:
            if not creator.enabled:
                blocks.append(
                    {
                        "slug": creator.slug,
                        "vanity": creator.vanity,
                        "summary": f"{creator.slug}: skipped (enabled: false)",
                        "not_a_member": False,
                        "counts": _counts([]),
                        "posts": [],
                        "warnings": [],
                        "skipped": True,
                    }
                )
                continue
            posts, creator_warnings, info = collect_posts(source, creator, window, max_posts)
            warnings.extend(creator_warnings)
            rows = [_list_post(post, creator, window) for post in posts]
            counts = _counts([row["access"] for row in rows])
            blocks.append(
                {
                    "slug": creator.slug,
                    "vanity": creator.vanity,
                    "summary": _summary_line(creator.slug, counts),
                    "not_a_member": info.not_a_member,
                    "counts": counts,
                    "posts": rows,
                    "warnings": [item for item in creator_warnings if not item.endswith("not a member")],
                    "skipped": False,
                }
            )
    except PatreonError as exc:
        held = exc
    document = {
        "schema": PATREON_POST_LIST_SCHEMA_ID,
        "generated": now_local().isoformat(),
        "window": {
            "since": window.display_start if window.start is not None else None,
            "until": window.display_end,
            "timezone": window.zone_name,
        },
        "creators": [
            {
                "slug": block["slug"],
                "vanity": block["vanity"],
                "summary": block["summary"],
                "not_a_member": bool(block["not_a_member"]),
                "counts": block["counts"],
                "posts": block["posts"],
                "warnings": list(block["warnings"]),
            }
            for block in blocks
            if not block.get("skipped")
        ],
        "warnings": warnings,
        "gate": None
        if held is None or held.exit_code < 3
        else {"exit_code": held.exit_code, "line": str(held)},
    }
    text_lines: list[str] = []
    for block in blocks:
        text_lines.extend(_format_creator_text(block, window))
        text_lines.append("")
    rendered = "\n".join(text_lines).rstrip() + ("\n" if text_lines else "")
    if fmt == "json":
        print(json.dumps(document, indent=2))
    elif rendered.strip():
        print(rendered, end="" if rendered.endswith("\n") else "\n")
    if results_path is not None:
        _write_new(results_path, json.dumps(document, indent=2) + "\n")
    if held is not None:
        raise held
    return 0


def _file_row(
    *,
    name: str,
    staging_path: str | None,
    size: int | None,
    sha256: str | None,
    kind: str,
    status: str,
    url: str | None,
    host: str | None,
    downloaded_at: str | None = None,
) -> dict:
    if kind not in {"zip", "loose-media", "other"}:
        kind = "other"
    if status not in _FILE_STATUSES:
        status = "failed"
    if size is not None and not isinstance(size, int):
        size = int(size)
    return {
        "name": name,
        "staging_path": staging_path,
        "size": size,
        "sha256": sha256,
        "kind": kind,
        "downloaded_at": downloaded_at,
        "status": status,
        "url": url,
        "host": host,
    }


def _link_name(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1] or "link"
    return sanitize_component(tail, fallback="link", limit=60)


def _outside_rows(post: PostRecord) -> list[dict]:
    rows = []
    for link in post.outside_links:
        rows.append(
            _file_row(
                name=_link_name(link.url),
                staging_path=None,
                size=None,
                sha256=None,
                kind="other",
                status="deferred",
                url=link.url,
                host=link.host,
            )
        )
    return rows


def _post_row(post: PostRecord, status: str, reason: str, files: list[dict]) -> dict:
    if files:
        statuses = [item["status"] for item in files]
        if "failed" in statuses:
            status = "failed"
        elif "downloaded" in statuses:
            status = "downloaded"
        elif "planned" in statuses:
            status = "planned"
        elif statuses and all(item == "skipped-duplicate" for item in statuses):
            status = "skipped-duplicate"
        elif "deferred" in statuses and status not in {"locked", "excluded"}:
            status = "deferred"
    return {
        "post_id": post.post_id,
        "post_title": post.title,
        "post_url": post.url,
        "published": _published_text(post),
        "status": status,
        "reason": reason,
        "files": files,
    }


def _discard_part(part: Path, warnings: list[str]) -> None:
    if not part.is_file():
        return
    try:
        from lam.actions import recycle_path

        recycle_path(part)
    except Exception:
        warnings.append(f"left in place: {part.name}")


def _dest_path(directory: Path, filename: str, digest: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    ext = Path(filename).suffix
    suffixed = directory / f"{stem}__{digest[:8]}{ext}"
    if not suffixed.exists():
        return suffixed
    return directory / f"{stem}__{digest[:16]}{ext}"


def _folder_for(post: PostRecord) -> str:
    safe_id = sanitize_component(post.post_id, fallback="post", limit=40)
    safe_title = sanitize_component(post.title, fallback=safe_id, limit=80)
    name = f"{safe_id}_{safe_title}"
    if len(name) > 90:
        name = name[:90].rstrip(" .") or safe_id
    return name


def _stage_media(
    source,
    media: MediaItem,
    creator: Creator,
    directory: Path,
    index: dict,
    *,
    apply: bool,
    warnings: list[str],
) -> dict:
    filename = sanitize_component(media.name, fallback="file", limit=80)
    kind = media.kind if media.kind in {"zip", "loose-media", "other"} else "other"
    if _too_big(media, creator):
        return _file_row(
            name=filename,
            staging_path=None,
            size=media.size if isinstance(media.size, int) else None,
            sha256=None,
            kind=kind,
            status="deferred",
            url=None,
            host=None,
        )
    known = index["media"].get(media.media_id)
    if isinstance(known, dict):
        path = known.get("staging_path") or None
        sha = known.get("sha256") or None
        size = known.get("size")
        return _file_row(
            name=str(known.get("name") or filename),
            staging_path=path if path else None,
            size=size if isinstance(size, int) else None,
            sha256=sha if sha else None,
            kind=kind,
            status="skipped-duplicate",
            url=None,
            host=None,
        )
    if not apply:
        return _file_row(
            name=filename,
            staging_path=str(directory / filename),
            size=media.size if isinstance(media.size, int) else None,
            sha256=None,
            kind=kind,
            status="planned",
            url=media.url or None,
            host=None,
        )
    directory.mkdir(parents=True, exist_ok=True)
    part = directory / f".lam-tmp-{uuid.uuid4().hex[:8]}-{filename}.part"
    try:
        digest = source.download(media, part)
    except OSError:
        _discard_part(part, warnings)
        index["media"][media.media_id] = {
            "sha256": "",
            "staging_path": "",
            "name": filename,
            "size": 0,
        }
        return _file_row(
            name=filename,
            staging_path=None,
            size=None,
            sha256=None,
            kind=kind,
            status="failed",
            url=None,
            host=None,
        )
    size = int(part.stat().st_size) if part.is_file() else 0
    prior = index["sha256"].get(digest)
    if isinstance(prior, dict):
        _discard_part(part, warnings)
        index["media"][media.media_id] = {
            "sha256": digest,
            "staging_path": str(prior.get("staging_path") or ""),
            "name": filename,
            "size": size,
        }
        return _file_row(
            name=filename,
            staging_path=str(prior.get("staging_path") or "") or None,
            size=size,
            sha256=digest,
            kind=kind,
            status="skipped-duplicate",
            url=None,
            host=None,
        )
    final = _dest_path(directory, filename, digest)
    try:
        from lam.actions import _rename_no_overwrite

        _rename_no_overwrite(part, final)
    except Exception:
        _discard_part(part, warnings)
        warnings.append(f"left in place: {part.name}")
        return _file_row(
            name=filename,
            staging_path=None,
            size=size,
            sha256=digest,
            kind=kind,
            status="failed",
            url=None,
            host=None,
        )
    staged = str(final)
    index["media"][media.media_id] = {
        "sha256": digest,
        "staging_path": staged,
        "name": final.name,
        "size": size,
    }
    index["sha256"][digest] = {"staging_path": staged, "media_id": media.media_id}
    return _file_row(
        name=final.name,
        staging_path=staged,
        size=size,
        sha256=digest,
        kind=kind,
        status="downloaded",
        url=None,
        host=None,
        downloaded_at=now_local().isoformat(),
    )


def _sync_post(
    source,
    post: PostRecord,
    creator: Creator,
    inbox: Path,
    index: dict,
    *,
    apply: bool,
    warnings: list[str],
) -> dict:
    label = access_label(post, creator)
    if label.startswith("excluded"):
        reason = "excluded (chronos)" if "chronos" in label else "excluded by title filter"
        return _post_row(post, "excluded", reason, [])
    if label == "locked":
        return _post_row(post, "locked", "locked for your tier", [])
    media = _selected_media(post, creator)
    directory = inbox / _folder_for(post)
    files: list[dict] = []
    if not media:
        files.extend(_outside_rows(post))
        if files:
            hosts = ", ".join(_hosts(post))
            return _post_row(post, "deferred", f"outside link: {hosts}", files)
        return _post_row(post, "deferred", "no downloadable file", [])
    for item in media:
        files.append(
            _stage_media(
                source,
                item,
                creator,
                directory,
                index,
                apply=apply,
                warnings=warnings,
            )
        )
    files.extend(_outside_rows(post))
    reason = ""
    if any(item["status"] == "failed" for item in files):
        reason = "download failed"
    elif any(item["status"] == "deferred" for item in files):
        reason = "outside link: " + ", ".join(_hosts(post))
    elif all(item["status"] == "skipped-duplicate" for item in files):
        reason = "already staged"
    return _post_row(post, "planned", reason, files)


def _ready(slug: str, posts: list[dict], inbox: Path, mode: str) -> str:
    rows = [
        item
        for post in posts
        for item in post["files"]
        if item["status"] in {"downloaded", "planned"}
    ]
    zips = sum(1 for item in rows if item["kind"] == "zip")
    images = sum(1 for item in rows if item["kind"] == "loose-media")
    other = len(rows) - zips - images
    total = sum(item["size"] or 0 for item in rows)
    mb = round(total / (1024 * 1024))
    kinds = [f"{zips} zip"]
    if images:
        kinds.append(f"{images} image")
    if other:
        kinds.append(f"{other} other")
    verb = "staged" if mode == "apply" else "would stage"
    return (
        f"{slug}: {len(posts)} posts, {len(rows)} files ({', '.join(kinds)}), "
        f"{mb} MB {verb} in {inbox} - ready for notLib expand"
    )


def _manifest(
    *,
    slug: str,
    mode: str,
    inbox: Path,
    started: str,
    posts: list[dict],
    warnings: list[str],
    gate: dict | None,
) -> dict:
    return {
        "schema": PATREON_MANIFEST_SCHEMA_ID,
        "creator_slug": slug,
        "run_started": started,
        "run_finished": now_local().isoformat(),
        "mode": mode,
        "staging_path": str(inbox),
        "warnings": list(warnings),
        "ready_summary": _ready(slug, posts, inbox, mode),
        "posts": posts,
        "gate": gate,
    }


def _csv_text(posts: list[dict]) -> str:
    lines = ["post_id,post_title,post_url,status,file_name,file_status,staging_path,sha256,size,host"]
    for post in posts:
        files = post["files"] or [
            {
                "name": "",
                "status": "",
                "staging_path": "",
                "sha256": "",
                "size": "",
                "host": "",
            }
        ]
        for item in files:
            values = [
                post["post_id"],
                post["post_title"].replace('"', "'"),
                post["post_url"],
                post["status"],
                item.get("name") or "",
                item.get("status") or "",
                item.get("staging_path") or "",
                item.get("sha256") or "",
                "" if item.get("size") is None else str(item.get("size")),
                item.get("host") or "",
            ]
            lines.append(",".join(f'"{value}"' for value in values))
    return "\n".join(lines) + "\n"


def run_sync(
    config: CreatorsConfig,
    source,
    *,
    window: Window,
    only_slug: str | None,
    max_posts: int | None,
    apply: bool,
    results_path: Path | None,
    csv: bool,
) -> int:
    creators = _selected_creators(config, only_slug)
    mode = "apply" if apply else "dry-run"
    started = now_local().isoformat()
    stamp = now_local().strftime("%Y%m%d_%H%M%S")
    day = now_local().strftime("%Y%m%d")
    warnings: list[str] = list(config.warnings)
    all_posts: list[dict] = []
    primary_slug = creators[0].slug if creators else "patreon"
    primary_inbox = Path(config.staging_root) / primary_slug / f"_inbox_{day}"
    held: PatreonError | None = None
    try:
        source.whoami()
        for creator in creators:
            if not creator.enabled:
                print(f"{creator.slug}: skipped (enabled: false)")
                continue
            inbox = Path(config.staging_root) / creator.staging_folder / f"_inbox_{day}"
            if creator.slug == primary_slug:
                primary_inbox = inbox
            index_path = Path(config.staging_root) / creator.staging_folder / "_index" / "patreon-index.json"
            index = _load_index(index_path, creator.slug) if apply else _blank_index(creator.slug)
            posts, creator_warnings, info = collect_posts(source, creator, window, max_posts)
            warnings.extend(creator_warnings)
            rows = [
                _sync_post(
                    source,
                    post,
                    creator,
                    inbox,
                    index,
                    apply=apply,
                    warnings=warnings,
                )
                for post in posts
            ]
            for post, row in zip(posts, rows):
                index["posts"][post.post_id] = {
                    "status": row["status"],
                    "title": post.title,
                    "url": post.url,
                }
                print(f"{creator.slug}: {post.title} {row['status']}")
            all_posts.extend(rows)
            if apply:
                index["campaign_id"] = info.campaign_id
                index["updated"] = now_local().isoformat()
                index["creator_slug"] = creator.slug
                _save_index(index_path, index)
                creator_doc = _manifest(
                    slug=creator.slug,
                    mode=mode,
                    inbox=inbox,
                    started=started,
                    posts=rows,
                    warnings=creator_warnings,
                    gate=None,
                )
                _write_new(
                    _fresh_manifest(inbox, stamp, ".json"),
                    json.dumps(creator_doc, indent=2) + "\n",
                )
                if csv:
                    _write_new(_fresh_manifest(inbox, stamp, ".csv"), _csv_text(rows))
            if creator_warnings:
                for warning in creator_warnings:
                    if warning.endswith("not a member"):
                        print(warning)
    except PatreonError as exc:
        held = exc
    gate = None
    if held is not None and held.exit_code >= 3:
        gate = {"exit_code": held.exit_code, "line": str(held)}
    document = _manifest(
        slug=primary_slug,
        mode=mode,
        inbox=primary_inbox,
        started=started,
        posts=all_posts,
        warnings=warnings,
        gate=gate,
    )
    if document["ready_summary"]:
        print(document["ready_summary"])
    if results_path is not None:
        _write_new(results_path, json.dumps(document, indent=2) + "\n")
        if csv and not apply:
            _write_new(Path(str(results_path) + ".csv"), _csv_text(all_posts))
    if held is not None:
        raise held
    if any(post["status"] == "failed" for post in all_posts):
        return 1
    return 0


def _selected_creators(config: CreatorsConfig, only_slug: str | None) -> tuple[Creator, ...]:
    if not only_slug:
        return config.creators
    chosen = tuple(creator for creator in config.creators if creator.slug == only_slug)
    if not chosen:
        from lam.patreon.errors import PatreonUsageError, schema_gate

        raise PatreonUsageError(schema_gate(f"unknown creator slug {only_slug!r}"))
    return chosen
