# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 15: blocker exit codes 3-7 and one gate stops the run."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.patreon.builders import creator, page, post, write_config, write_fixture
from tests.patreon.conftest import gate_lines, invoke

GATE_AUTH = (
    "GATE auth: Patreon session expired - run: lam token login patreon "
    "(or refresh PATREON_SESSION_COOKIE) and rerun (no files written)"
)
GATE_CHALLENGE = "GATE challenge: captcha or Cloudflare challenge - rerun after it clears (no files written)"
GATE_VERIFICATION = (
    "GATE verification: 2FA or verification required - finish it in the browser and rerun (no files written)"
)
GATE_RATE = "GATE rate: HTTP 429 - wait and rerun (no files written)"
GATE_NETWORK = "GATE network: Patreon request failed - check the connection and rerun (no files written)"


def _cfg(tmp_path: Path) -> Path:
    return write_config(
        tmp_path / "creators.json",
        [creator("Kidney_Boy", "https://www.patreon.com/kidneyboy", campaign_id="9001")],
        staging_root=str(tmp_path / "stage"),
    )


def _install(monkeypatch, handler):
    import lam.patreon.http_source as http_source

    def factory():
        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(http_source, "_client_factory", factory)
    monkeypatch.setenv("LAM_PATREON_COOKIE", "session_id=fixture-session")

    def boom(site):
        raise RuntimeError("no profile")

    monkeypatch.setattr("lam.patreon.cookies.get_cookie_header", boom)


@pytest.mark.parametrize(
    ("status", "body", "code", "line"),
    [
        (401, "nope", 3, GATE_AUTH),
        (403, "nope", 3, GATE_AUTH),
        (403, "<html>Just a moment cf-challenge</html>", 4, GATE_CHALLENGE),
        (200, "<html>Enter the verification code</html>", 5, GATE_VERIFICATION),
        (429, "slow down", 6, GATE_RATE),
    ],
)
def test_whoami_gates(tmp_path: Path, monkeypatch, capsys, status, body, code, line):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    _install(monkeypatch, handler)
    results = tmp_path / "out.json"
    got = invoke(
        ["patreon", "sync", "--creators", str(_cfg(tmp_path)), "--since", "2025-01-01", "--results", str(results)]
    )
    captured = capsys.readouterr()
    assert got == code
    assert gate_lines(captured.err) == [line]
    doc = json.loads(results.read_text(encoding="utf-8"))
    assert doc["gate"]["exit_code"] == code
    assert line in doc["gate"]["line"]


def test_network_gate(tmp_path: Path, monkeypatch, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns lookup failed")

    _install(monkeypatch, handler)
    got = invoke(["patreon", "list", "--creators", str(_cfg(tmp_path)), "--since", "2025-01-01"])
    captured = capsys.readouterr()
    assert got == 7
    assert gate_lines(captured.err) == [GATE_NETWORK]


def test_gate_on_second_creator_stops(tmp_path: Path, capsys):
    from lam.patreon.http_source import FixtureSource

    FixtureSource.pages_read = []
    root = tmp_path / "fx"
    write_fixture(root, "kidneyboy", [page([post("p1", "Harbor", "2026-06-01T15:00:00+00:00")])])
    (root / "posts-partyoftwo.json").write_text(json.dumps({"gate": "rate"}) + "\n", encoding="utf-8")
    (root / "campaign-partyoftwo.json").write_text(
        (root / "campaign-kidneyboy.json").read_text(encoding="utf-8").replace("kidneyboy", "partyoftwo"),
        encoding="utf-8",
    )
    write_fixture(root, "laterone", [page([post("p9", "Later", "2026-06-01T15:00:00+00:00")])])
    cfg = write_config(
        tmp_path / "creators.json",
        [
            creator("Kidney_Boy", "https://www.patreon.com/kidneyboy"),
            creator("Party_of_Two", "https://www.patreon.com/partyoftwo"),
            creator("Later_One", "https://www.patreon.com/laterone"),
        ],
        staging_root=str(tmp_path / "stage"),
    )
    results = tmp_path / "out.json"
    code = invoke(
        [
            "patreon",
            "sync",
            "--creators",
            str(cfg),
            "--fixture-dir",
            str(root),
            "--since",
            "2025-01-01",
            "--results",
            str(results),
        ]
    )
    captured = capsys.readouterr()
    assert code == 6
    assert gate_lines(captured.err) == [GATE_RATE]
    assert not any("laterone" in item for item in FixtureSource.pages_read)
    doc = json.loads(results.read_text(encoding="utf-8"))
    assert doc["gate"]["exit_code"] == 6
    assert any(post["post_title"] == "Harbor" for post in doc["posts"])
