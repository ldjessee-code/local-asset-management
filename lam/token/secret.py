# SPDX-License-Identifier: AGPL-3.0-or-later
"""Redacting wrapper so cookie values never appear in logs or ``repr()``."""

from __future__ import annotations

_REDACTED = "<redacted>"


class Secret:
    """A secret string. ``str`` / ``repr`` / ``format`` never include the value."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        object.__setattr__(self, "_value", value)

    def reveal(self) -> str:
        """Return the real value. Callers must not print it."""
        return self._value

    def __len__(self) -> int:
        return len(self._value)

    def __str__(self) -> str:
        return _REDACTED

    def __repr__(self) -> str:
        return "Secret(<redacted>)"

    def __format__(self, spec: str) -> str:
        return _REDACTED

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Secret):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)
