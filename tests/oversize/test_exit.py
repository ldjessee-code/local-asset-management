# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exit 2 for bad input. Exit 3 when pyvips cannot be imported."""

from __future__ import annotations

from pathlib import Path

import pytest

from lam.cli import main
from lam.oversize.errors import MissingPyvips


def test_exit_2_bad_folder_bad_zip_bad_register(tmp_path: Path, capsys):
    register = tmp_path / "reg.json"
    with pytest.raises(SystemExit) as missing:
        main(["oversize", "scan", str(tmp_path / "nope"), "--register", str(register)])
    assert missing.value.code == 2

    with pytest.raises(SystemExit) as bad_zip:
        main(["oversize", "scan", str(tmp_path), "--zip", "sometimes"])
    assert bad_zip.value.code == 2

    folder = tmp_path / "maps"
    folder.mkdir()
    with pytest.raises(SystemExit) as bad_quality:
        main(
            [
                "oversize",
                "scan",
                str(folder),
                "--register",
                str(register),
                "--quality",
                "0",
                "--min-mp",
                "89",
            ]
        )
    assert bad_quality.value.code == 2

    register.write_text("{", encoding="utf-8")
    with pytest.raises(SystemExit) as bad_reg:
        main(["oversize", "scan", str(folder), "--register", str(register), "--record"])
    assert bad_reg.value.code == 2
    assert register.read_text(encoding="utf-8") == "{"


def test_exit_3_pyvips_import_failure(tmp_path: Path, monkeypatch, capsys):
    def boom():
        raise ImportError("simulated missing pyvips")

    monkeypatch.setattr("lam.oversize.images.load_pyvips", boom)
    folder = tmp_path / "maps"
    folder.mkdir()
    with pytest.raises(SystemExit) as caught:
        main(["oversize", "scan", str(folder), "--register", str(tmp_path / "reg.json")])
    assert caught.value.code == 3
    assert "pip install pyvips-binary pyvips" in capsys.readouterr().err
    assert MissingPyvips().code == 3
