# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local Asset Management — multi-source file librarian.

The **engine** walks disks, hashes exact duplicates, plans a library tree, and
copies. The **CLI** (``lam``) and **web UI** (``lam serve``) are faces of that
engine. The browser does not hash or copy files.

Typical pipeline::

    load_config → run_scan → write_reports → run_plan → run_apply

Discoverability:

- ``lam capabilities`` / ``lam capabilities --json``
- ``GET /api/capabilities`` and ``/docs`` (OpenAPI) when the UI is running
- ``lam.describe_capabilities()`` from Python

Tests use fixtures. Live disks and writes need the operator’s config and
approval (`apply --yes` or the UI confirm). Optional ``protect_paths``
blocks extra drives or folders from being modified.
"""

from __future__ import annotations

from lam.apply import run_apply
from lam.capabilities import describe_capabilities, format_capabilities_text
from lam.config import (
    ConfigError,
    find_default_config,
    load_config,
    setup_from_form,
)
from lam.plan import run_plan
from lam.report import write_reports
from lam.scan import run_scan
from lam.version import __version__

__all__ = [
    "__version__",
    "ConfigError",
    "describe_capabilities",
    "find_default_config",
    "format_capabilities_text",
    "load_config",
    "run_apply",
    "run_plan",
    "run_scan",
    "setup_from_form",
    "write_reports",
]
