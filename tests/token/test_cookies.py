# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cookie header building from a fake cookie jar."""

from __future__ import annotations

import time

import pytest

from lam.token.cookies import build_cookie_header
from lam.token.errors import EXIT_AUTH, TokenAuthError
from lam.token.profiles import builtin_profiles, load_profiles_document
from lam.schemas.registry import SITE_PROFILES_SCHEMA_ID


def _site(**overrides):
    data = {
        "schema": SITE_PROFILES_SCHEMA_ID,
        "sites": [
            {
                "id": "patreon",
                "login_url": "https://www.patreon.com/login",
                "cookie_domains": [".patreon.com"],
                "required_cookies": ["session_id"],
                "header_cookies": "all",
                **overrides,
            }
        ],
    }
    return load_profiles_document(data).get("patreon")


def _cookie(name: str, value: str, domain: str = ".patreon.com", expires: float | None = -1):
    item = {"name": name, "value": value, "domain": domain, "path": "/"}
    if expires is not None:
        item["expires"] = expires
    return item


def test_full_header_stable_order_domain_filter():
    site = builtin_profiles().get("patreon")
    cookies = [
        _cookie("zeta", "z", ".patreon.com"),
        _cookie("session_id", "s1", "www.patreon.com"),
        _cookie("alpha", "a", ".patreon.com"),
        _cookie("foreign", "nope", ".google.com"),
    ]
    result = build_cookie_header(cookies, site)
    assert result.header.reveal() == "alpha=a; session_id=s1; zeta=z"
    assert result.names == ("alpha", "session_id", "zeta")
    assert result.count == 3
    assert result.total_length == len(result.header.reveal())
    assert result.required_present is True
    assert "foreign" not in result.names


def test_explicit_list_mode():
    site = _site(header_cookies=["session_id", "csrf"])
    cookies = [
        _cookie("session_id", "s1"),
        _cookie("csrf", "tok"),
        _cookie("noise", "x"),
    ]
    result = build_cookie_header(cookies, site)
    assert result.header.reveal() == "csrf=tok; session_id=s1"
    assert result.names == ("csrf", "session_id")


def test_required_cookie_missing_exit_3():
    site = builtin_profiles().get("patreon")
    cookies = [_cookie("other", "x")]
    with pytest.raises(TokenAuthError) as caught:
        build_cookie_header(cookies, site)
    assert caught.value.exit_code == EXIT_AUTH
    assert str(caught.value).startswith("GATE auth:")
    assert "session_id" in str(caught.value)


def test_expired_required_cookie_exit_3():
    site = builtin_profiles().get("patreon")
    cookies = [_cookie("session_id", "old", expires=time.time() - 60)]
    with pytest.raises(TokenAuthError) as caught:
        build_cookie_header(cookies, site)
    assert caught.value.exit_code == EXIT_AUTH
    assert str(caught.value).startswith("GATE auth:")
    assert "expired" in str(caught.value).lower()


def test_session_cookie_no_expiry_accepted():
    site = builtin_profiles().get("patreon")
    for expires in (-1, 0, None):
        cookies = [_cookie("session_id", "live", expires=expires)]
        result = build_cookie_header(cookies, site)
        assert result.required_present is True
        assert result.earliest_expiry is None


def test_value_with_equals_preserved():
    site = builtin_profiles().get("patreon")
    cookies = [_cookie("session_id", "abc=def=ghi")]
    result = build_cookie_header(cookies, site)
    assert result.header.reveal() == "session_id=abc=def=ghi"
