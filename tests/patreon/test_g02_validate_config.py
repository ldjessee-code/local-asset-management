# SPDX-License-Identifier: AGPL-3.0-or-later
"""Group 2: lam patreon validate-config, offline."""

from __future__ import annotations

import json
import socket
from pathlib import Path

from tests.patreon.conftest import invoke

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "examples" / "patreon-creators.sample.json"


def test_sample_exits_0(capsys):
    code = invoke(["patreon", "validate-config", str(SAMPLE)])
    assert code == 0
    out = capsys.readouterr().out
    assert "patreon-creators/v1" in out
    assert "2 creators" in out


def test_missing_slug_exits_2(tmp_path: Path, capsys):
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    del data["creators"][0]["slug"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    code = invoke(["patreon", "validate-config", str(path)])
    captured = capsys.readouterr()
    assert code == 2
    assert "slug" in captured.err + captured.out


def test_unknown_schema_exits_2(tmp_path: Path, capsys):
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data["schema"] = "patreon-creators/v9"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    code = invoke(["patreon", "validate-config", str(path)])
    text = capsys.readouterr().err
    assert code == 2
    assert "patreon-creators/v9" in text
    assert "patreon-creators/v1" in text


def test_validate_does_not_open_a_socket(capsys):
    code = invoke(["patreon", "validate-config", str(SAMPLE)])
    assert code == 0
    with __import__("pytest").raises(RuntimeError, match="network is blocked"):
        socket.socket()


def test_accidental_whoami_fails():
    from lam.patreon.http_source import HttpPatreonSource
    from lam.token.secret import Secret

    source = HttpPatreonSource(Secret("session_id=not-a-real-cookie"))
    try:
        source.whoami()
    except Exception as exc:
        assert "not-a-real-cookie" not in str(exc)
        assert "not-a-real-cookie" not in repr(exc)
    else:
        raise AssertionError("live whoami should have failed closed")
