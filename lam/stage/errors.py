# SPDX-License-Identifier: AGPL-3.0-or-later
"""Errors for ``lam stage``, ``lam place``, and ``lam publish``."""

from __future__ import annotations


class StageError(Exception):
    """Bad arguments or a job that cannot be placed. ``code`` is the exit code."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code
