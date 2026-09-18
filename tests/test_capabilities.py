from fastapi.testclient import TestClient

from lam.capabilities import describe_capabilities, format_capabilities_text
from lam.cli import main
from lam.web.app import create_app


def test_describe_capabilities_has_pipeline_and_public_api():
    cap = describe_capabilities()
    assert cap["pipeline"] == ["scan", "report", "plan", "apply"]
    assert "lam.run_scan" in cap["public_python"]
    assert "delete files" in cap["safety"]["not_in_v1"]
    assert cap["constraints"]["writes_need_user_approval"] is True
    text = format_capabilities_text(cap)
    assert "lam scan" in text
    assert "keep_relpath" in text


def test_cli_capabilities_no_config(capsys):
    main(["capabilities"])
    out = capsys.readouterr().out
    assert "local-asset-management" in out
    main(["capabilities", "--json"])
    json_out = capsys.readouterr().out
    assert '"pipeline"' in json_out


def test_capabilities_http_needs_no_token():
    app = create_app(None, token="secret")
    client = TestClient(app)
    res = client.get("/api/capabilities")
    assert res.status_code == 200
    assert res.json()["version"]
    docs = client.get("/openapi.json")
    assert docs.status_code == 200
