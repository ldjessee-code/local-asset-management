# SPDX-License-Identifier: AGPL-3.0-or-later
"""Errors for ``lam oversize``. ``code`` is the process exit code."""

from __future__ import annotations


class OversizeError(Exception):
    """Bad arguments, a bad register, or a file that could not be handled."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


class MissingPyvips(OversizeError):
    """pyvips could not be imported. The rest of lam still runs."""

    def __init__(self) -> None:
        super().__init__(
            "pyvips is not installed. Install it with: pip install pyvips-binary pyvips",
            code=3,
        )


class ZipVerifyError(OversizeError):
    """The zip could not be proved to contain the original bytes."""

    def __init__(self, message: str):
        super().__init__(message, code=4)
