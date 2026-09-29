# SPDX-License-Identifier: AGPL-3.0-or-later
"""Patreon sources: logged-in web JSON, or a recorded fixture directory.

No cookie value is placed in logs, exceptions, or ``repr()``.
"""

from __future__ import annotations

import hashlib
import json
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
from lam.patreon.parse import MediaItem, ParsedPage, parse_posts_page
from lam.token.secret import Secret

API_ROOT = "https://www.patreon.com/api"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
MAX_ATTEMPTS = 3
_CHALLENGE_MARKERS = (
    "cf-challenge",
    "challenge-platform",
    "just a moment",
    "hcaptcha",
    "g-recaptcha",
    "cf-turnstile",
    "cdn-cgi/challenge",
    "captcha",
)
_TWO_FACTOR_MARKERS = (
    "two-factor",
    "two factor",
    "2fa",
    "verification code",
    "identity verification",
    "enter the code",
)
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

    return httpx.Client(timeout=30.0)


@dataclass
class CampaignInfo:
    campaign_id: str | None
    vanity: str
    name: str
    is_member: bool

    @property
    def not_a_member(self) -> bool:
        return not self.is_member


def _is_network(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    return type(exc).__name__ in _NETWORK_TYPES


def _challenge(text: str, headers) -> bool:
    blob = (text or "").lower()
    if any(marker in blob for marker in _CHALLENGE_MARKERS):
        return True
    server = ""
    if headers is not None:
        try:
            server = str(headers.get("server") or "").lower()
        except Exception:
            server = ""
    return "cloudflare" in server and ("captcha" in blob or "challenge" in blob)


def _two_factor(text: str) -> bool:
    blob = (text or "").lower()
    return any(marker in blob for marker in _TWO_FACTOR_MARKERS)


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


def _campaign_from_payload(payload: dict, vanity: str, campaign_id: str | None) -> CampaignInfo:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return CampaignInfo(campaign_id=campaign_id, vanity=vanity, name=vanity, is_member=False)
    attrs = data.get("attributes") or {}
    rel = (data.get("relationships") or {}).get("current_user_pledge") or {}
    pledge = rel.get("data") if isinstance(rel, dict) else None
    free = bool(attrs.get("current_user_is_free_member"))
    member = pledge is not None or free
    found = data.get("id")
    cid = str(found) if found is not None else (campaign_id or None)
    return CampaignInfo(
        campaign_id=cid or None,
        vanity=str(attrs.get("vanity") or vanity),
        name=str(attrs.get("name") or vanity),
        is_member=member,
    )


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

                self._client = httpx.Client(transport=self._transport, timeout=30.0)
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
            if _challenge(text, resp_headers):
                raise PatreonChallengeError(GATE_CHALLENGE) from None
            if _two_factor(text):
                raise PatreonVerificationError(GATE_VERIFICATION) from None
            status = int(getattr(response, "status_code", 0) or 0)
            if status in {401, 403}:
                raise PatreonAuthError(GATE_AUTH) from None
            if status == 429:
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
            response = self._request("GET", f"{API_ROOT}/campaigns/{campaign_id}")
        else:
            response = self._request(
                "GET",
                f"{API_ROOT}/campaigns",
                params={"filter[vanity]": vanity, "page[count]": "1"},
            )
        return _campaign_from_payload(self._json(response), vanity, campaign_id)

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
                    "include": "attachments,images,media",
                },
            )
        return parse_posts_page(self._json(response))

    def download(self, media: MediaItem, dest: Path) -> str:
        if not media.url.startswith(("http://", "https://")):
            raise OSError("download failed")
        response = self._request("GET", media.url)
        digest = hashlib.sha256()
        try:
            iterator = response.iter_bytes()
        except Exception:
            iterator = None
        with dest.open("xb") as handle:
            if iterator is None:
                body = getattr(response, "content", b"") or b""
                handle.write(body)
                digest.update(body)
            else:
                for chunk in iterator:
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
        return digest.hexdigest()


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
