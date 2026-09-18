from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from lam.config import load_config

PNG_A = b"\x89PNG\r\n\x1a\n" + b"A" * 200
PNG_B = b"\x89PNG\r\n\x1a\n" + b"B" * 320
PNG_C = b"\x89PNG\r\n\x1a\n" + b"C" * 180


@pytest.fixture
def library_env(tmp_path: Path):
    shots = tmp_path / "src" / "screenshots"
    patreon = tmp_path / "src" / "patreon" / "Artist" / "Pack"
    downloads = tmp_path / "src" / "downloads"
    shots.mkdir(parents=True)
    patreon.mkdir(parents=True)
    downloads.mkdir(parents=True)

    (shots / "unique.png").write_bytes(PNG_C)
    (patreon / "token.png").write_bytes(PNG_A)
    (patreon / "README.md").write_text("pack notes\n", encoding="utf-8")
    (downloads / "token.png").write_bytes(PNG_A)  # exact dupe of pack token
    (downloads / "notes.txt").write_text("orphan sidecar\n", encoding="utf-8")
    (shots / "also-token.png").write_bytes(PNG_A)

    zip_path = tmp_path / "src" / "patreon" / "Artist" / "Pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("token.png", PNG_A)
        zf.writestr("README.md", "pack notes\n")
        zf.writestr("nested/inner.zip", _inner_zip_bytes())

    cfg_path = tmp_path / "library.jsonc"
    cfg_path.write_text(
        f"""
        {{
          "profile": "spacious",
          "sources": [
            {{ "path": "src/screenshots", "layout": "by_year", "priority": 10 }},
            {{ "path": "src/patreon", "layout": "pack_tree", "priority": 50 }},
            {{ "path": "src/downloads", "layout": "keep_relpath", "priority": 5 }}
          ],
          "library_root": "library/media",
          "quarantine": "library/_quarantine",
          "index_db": "library/index/library.sqlite",
          "log_dir": "library/logs",
          "reports_dir": "library/reports",
          "hash_workers": 2
        }}
        """,
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    return {"root": tmp_path, "cfg_path": cfg_path, "cfg": cfg, "zip_path": zip_path}


def _inner_zip_bytes() -> bytes:
    from io import BytesIO

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("tiny.txt", "hi")
    return buf.getvalue()
