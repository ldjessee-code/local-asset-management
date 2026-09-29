# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 3: cookie source order, normalization, redaction."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from lam.patreon.cookies import load_cookie, normalize_cookie
from lam.patreon.errors import EXIT_AUTH, EXIT_USAGE, PatreonAuthError, PatreonUsageError
from lam.token.secret import Secret
from tests.patreon.builders import attachment, creator, page, post, write_config, write_fixture
from tests.patreon.conftest import assert_no_secret, gate_lines, invoke

BODY = "FAKECOOKIE-abc123-ZZZ-NEVER-SHOW-9911"
FULL = "session_id=" + BODY
GATE_AUTH = (
    "GATE auth: Patreon session expired - run: lam token login patreon "
    "(or refresh PATREON_SESSION_COOKIE) and rerun (no files written)"
)


def test_source_order_token_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LAM_PATREON_COOKIE", "session_id=from-env-not-this")
    monkeypatch.setattr(
        "lam.patreon.cookies.get_cookie_header",
        lambda site: Secret("session_id=from-token-value"),
    )
    loaded = load_cookie()
    assert loaded.source == "lam.token"
    assert loaded.header.reveal() == "session_id=from-token-value"


def test_source_order_env_then_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def boom(site):
        raise RuntimeError("no profile")

    monkeypatch.setattr("lam.patreon.cookies.get_cookie_header", boom)
    monkeypatch.setenv("LAM_PATREON_COOKIE", "session_id=from-lam-env")
    monkeypatch.setenv("PATREON_SESSION_COOKIE", "session_id=from-old-env")
    loaded = load_cookie()
    assert loaded.source == "LAM_PATREON_COOKIE"
    assert loaded.header.reveal() == "session_id=from-lam-env"

    monkeypatch.delenv("LAM_PATREON_COOKIE")
    loaded = load_cookie()
    assert loaded.source == "PATREON_SESSION_COOKIE"
    assert loaded.header.reveal() == "session_id=from-old-env"

    monkeypatch.delenv("PATREON_SESSION_COOKIE")
    cookie_file = tmp_path / "patreon_cookie.txt"
    cookie_file.write_text("session_id=from-file\n", encoding="utf-8")
    monkeypatch.setenv("LAM_PATREON_COOKIE_FILE", str(cookie_file))
    loaded = load_cookie()
    assert loaded.source == "LAM_PATREON_COOKIE_FILE"
    assert loaded.header.reveal() == "session_id=from-file"


def test_cookie_file_inside_repo_refused(monkeypatch: pytest.MonkeyPatch):
    def boom(site):
        raise RuntimeError("no profile")

    monkeypatch.setattr("lam.patreon.cookies.get_cookie_header", boom)
    repo = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("LAM_PATREON_COOKIE_FILE", str(repo / "patreon_cookie.txt"))
    with pytest.raises(PatreonUsageError) as caught:
        load_cookie()
    assert caught.value.exit_code == EXIT_USAGE
    assert BODY not in str(caught.value)


def test_cookie_file_under_dropbox_refused(monkeypatch: pytest.MonkeyPatch):
    def boom(site):
        raise RuntimeError("no profile")

    monkeypatch.setattr("lam.patreon.cookies.get_cookie_header", boom)
    monkeypatch.setenv("LAM_PATREON_COOKIE_FILE", r"F:\Dropbox\secrets\patreon_cookie.txt")
    with pytest.raises(PatreonUsageError) as caught:
        load_cookie()
    assert caught.value.exit_code == EXIT_USAGE
    assert "Dropbox" in str(caught.value)


def test_missing_cookie_gate(monkeypatch: pytest.MonkeyPatch):
    def boom(site):
        raise RuntimeError("no profile")

    monkeypatch.setattr("lam.patreon.cookies.get_cookie_header", boom)
    with pytest.raises(PatreonAuthError) as caught:
        load_cookie()
    assert caught.value.exit_code == EXIT_AUTH
    assert str(caught.value) == GATE_AUTH


def test_normalize_quotes_prefix_bare_and_newline():
    assert normalize_cookie('  "session_id=abc"  ').header.reveal() == "session_id=abc"
    assert normalize_cookie("Cookie: session_id=abc").header.reveal() == "session_id=abc"
    bare = normalize_cookie("  baretoken  ")
    assert bare.header.reveal() == "session_id=baretoken"
    assert bare.warnings
    assert any("full" in item.lower() for item in bare.warnings)
    with pytest.raises(PatreonUsageError) as caught:
        normalize_cookie("session_id=ab\ncd")
    assert caught.value.exit_code == EXIT_USAGE
    assert "ab" not in str(caught.value)


def _client(handler):
    def factory():
        return httpx.Client(transport=httpx.MockTransport(handler))

    return factory


def _posts_ok(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/current_user"):
        return httpx.Response(200, json={"data": {"id": "u1", "attributes": {"full_name": "P"}}})
    if "campaigns" in path:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "9001",
                        "type": "campaign",
                        "attributes": {"vanity": "kidneyboy", "current_user_is_free_member": False},
                        "relationships": {"current_user_pledge": {"data": {"id": "p", "type": "pledge"}}},
                    }
                ]
            },
        )
    return httpx.Response(200, json={"data": [], "included": [], "links": {}})


def test_cookie_never_appears(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("LAM_PATREON_COOKIE", FULL)

    def boom(site):
        raise RuntimeError("no profile")

    monkeypatch.setattr("lam.patreon.cookies.get_cookie_header", boom)
    import lam.patreon.http_source as http_source

    monkeypatch.setattr(http_source, "_client_factory", _client(_posts_ok))

    root = tmp_path / "fx"
    resource, included = post(
        "p1",
        "Harbor",
        "2026-06-01T15:00:00+00:00",
        attachments=[attachment("a1", "map.zip", size=4)],
    )
    write_fixture(root, "kidneyboy", [page([(resource, included)])])
    (root / "files").mkdir()
    (root / "files" / "a1").write_bytes(b"zip!")
    cfg = write_config(
        tmp_path / "creators.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy", campaign_id="9001")],
        staging_root=str(tmp_path / "stage"),
    )
    results = tmp_path / "results.json"
    commands = [
        ["patreon", "validate-config", str(cfg)],
        [
            "patreon",
            "list",
            "--creators",
            str(cfg),
            "--since",
            "2025-01-01",
            "--until",
            "2026-12-31",
            "--results",
            str(results),
        ],
        [
            "patreon",
            "sync",
            "--creators",
            str(cfg),
            "--since",
            "2025-01-01",
            "--results",
            str(tmp_path / "dry.json"),
        ],
        [
            "patreon",
            "sync",
            "--creators",
            str(cfg),
            "--apply",
            "--fixture-dir",
            str(root),
            "--since",
            "2025-01-01",
        ],
    ]
    blobs = []
    for argv in commands:
        code = invoke(argv)
        captured = capsys.readouterr()
        blobs.append(captured.out)
        blobs.append(captured.err)
        blobs.append(f"exit={code}")
    monkeypatch.setattr(http_source, "_client_factory", _client(lambda request: httpx.Response(401, json={})))
    for status, marker in ((401, "auth"), (403, "auth")):
        monkeypatch.setattr(
            http_source,
            "_client_factory",
            _client(lambda request, status=status: httpx.Response(status, text="nope")),
        )
        invoke(["patreon", "list", "--creators", str(cfg), "--since", "2025-01-01"])
        captured = capsys.readouterr()
        blobs.append(captured.out)
        blobs.append(captured.err)
        assert marker == "auth"
    text = "\n".join(blobs) + caplog.text
    assert_no_secret(text, BODY)
    assert_no_secret(text, FULL)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            try:
                assert_no_secret(raw.decode("utf-8", errors="ignore"), BODY)
            except AssertionError:
                raise
    from lam.patreon.http_source import HttpPatreonSource

    assert_no_secret(repr(HttpPatreonSource(Secret(FULL))), BODY)
    loaded = load_cookie()
    assert_no_secret(repr(loaded), BODY)
    assert_no_secret(repr(loaded.header), BODY)
