# SPDX-License-Identifier: AGPL-3.0-or-later
"""Playwright persistent-context helper. The only module that imports Playwright."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from lam.token.errors import PLAYWRIGHT_INSTALL_HINT, TokenDependencyError, gate

ContextFactory = Callable[..., Any]
WaitFn = Callable[[Any], None]

# Tests inject a fake. Production leaves this None and imports Playwright lazily.
_context_factory: ContextFactory | None = None
_wait_fn: WaitFn | None = None


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise TokenDependencyError(
            gate("deps", f"playwright not installed - run: {PLAYWRIGHT_INSTALL_HINT}")
        ) from exc
    return sync_playwright


def ensure_browser_available() -> None:
    """Raise ``TokenDependencyError`` before creating any profile directory."""
    if _context_factory is not None:
        return
    _require_playwright()


@contextmanager
def persistent_context(profile_dir: Path, *, headless: bool) -> Iterator[Any]:
    """Open a Chromium persistent context at *profile_dir*. Always closed on exit."""
    factory = _context_factory
    if factory is not None:
        ctx = factory(profile_dir, headless=headless)
        try:
            yield ctx
        finally:
            closer = getattr(ctx, "close", None)
            if closer is not None:
                closer()
        return

    sync_playwright = _require_playwright()
    with sync_playwright() as playwright:
        ctx = playwright.chromium.launch_persistent_context(str(profile_dir), headless=headless)
        try:
            yield ctx
        finally:
            ctx.close()


def wait_for_login(context: Any) -> None:
    """Wait until the user presses Enter (or an injected wait returns).

    Never types, fills, or presses keys in the page. Closing the window and
    then pressing Enter here lets Playwright flush the persistent profile.
    """
    injected = _wait_fn
    if injected is not None:
        injected(context)
        return
    print(
        "Log in in the browser window, then close it or press Enter here to save the session.",
        file=sys.stderr,
    )
    done = threading.Event()

    def _watch_enter() -> None:
        try:
            if not sys.stdin or not sys.stdin.isatty():
                done.set()
                return
            sys.stdin.readline()
        except (OSError, KeyboardInterrupt, EOFError):
            pass
        done.set()

    thread = threading.Thread(target=_watch_enter, daemon=True, name="lam-token-login")
    thread.start()
    done.wait()
