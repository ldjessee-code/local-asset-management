from fastapi.testclient import TestClient

from lam.scan import run_scan
from lam.web.app import create_app


def test_health_and_scan_via_api(library_env):
    cfg = library_env["cfg"]
    app = create_app(cfg, token="secret")
    client = TestClient(app)

    assert client.get("/api/health").status_code == 401
    headers = {"X-Lam-Token": "secret"}
    health = client.get("/api/health", headers=headers)
    assert health.status_code == 200
    assert health.json()["license"] == "AGPL-3.0-or-later"
    assert health.json()["needs_setup"] is False
    assert client.get("/", headers=headers).status_code == 200

    run_scan(cfg)
    ov = client.get("/api/overview", headers=headers)
    assert ov.status_code == 200
    assert "space_to_save" in ov.json()
    cmp = client.get("/api/compare", headers=headers)
    assert cmp.status_code == 200
    assert "rows" in cmp.json()
    summary = client.get("/api/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["files"] >= 7

    denied = client.post("/api/apply", headers=headers, json={})
    assert denied.status_code == 400
    stop = client.post("/api/stop", headers=headers)
    assert stop.status_code == 200
    assert stop.json()["stopping"] is True


def test_serve_without_config_offers_setup(tmp_path):
    app = create_app(None, token="secret")
    client = TestClient(app)
    headers = {"X-Lam-Token": "secret"}
    health = client.get("/api/health", headers=headers)
    assert health.json()["needs_setup"] is True
    scan = client.post("/api/scan", headers=headers)
    assert scan.status_code == 409
    assert scan.json()["needs_setup"] is True

    src = tmp_path / "pics"
    src.mkdir()
    (src / "a.png").write_bytes(b"x")
    dest = tmp_path / "out"
    dest.mkdir()
    saved = client.post(
        "/api/setup",
        headers=headers,
        json={
            "config_path": str(tmp_path / "library.jsonc"),
            "output_folder": str(dest),
            "sources": [{"path": str(src), "layout": "keep_relpath", "priority": 10}],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["config"]["needs_setup"] is False
    assert client.get("/api/health", headers=headers).json()["needs_setup"] is False
    moved = tmp_path / "out2"
    moved.mkdir()
    paths = client.post(
        "/api/paths",
        headers=headers,
        json={"library_root": str(moved / "media")},
    )
    assert paths.status_code == 200, paths.text
    assert "media" in paths.json()["config"]["library_root"]
