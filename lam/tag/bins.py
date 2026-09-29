# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixed level-1 bins. No game, vendor, or creator names."""

from __future__ import annotations

LEVEL1_BINS: tuple[str, ...] = (
    "_Maps",
    "_GameMods",
    "_Installers",
    "_Documents",
    "_ModelWeights",
    "_Images",
    "_Archives",
    "_Unknown",
)

UNSURE = "Unsure"
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_MODEL_URL = "http://127.0.0.1:11434"
DEFAULT_BURST_SECONDS = 300
DEFAULT_MAX_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_WEIGHT_MIN_BYTES = 50 * 1024 * 1024

RATING = {"high": 5, "low": 2, "unsure": 0}
