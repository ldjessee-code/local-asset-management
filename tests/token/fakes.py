# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fake Playwright context and httpx client for token tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def goto(self, url: str, **kwargs) -> None:
        self.calls.append(("goto", url, kwargs))

    def fill(self, *args, **kwargs) -> None:
        self.calls.append(("fill", args, kwargs))

    def type(self, *args, **kwargs) -> None:
        self.calls.append(("type", args, kwargs))

    def press(self, *args, **kwargs) -> None:
        self.calls.append(("press", args, kwargs))

    @property
    def keyboard(self) -> FakePage:
        return self


class FakeContext:
    def __init__(self, user_data_dir: Path, headless: bool, cookies: list[dict]) -> None:
        self.user_data_dir = Path(user_data_dir)
        self.headless = headless
        self._cookies = list(cookies)
        self.closed = False
        self.pages = [FakePage()]
        self.cookies_error: BaseException | None = None

    def cookies(self) -> list[dict]:
        if self.cookies_error is not None:
            raise self.cookies_error
        return list(self._cookies)

    def close(self) -> None:
        self.closed = True

    def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

    def on(self, event: str, handler) -> None:  # noqa: ARG002
        return None


class FakeBrowserLayer:
    def __init__(self, cookies: list[dict] | None = None) -> None:
        self.cookies = list(cookies or [])
        self.launches: list[dict[str, Any]] = []
        self.contexts: list[FakeContext] = []
        self.cookies_error: BaseException | None = None

    def __call__(self, user_data_dir, *, headless: bool, **kwargs) -> FakeContext:
        ctx = FakeContext(Path(user_data_dir), headless, self.cookies)
        ctx.cookies_error = self.cookies_error
        self.launches.append(
            {"user_data_dir": Path(user_data_dir), "headless": headless, "kwargs": kwargs}
        )
        self.contexts.append(ctx)
        return ctx


class FakeResponse:
    def __init__(self, status_code: int, text: str = "", json_data=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeHttpxClient:
    def __init__(self, responses: list[Any], errors: list[BaseException] | None = None):
        self.responses = list(responses)
        self.errors = list(errors or [])
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.errors:
            raise self.errors.pop(0)
        if not self.responses:
            raise AssertionError("no fake httpx responses left")
        return self.responses.pop(0)

    def close(self) -> None:
        return None
