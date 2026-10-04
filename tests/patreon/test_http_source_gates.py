# SPDX-License-Identifier: AGPL-3.0-or-later
"""HttpPatreonSource gates: structural signals only; 2xx JSON is never 2FA/challenge."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import httpx
import pytest

from lam.patreon.errors import (
    EXIT_AUTH,
    EXIT_CHALLENGE,
    EXIT_RATE,
    EXIT_VERIFICATION,
    GATE_AUTH,
    GATE_CHALLENGE,
    GATE_VERIFICATION,
    PatreonAuthError,
    PatreonChallengeError,
    PatreonRateLimitError,
    PatreonVerificationError,
)
from lam.patreon.http_source import HttpPatreonSource, RequestClock
from lam.patreon.parse import MediaItem
from lam.token.secret import Secret

WHOAMI = {
    "data": {
        "id": "user-1",
        "type": "user",
        "attributes": {
            "full_name": "Fixture Patron",
            "two_factor_enabled": True,
            "notes": "verification code captcha 2fa two_factor",
        },
    }
}
COOKIE = "session_id=fixture-session"


def _source(handler, clock: RequestClock | None = None) -> HttpPatreonSource:
    return HttpPatreonSource(
        Secret(COOKIE),
        transport=httpx.MockTransport(handler),
        clock=clock or RequestClock(),
        min_delay=0.0,
        retry_cap=5.0,
    )


def test_2xx_json_with_2fa_words_passes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=WHOAMI)

    assert _source(handler).whoami() == {"id": "user-1", "name": "Fixture Patron"}


def test_2xx_json_api_content_type_with_2fa_words_passes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/vnd.api+json"},
            json=WHOAMI,
        )

    assert _source(handler).whoami()["id"] == "user-1"


def test_2xx_json_captcha_word_is_not_challenge():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=WHOAMI)

    _source(handler).whoami()


def _login_html() -> httpx.Response:
    return httpx.Response(
        200,
        text="<html>Enter the verification code</html>",
        headers={"content-type": "text/html"},
    )


@pytest.mark.parametrize(
    "location",
    [
        "https://www.patreon.com/login",
        "https://www.patreon.com/login?ru=/home",
        "/login",
        "https://www.patreon.com/two-factor",
        "https://www.patreon.com/2fa",
    ],
)
def test_redirect_to_login_or_2fa_is_verification(location: str):
    def handler(request: httpx.Request) -> httpx.Response:
        # Client follows the hop. The verification page itself is 200 HTML.
        if not request.url.path.startswith("/api"):
            return _login_html()
        return httpx.Response(302, headers={"Location": location})

    with pytest.raises(PatreonVerificationError) as caught:
        _source(handler).whoami()
    assert caught.value.exit_code == EXIT_VERIFICATION
    assert str(caught.value) == GATE_VERIFICATION


def test_final_url_login_html_is_verification(caplog: pytest.LogCaptureFixture):
    """302 to /login, then 200 HTML there. Proves the followed final URL, not the 302 alone."""
    caplog.set_level(logging.DEBUG, logger="lam.patreon.http")
    login = "https://www.patreon.com/login"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.rstrip("/") == "/login":
            return _login_html()
        return httpx.Response(302, headers={"Location": login})

    with pytest.raises(PatreonVerificationError) as caught:
        _source(handler).whoami()
    assert caught.value.exit_code == EXIT_VERIFICATION
    text = caplog.text.lower()
    assert "verification" in text
    assert "status=200" in text
    assert "url=https://www.patreon.com/login" in text


def test_403_cloudflare_html_is_challenge():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<html>Just a moment cf-challenge</html>",
            headers={"content-type": "text/html"},
        )

    with pytest.raises(PatreonChallengeError) as caught:
        _source(handler).whoami()
    assert caught.value.exit_code == EXIT_CHALLENGE
    assert str(caught.value) == GATE_CHALLENGE


def test_cf_mitigated_header_is_challenge():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<html>blocked</html>",
            headers={"content-type": "text/html", "cf-mitigated": "challenge"},
        )

    with pytest.raises(PatreonChallengeError):
        _source(handler).whoami()


def test_401_with_2fa_words_is_auth():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "2fa verification code captcha"})

    with pytest.raises(PatreonAuthError) as caught:
        _source(handler).whoami()
    assert caught.value.exit_code == EXIT_AUTH
    assert str(caught.value) == GATE_AUTH


def test_429_with_2fa_words_retries_then_rate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "time.sleep", lambda seconds: (_ for _ in ()).throw(AssertionError("real sleep"))
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "30"},
            text="slow down 2fa verification code",
        )

    clock = RequestClock()
    with pytest.raises(PatreonRateLimitError) as caught:
        _source(handler, clock).whoami()
    assert caught.value.exit_code == EXIT_RATE
    assert calls["n"] == 3
    assert max(clock.sleeps) <= 5


def test_debug_log_names_signal_without_cookies(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG, logger="lam.patreon.http")

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.startswith("/api"):
            return _login_html()
        return httpx.Response(
            302,
            headers={"Location": "https://www.patreon.com/login", "Set-Cookie": "session_id=leaked"},
        )

    with pytest.raises(PatreonVerificationError):
        _source(handler).whoami()
    text = caplog.text
    assert "verification" in text.lower()
    assert "redirect" in text.lower() or "login" in text.lower()
    assert COOKIE not in text
    assert "session_id=leaked" not in text
    assert "Set-Cookie" not in text
    assert "Cookie:" not in text
    bodies = [rec.getMessage() for rec in caplog.records if "body" in rec.getMessage().lower()]
    for message in bodies:
        assert len(message) < 2000


# ZIP local-file header plus the words that used to false-trip 2FA, and a non-UTF-8 byte.
_BINARY_WITH_2FA = b"PK\x03\x04\x14\x00" + b"2fa two_factor verification code captcha" + b"\xff\xfe"


@pytest.mark.parametrize("content_type", ["application/zip", "application/octet-stream"])
def test_2xx_binary_download_with_2fa_bytes_is_not_verification(tmp_path: Path, content_type: str):
    """download() goes through _request. A 200 zip/octet-stream body is not a 2FA gate."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_BINARY_WITH_2FA,
            headers={"content-type": content_type},
        )

    media = MediaItem(
        media_id="legend-1",
        name="legend.zip",
        url="https://c10.patreonusercontent.com/4/patreon-media/p/post/1/legend.zip",
        kind="attachment",
        source_kind="attachment",
    )
    dest = tmp_path / "legend.zip"
    digest = _source(handler).download(media, dest)
    assert digest == hashlib.sha256(_BINARY_WITH_2FA).hexdigest()
    assert dest.read_bytes() == _BINARY_WITH_2FA
