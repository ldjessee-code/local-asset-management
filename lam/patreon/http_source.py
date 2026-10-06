# SPDX-License-Identifier: AGPL-3.0-or-later
"""Patreon sources: logged-in web JSON, or a recorded fixture directory.

No cookie value is placed in logs, exceptions, or ``repr()``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from lam.patreon.errors import (
    GATE_AUTH,
    GATE_CHALLENGE,
    GATE_NETWORK,
    GATE_RATE,
    GATE_VERIFICATION,
    PatreonAuthError,
    PatreonChallengeError,
    PatreonItemError,
    PatreonNetworkError,
    PatreonRateLimitError,
    PatreonVerificationError,
)
from lam.patreon.parse import FILE_RELATIONSHIPS, MediaItem, ParsedPage, PostRecord, parse_posts_page
from lam.patreon.safe_download import DownloadResult, DownloadSizeError, write_atomic
from lam.token.http_signals import (
    classify_response,
    content_type,
    is_html_content,
    is_json_content,
    safe_url_for_log,
    snippet_for_log,
)
from lam.token.secret import Secret

log = logging.getLogger("lam.patreon.http")

API_ROOT = "https://www.patreon.com/api"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
MAX_ATTEMPTS = 3
# File bodies retry 429, 5xx, and timeouts. API calls stay on MAX_ATTEMPTS.
DOWNLOAD_ATTEMPTS = 4
# Download Retry-After is not cut down to the API retry_cap. 120s is the ceiling.
DOWNLOAD_RETRY_CAP = 120.0
MEMBER = "member"
NOT_MEMBER = "not_a_member"
UNKNOWN = "unknown"
CAMPAIGN_INCLUDE = "current_user_pledge"
USER_INCLUDE = "memberships.campaign,pledges.campaign"
POST_INCLUDE = ",".join((*FILE_RELATIONSHIPS, "images", "media"))
_NETWORK_TYPES = {
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "TimeoutException",
    "NetworkError",
    "TimeoutError",
    "RemoteProtocolError",
}


class RequestClock:
    """Records delays. Does not call ``time.sleep`` (tests inject this)."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        if seconds and seconds > 0:
            self.sleeps.append(float(seconds))


class WallClock(RequestClock):
    """Production clock: record the delay, then sleep."""

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        if seconds and seconds > 0:
            import time

            time.sleep(seconds)


def _client_factory():
    import httpx

    # Follow redirects, same as ``lam.token.check``, so a hop to a login /
    # 2FA page is visible as the final response URL.
    return httpx.Client(follow_redirects=True, timeout=30.0)


@dataclass
class CampaignInfo:
    campaign_id: str | None
    vanity: str
    name: str
    is_member: bool = False
    # member | not_a_member | unknown. Unknown must not be reported as fact.
    membership: str = ""

    def __post_init__(self) -> None:
        if self.membership not in {MEMBER, NOT_MEMBER, UNKNOWN}:
            self.membership = MEMBER if self.is_member else NOT_MEMBER
        else:
            self.is_member = self.membership == MEMBER

    @property
    def not_a_member(self) -> bool:
        return self.membership == NOT_MEMBER


def _is_network(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    return type(exc).__name__ in _NETWORK_TYPES


def _log_gate(kind: str, reason: str, status: int, url: str, text: str) -> None:
    log.debug(
        "patreon gate %s (%s) status=%s url=%s body[:200]=%s",
        kind,
        reason,
        status,
        safe_url_for_log(url),
        snippet_for_log(text, 200),
    )


class _DownloadRetry(Exception):
    """One file-download attempt failed in a way that can be tried again."""

    def __init__(self, status: int, headers) -> None:
        super().__init__("retry")
        self.status = int(status)
        self.headers = headers


def _optional_size(value) -> int | None:
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _body_text(body: bytes) -> str:
    try:
        return body.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _header_retry_after(headers) -> float | None:
    if headers is None:
        return None
    try:
        raw = str(headers.get("Retry-After") or headers.get("retry-after") or "").strip()
    except Exception:
        return None
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 5.0


def _retry_after(headers) -> float:
    raw = ""
    if headers is not None:
        try:
            raw = str(headers.get("Retry-After") or headers.get("retry-after") or "")
        except Exception:
            raw = ""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 5.0


def _pledge_signal(data: dict) -> str:
    """``present``, ``explicit_none``, or ``missing`` for current_user_pledge."""
    rels = data.get("relationships")
    if not isinstance(rels, dict) or "current_user_pledge" not in rels:
        return "missing"
    rel = rels.get("current_user_pledge")
    if not isinstance(rel, dict) or "data" not in rel:
        return "missing"
    pledge = rel.get("data")
    rows = pledge if isinstance(pledge, list) else ([pledge] if isinstance(pledge, dict) else [])
    if any(isinstance(item, dict) and item.get("id") for item in rows):
        return "present"
    return "explicit_none"


def _campaign_ids_of(resource: dict) -> list[str]:
    ids: list[str] = []
    rels = resource.get("relationships") if isinstance(resource.get("relationships"), dict) else {}
    rel = rels.get("campaign") if isinstance(rels, dict) else None
    data = rel.get("data") if isinstance(rel, dict) else None
    rows = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
    for item in rows:
        if isinstance(item, dict) and item.get("id") is not None:
            ids.append(str(item["id"]))
    attrs = resource.get("attributes") if isinstance(resource.get("attributes"), dict) else {}
    if isinstance(attrs, dict) and attrs.get("campaign_id") is not None:
        ids.append(str(attrs["campaign_id"]))
    return ids


def _active_patron(attrs: object) -> bool:
    if not isinstance(attrs, dict):
        return False
    cents = attrs.get("currently_entitled_amount_cents")
    if isinstance(cents, str) and cents.strip().isdigit():
        cents = int(cents.strip())
    if isinstance(cents, int) and not isinstance(cents, bool) and cents > 0:
        return True
    amount = attrs.get("amount_cents")
    if isinstance(amount, int) and not isinstance(amount, bool) and amount > 0:
        return True
    return str(attrs.get("patron_status") or "") == "active_patron"


def _included_active_for_campaign(payload: dict, campaign_id: str | None) -> bool:
    if not campaign_id or not isinstance(payload, dict):
        return False
    for item in payload.get("included") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") not in {"pledge", "member"}:
            continue
        if campaign_id in _campaign_ids_of(item) and _active_patron(item.get("attributes") or {}):
            return True
    return False


def _campaign_from_payload(payload: dict, vanity: str, campaign_id: str | None) -> CampaignInfo:
    """Read campaign membership. A false free-member flag is not "not a member".

    Paid patrons are not free members. Membership is positive when a pledge
    relationship or included pledge/member is present, or when
    ``current_user_is_free_member`` is true. Anything else stays unknown
    until ``membership_from_current_user`` or the post fallback runs.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return CampaignInfo(campaign_id=campaign_id, vanity=vanity, name=vanity, membership=UNKNOWN)
    attrs = data.get("attributes") or {}
    found = data.get("id")
    cid = str(found) if found is not None else (campaign_id or None)
    free = attrs.get("current_user_is_free_member") is True
    pledge = _pledge_signal(data)
    if pledge == "present" or free or _included_active_for_campaign(payload, cid):
        membership = MEMBER
    else:
        membership = UNKNOWN
    return CampaignInfo(
        campaign_id=cid or None,
        vanity=str(attrs.get("vanity") or vanity),
        name=str(attrs.get("name") or vanity),
        membership=membership,
    )


def membership_from_current_user(payload: dict, campaign_id: str | None) -> str:
    """Membership from ``/api/current_user?include=memberships.campaign``.

    ``active_patron`` or ``currently_entitled_amount_cents > 0`` on a
    member/pledge for this campaign means member. ``former_patron`` (and any
    other non-active status) for this campaign, or an active membership of a
    different campaign only, means not a member. A missing relationship stays
    unknown.
    """
    if not isinstance(payload, dict) or not campaign_id:
        return UNKNOWN
    data = payload.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return UNKNOWN
    rels = data.get("relationships") if isinstance(data.get("relationships"), dict) else {}
    included: dict[tuple[str, str], dict] = {}
    for item in payload.get("included") or []:
        if isinstance(item, dict) and item.get("id") is not None:
            included[(str(item.get("type") or ""), str(item["id"]))] = item
    if not isinstance(rels, dict):
        return UNKNOWN
    saw_relationship = False
    active_this = False
    inactive_this = False
    other = False
    for rel_name in ("memberships", "pledges"):
        rel = rels.get(rel_name)
        if not isinstance(rel, dict) or "data" not in rel:
            continue
        saw_relationship = True
        rows = rel.get("data")
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            continue
        for ref in rows:
            if not isinstance(ref, dict) or not ref.get("id"):
                continue
            resource = included.get((str(ref.get("type") or ""), str(ref["id"])))
            if not isinstance(resource, dict):
                continue
            ids = _campaign_ids_of(resource)
            if campaign_id in ids:
                if _active_patron(resource.get("attributes") or {}):
                    active_this = True
                else:
                    inactive_this = True
            elif ids:
                other = True
    if active_this:
        return MEMBER
    if inactive_this or other:
        return NOT_MEMBER
    if saw_relationship:
        return UNKNOWN
    return UNKNOWN


def apply_post_membership_fallback(info: CampaignInfo, posts) -> CampaignInfo:
    """Fallback only, and only while membership is still unknown.

    Campaign pledge, free membership, and current_user patron status are
    decided first. ``current_user_is_free_member: false`` never means the
    viewer is not a member: paid patrons are not free members.

    If at least one collected post is viewable and tier-gated (not public),
    the viewer is a member. A viewable post whose gating is unknown leaves
    membership unknown so the list does not claim "not a member". No pledge,
    no membership, and no viewable tier-gated post stays a genuine non-member.
    """
    if info.membership != UNKNOWN:
        return info
    indeterminate = False
    for post in posts:
        if not getattr(post, "can_view", False):
            continue
        gated = getattr(post, "tier_gated", None)
        if gated is True:
            info.membership = MEMBER
            info.is_member = True
            return info
        if gated is None:
            indeterminate = True
    if indeterminate:
        return info
    info.membership = NOT_MEMBER
    info.is_member = False
    return info


class HttpPatreonSource:
    """Logged-in ``www.patreon.com/api`` client. Cookie stays inside ``Secret``."""

    def __init__(
        self,
        cookie: Secret | str,
        *,
        transport=None,
        clock: RequestClock | None = None,
        min_delay: float = 0.4,
        retry_cap: float = 5.0,
    ):
        self._cookie = cookie if isinstance(cookie, Secret) else Secret(str(cookie))
        self._transport = transport
        self._clock = clock or WallClock()
        self._min_delay = float(min_delay)
        self._retry_cap = float(retry_cap)
        self._client = None
        self._calls = 0

    def __repr__(self) -> str:
        return "HttpPatreonSource(cookie=Secret(<redacted>))"

    def _http(self):
        if self._client is None:
            import logging

            logging.getLogger("httpx").setLevel(logging.CRITICAL)
            logging.getLogger("httpcore").setLevel(logging.CRITICAL)
            if self._transport is not None:
                import httpx

                self._client = httpx.Client(
                    transport=self._transport, follow_redirects=True, timeout=30.0
                )
            else:
                self._client = _client_factory()
        return self._client

    def _pause(self, extra: float | None = None) -> None:
        if extra is not None:
            delay = min(max(float(extra), 0.0), self._retry_cap)
            if delay > 0:
                self._clock.sleep(delay)
            return
        if self._calls > 0 and self._min_delay > 0:
            self._clock.sleep(self._min_delay)

    def _request(self, method: str, url: str, *, params: dict | None = None):
        headers = {
            "Cookie": self._cookie.reveal(),
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html;q=0.8",
        }
        for attempt in range(MAX_ATTEMPTS):
            if attempt == 0:
                self._pause()
            self._calls += 1
            try:
                response = self._http().request(method, url, params=params, headers=headers)
            except Exception as exc:
                if _is_network(exc) and attempt + 1 < MAX_ATTEMPTS:
                    self._pause()
                    continue
                raise PatreonNetworkError(GATE_NETWORK) from None
            text = ""
            try:
                text = response.text or ""
            except Exception:
                text = ""
            resp_headers = getattr(response, "headers", None)
            status = int(getattr(response, "status_code", 0) or 0)
            resp_url = str(getattr(response, "url", "") or "")
            classified = classify_response(
                status=status, text=text, headers=resp_headers, url=resp_url
            )
            if classified is not None:
                kind, reason = classified
                _log_gate(kind, reason, status, resp_url, text)
                if kind == "challenge":
                    raise PatreonChallengeError(GATE_CHALLENGE) from None
                if kind == "verification":
                    raise PatreonVerificationError(GATE_VERIFICATION) from None
                if kind == "auth":
                    raise PatreonAuthError(GATE_AUTH) from None
                if kind == "rate":
                    if attempt + 1 < MAX_ATTEMPTS:
                        self._pause(_retry_after(resp_headers))
                        continue
                    raise PatreonRateLimitError(GATE_RATE) from None
            if status >= 400:
                raise PatreonNetworkError(GATE_NETWORK) from None
            return response
        raise PatreonNetworkError(GATE_NETWORK) from None

    def _json(self, response) -> dict:
        try:
            payload = response.json()
        except Exception:
            raise PatreonItemError("error: malformed JSON response") from None
        if not isinstance(payload, dict):
            raise PatreonItemError("error: malformed JSON response")
        return payload

    def whoami(self) -> dict:
        response = self._request("GET", f"{API_ROOT}/current_user")
        payload = self._json(response)
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            raise PatreonAuthError(GATE_AUTH) from None
        attrs = data.get("attributes") or {}
        return {"id": str(data.get("id")), "name": str(attrs.get("full_name") or "")}

    def resolve_campaign(self, vanity: str, campaign_id: str | None = None) -> CampaignInfo:
        if campaign_id:
            response = self._request(
                "GET",
                f"{API_ROOT}/campaigns/{campaign_id}",
                params={"include": CAMPAIGN_INCLUDE},
            )
        else:
            response = self._request(
                "GET",
                f"{API_ROOT}/campaigns",
                params={
                    "filter[vanity]": vanity,
                    "page[count]": "1",
                    "include": CAMPAIGN_INCLUDE,
                },
            )
        info = _campaign_from_payload(self._json(response), vanity, campaign_id)
        if info.membership != UNKNOWN:
            return info
        response = self._request(
            "GET",
            f"{API_ROOT}/current_user",
            params={"include": USER_INCLUDE},
        )
        status = membership_from_current_user(self._json(response), info.campaign_id)
        if status != UNKNOWN:
            info.membership = status
            info.is_member = status == MEMBER
        return info

    def list_posts(self, vanity: str, campaign_id: str | None, *, cursor: str | None = None) -> ParsedPage:
        del vanity
        if cursor:
            response = self._request("GET", cursor)
        else:
            response = self._request(
                "GET",
                f"{API_ROOT}/posts",
                params={
                    "filter[campaign_id]": campaign_id or "",
                    "filter[is_draft]": "false",
                    "sort": "-published_at",
                    "include": POST_INCLUDE,
                },
            )
        return parse_posts_page(self._json(response))

    def fetch_post(self, post_id: str) -> PostRecord | None:
        """One post from ``/api/posts/<id>``. ``data`` may be an object."""
        response = self._request(
            "GET",
            f"{API_ROOT}/posts/{post_id}",
            params={"include": POST_INCLUDE},
        )
        page = parse_posts_page(self._json(response))
        return page.posts[0] if page.posts else None

    def download(self, media: MediaItem, dest: Path) -> str:
        """Save ``media`` to ``dest`` and return the sha256 hex digest.

        Delegates to ``download_atomic``. A leftover temp is overwritten, so
        this no longer raises ``FileExistsError`` for a previous attempt.
        """
        return self.download_atomic(media, dest).sha256

    def download_atomic(
        self,
        media: MediaItem,
        dest: Path,
        *,
        expected_size: int | None = None,
    ) -> DownloadResult:
        """Stream ``media`` to ``<dest>.partial`` and rename it onto ``dest``.

        A non-empty ``dest`` whose size matches ``expected_size`` (or any
        non-empty file when ``expected_size`` is omitted) is returned without
        a request. 429, 5xx, and timeouts retry up to four times. Auth,
        challenge, and verification errors are raised on the first response.
        """
        if not str(media.url or "").startswith(("http://", "https://")):
            raise OSError("download failed")
        dest = Path(dest)
        expected = _optional_size(expected_size)
        if dest.is_file():
            size = int(dest.stat().st_size)
            if size > 0 and (expected is None or size == expected):
                return DownloadResult(
                    path=dest,
                    sha256=_sha256_file(dest),
                    size=size,
                    skipped_existing=True,
                    resumed=False,
                )
        last_status = 0
        last_headers = None
        for attempt in range(DOWNLOAD_ATTEMPTS):
            if attempt == 0:
                self._pause()
            else:
                delay = self._download_backoff(attempt, last_status, last_headers)
                if delay > 0:
                    self._clock.sleep(delay)
            self._calls += 1
            try:
                return self._stream_download(str(media.url), dest, expected)
            except _DownloadRetry as retry:
                last_status = retry.status
                last_headers = retry.headers
                if attempt + 1 >= DOWNLOAD_ATTEMPTS:
                    break
            except (PatreonAuthError, PatreonChallengeError, PatreonVerificationError):
                raise
        if last_status == 429:
            raise PatreonRateLimitError(GATE_RATE) from None
        raise PatreonNetworkError(GATE_NETWORK) from None

    def _download_backoff(self, attempt: int, status: int, headers) -> float:
        """Seconds to wait before try number ``attempt`` (0-based).

        A ``Retry-After`` header wins. It is not limited by ``retry_cap``
        (that cap is for API calls). The ceiling is ``DOWNLOAD_RETRY_CAP``.
        """
        parsed = _header_retry_after(headers)
        if parsed is not None:
            delay = parsed
        elif status == 429:
            delay = 5.0
        else:
            delay = float(2 ** max(attempt - 1, 0))
        if delay < self._min_delay:
            delay = self._min_delay
        if delay > DOWNLOAD_RETRY_CAP:
            delay = DOWNLOAD_RETRY_CAP
        return delay

    def _stream_download(self, url: str, dest: Path, expected: int | None) -> DownloadResult:
        headers = {
            "Cookie": self._cookie.reveal(),
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html;q=0.8",
        }
        try:
            with self._http().stream("GET", url, headers=headers) as response:
                return self._consume_download(response, dest, expected)
        except _DownloadRetry:
            raise
        except (PatreonAuthError, PatreonChallengeError, PatreonVerificationError, DownloadSizeError):
            raise
        except Exception as exc:
            # DownloadSizeError is an OSError, so it must be re-raised above.
            # A dropped connection or timeout is safe to try again.
            if _is_network(exc):
                raise _DownloadRetry(0, None) from None
            raise PatreonNetworkError(GATE_NETWORK) from None

    def _consume_download(self, response, dest: Path, expected: int | None) -> DownloadResult:
        status = int(getattr(response, "status_code", 0) or 0)
        resp_headers = getattr(response, "headers", None)
        resp_url = str(getattr(response, "url", "") or "")
        if not (200 <= status < 300):
            text = _body_text(response.read())
            self._raise_or_retry_download(status, text, resp_headers, resp_url)
        ctype = content_type(resp_headers)
        if is_html_content(ctype, "") or is_json_content(ctype):
            body = response.read()
            text = _body_text(body)
            self._raise_immediate_gate(status, text, resp_headers, resp_url)
            return write_atomic(dest, [body], expected_size=expected)
        self._raise_immediate_gate(status, "", resp_headers, resp_url)
        return write_atomic(dest, _iter_response_bytes(response), expected_size=expected)

    def _raise_or_retry_download(self, status: int, text: str, headers, url: str) -> None:
        classified = classify_response(status=status, text=text, headers=headers, url=url)
        if classified is not None:
            kind, reason = classified
            _log_gate(kind, reason, status, url, text)
            if kind == "challenge":
                raise PatreonChallengeError(GATE_CHALLENGE) from None
            if kind == "verification":
                raise PatreonVerificationError(GATE_VERIFICATION) from None
            if kind == "auth":
                raise PatreonAuthError(GATE_AUTH) from None
            if kind == "rate":
                raise _DownloadRetry(status, headers) from None
        if status == 429 or status >= 500:
            raise _DownloadRetry(status, headers) from None
        raise PatreonNetworkError(GATE_NETWORK) from None

    def _raise_immediate_gate(self, status: int, text: str, headers, url: str) -> None:
        classified = classify_response(status=status, text=text, headers=headers, url=url)
        if classified is None:
            return
        kind, reason = classified
        _log_gate(kind, reason, status, url, text)
        if kind == "challenge":
            raise PatreonChallengeError(GATE_CHALLENGE) from None
        if kind == "verification":
            raise PatreonVerificationError(GATE_VERIFICATION) from None
        if kind == "auth":
            raise PatreonAuthError(GATE_AUTH) from None
        if kind == "rate":
            raise _DownloadRetry(status, headers) from None


def _iter_response_bytes(response):
    try:
        iterator = response.iter_bytes()
    except Exception:
        iterator = None
    if iterator is None:
        body = getattr(response, "content", b"") or b""
        if body:
            yield body
        return
    yield from iterator


class FixtureSource:
    """Read sanitized JSON fixtures. No socket and no cookie."""

    pages_read: list[str] = []
    downloaded: list[str] = []

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._index: dict[str, int] = {}

    def __repr__(self) -> str:
        return f"FixtureSource(root={self.root.name!r})"

    def whoami(self) -> dict:
        path = self.root / "whoami.json"
        if not path.is_file():
            return {"id": "fixture", "name": "Fixture"}
        payload = json.loads(path.read_text(encoding="utf-8"))
        self._raise_gate(payload)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return {"id": "fixture", "name": "Fixture"}
        attrs = data.get("attributes") or {}
        return {"id": str(data.get("id") or "fixture"), "name": str(attrs.get("full_name") or "Fixture")}

    def resolve_campaign(self, vanity: str, campaign_id: str | None = None) -> CampaignInfo:
        path = self.root / f"campaign-{vanity}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self._raise_gate(payload)
        return _campaign_from_payload(payload, vanity, campaign_id)

    def list_posts(self, vanity: str, campaign_id: str | None = None, *, cursor: str | None = None) -> ParsedPage:
        del campaign_id, cursor
        path = self.root / f"posts-{vanity}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self._raise_gate(payload)
        if isinstance(payload, dict) and "pages" in payload:
            pages = payload["pages"]
        else:
            pages = [payload]
        index = self._index.get(vanity, 0)
        if index >= len(pages):
            return ParsedPage(posts=[], next_link=None, next_malformed=False)
        FixtureSource.pages_read.append(f"{vanity}:{index}")
        self._index[vanity] = index + 1
        page = pages[index]
        if not isinstance(page, dict):
            raise PatreonItemError("error: malformed next link")
        return parse_posts_page(page)

    def fetch_post(self, post_id: str) -> PostRecord | None:
        path = self.root / f"post-{post_id}.json"
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        self._raise_gate(payload)
        page = parse_posts_page(payload if isinstance(payload, dict) else {})
        return page.posts[0] if page.posts else None

    def download(self, media: MediaItem, dest: Path) -> str:
        FixtureSource.downloaded.append(media.url)
        if str(media.media_id).startswith("fail"):
            raise OSError("download failed")
        data = (self.root / "files" / str(media.media_id)).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        with dest.open("xb") as handle:
            handle.write(data)
        return digest

    @staticmethod
    def _raise_gate(payload: object) -> None:
        if not isinstance(payload, dict) or "gate" not in payload:
            return
        kind = str(payload.get("gate") or "")
        if kind == "rate":
            raise PatreonRateLimitError(GATE_RATE)
        if kind == "auth":
            raise PatreonAuthError(GATE_AUTH)
        if kind == "challenge":
            raise PatreonChallengeError(GATE_CHALLENGE)
        if kind == "verification":
            raise PatreonVerificationError(GATE_VERIFICATION)
        if kind == "network":
            raise PatreonNetworkError(GATE_NETWORK)
        raise PatreonNetworkError(GATE_NETWORK)
