# SPDX-License-Identifier: AGPL-3.0-or-later
"""Errors for ``lam tag``. ``TagError`` is exit 2 at the CLI."""

from __future__ import annotations


class TagError(ValueError):
    """The tags, sub-bins, or arguments are invalid. CLI maps this to exit 2."""
