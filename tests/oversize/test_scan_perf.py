# SPDX-License-Identifier: AGPL-3.0-or-later
"""The part search must not resolve paths or test set membership for name non-matches.

On a real pack (about 6,000 images, 600+ oversize) the old loop resolved every candidate
for every oversize file and ran for over half an hour before the first classification.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pyvips")

from lam.oversize import engine  # noqa: E402


def test_is_inside_only_called_for_name_matches(tmp_path, monkeypatch, write_image):
    root = tmp_path / "pack"
    write_image(root / "SetA" / "Keep_Combined_Day.jpg", 64, 64)
    write_image(root / "SetB" / "Keep_A_Day.jpg", 32, 32)
    for index in range(40):
        write_image(root / "SetC" / f"Other_{index:02d}_Night.jpg", 16, 16)
    calls = []
    real = engine.is_inside

    def counting(path, directory):
        calls.append(path)
        return real(path, directory)

    monkeypatch.setattr(engine, "is_inside", counting)
    payload, code = engine.run_oversize_scan(
        root,
        min_mb=100000,
        min_mp=0.004,
        register=tmp_path / "reg.json",
        part_scope="set",
    )
    assert code == 0
    assert [row["original_path"].endswith("Keep_Combined_Day.jpg") for row in payload["files"]] == [True]
    assert len(calls) <= 1
