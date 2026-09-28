# SPDX-License-Identifier: AGPL-3.0-or-later
"""Machine-readable inventory of what this program can do.

Import ``describe_capabilities()`` or run ``lam capabilities``.
The HTTP copy is ``GET /api/capabilities`` (no token) and OpenAPI is ``/docs``.
"""

from __future__ import annotations

from typing import Any

from lam.config import DEFAULT_LAYOUTS, LAYOUT_NAMES
from lam.version import __version__

SOURCE_URL = "https://github.com/ldjessee-code/local-asset-management"

# What v0.1 will not do. Keep this list in one place.
NOT_IN_V1 = (
    "delete files",
    "move or rewrite source files when never_modify_sources is true",
    "perceptual hash / similar-image matching",
    "Foundry or Roll20 pack awareness",
    "auto-tagging, embeddings, or clustering as folder names",
    "hashing or copying inside the browser / WebAssembly",
)

COMMANDS: tuple[dict[str, Any], ...] = (
    {
        "name": "serve",
        "config_required": False,
        "writes": False,
        "summary": "LAN web UI. Setup works with no config file.",
    },
    {
        "name": "scan",
        "config_required": True,
        "writes": False,
        "summary": "Walk sources, index zips, exact-dedupe via size → 64KB prefix → SHA-256.",
        "function": "lam.scan.run_scan",
    },
    {
        "name": "report",
        "config_required": True,
        "writes": False,
        "summary": "Write reports/summary.md and reports/exceptions.md from the last scan.",
        "function": "lam.report.write_reports",
    },
    {
        "name": "plan",
        "config_required": True,
        "writes": False,
        "summary": "Build copy/quarantine destinations from the last scan. Does not copy yet.",
        "function": "lam.plan.run_plan",
    },
    {
        "name": "apply",
        "config_required": True,
        "writes": True,
        "summary": "Execute the last plan (copy or hardlink). Requires --yes / confirm=true. No delete.",
        "function": "lam.apply.run_apply",
    },
    {
        "name": "capabilities",
        "config_required": False,
        "writes": False,
        "summary": "Print this inventory (add --json for machine-readable).",
        "function": "lam.capabilities.describe_capabilities",
    },
    {
        "name": "actions",
        "config_required": False,
        "writes": True,
        "summary": (
            "Separate opt-in JSON file-action runner (move/copy/recycle/mkdir). "
            "Dry-run default; --apply executes. Never overwrites. Recycle Bin, not delete."
        ),
        "function": "lam.actions.run_file_actions",
    },
)

MODULES: tuple[dict[str, str], ...] = (
    {"module": "lam.config", "role": "JSONC load/save, path checks, setup form"},
    {"module": "lam.scan", "role": "Walk + zip index + duplicate groups"},
    {"module": "lam.hashing", "role": "Size/prefix/full SHA-256 with inode cache"},
    {"module": "lam.zips", "role": "List zip entries; nested zip listed, not exploded to disk"},
    {"module": "lam.plan", "role": "Layout templates → dest paths; quarantine extras"},
    {"module": "lam.apply", "role": "Copy/hardlink into library_root and quarantine"},
    {
        "module": "lam.actions",
        "role": "Opt-in JSON file-action runner (dry-run default, --apply, undo log)",
    },
    {"module": "lam.report", "role": "Markdown + JSON summary of the index"},
    {"module": "lam.web.app", "role": "FastAPI UI and job API over the same engine"},
    {"module": "lam.cli", "role": "lam command entrypoint"},
)

HTTP_API: tuple[dict[str, str], ...] = (
    {"method": "GET", "path": "/api/capabilities", "auth": False, "summary": "This inventory"},
    {"method": "GET", "path": "/api/health", "auth": True, "summary": "Version, job state, needs_setup"},
    {"method": "GET", "path": "/api/config", "auth": True, "summary": "Sanitized loaded config"},
    {"method": "GET", "path": "/api/setup/defaults", "auth": True, "summary": "Suggested library.jsonc path and layouts"},
    {"method": "POST", "path": "/api/setup/check", "auth": True, "summary": "Validate sources/output without writing"},
    {"method": "POST", "path": "/api/setup", "auth": True, "summary": "Write library.jsonc and load it"},
    {"method": "POST", "path": "/api/setup/load", "auth": True, "summary": "Load an existing config file"},
    {"method": "POST", "path": "/api/scan", "auth": True, "summary": "Background scan + report"},
    {"method": "POST", "path": "/api/report", "auth": True, "summary": "Rewrite markdown reports"},
    {"method": "POST", "path": "/api/plan", "auth": True, "summary": "Background plan"},
    {"method": "POST", "path": "/api/apply", "auth": True, "summary": "Background apply; body {confirm: true}"},
    {"method": "GET", "path": "/api/status", "auth": True, "summary": "Current job + log tail"},
    {"method": "GET", "path": "/api/summary", "auth": True, "summary": "JSON report"},
    {"method": "GET", "path": "/docs", "auth": False, "summary": "OpenAPI UI"},
    {"method": "GET", "path": "/openapi.json", "auth": False, "summary": "OpenAPI schema"},
)

PUBLIC_PYTHON: tuple[str, ...] = (
    "lam.load_config",
    "lam.find_default_config",
    "lam.setup_from_form",
    "lam.run_scan",
    "lam.write_reports",
    "lam.run_plan",
    "lam.run_apply",
    "lam.run_file_actions",
    "lam.undo_file_actions",
    "lam.describe_capabilities",
    "lam.web.app.create_app",
)


def describe_capabilities() -> dict[str, Any]:
    """Return the inventory as a JSON-serializable dict."""
    return {
        "name": "local-asset-management",
        "version": __version__,
        "license": "AGPL-3.0-or-later",
        "source": SOURCE_URL,
        "summary": (
            "Multi-source local file librarian: inventory, exact-dedupe, "
            "named layouts, quarantine. Python engine; browser is a remote control."
        ),
        "pipeline": ["scan", "report", "plan", "apply"],
        "commands": [dict(c) for c in COMMANDS],
        "layouts": {name: DEFAULT_LAYOUTS[name] for name in LAYOUT_NAMES},
        "config": {
            "default_names": ["library.jsonc", "library.json"],
            "env": "LAM_CONFIG",
            "must_not_sit_inside": [
                "any source folder",
                "library_root (usually output/media)",
                "output folder that contains media/_quarantine/index/logs/reports",
            ],
            "examples_dir": "examples/",
        },
        "safety": {
            "scan_is_read_only": True,
            "no_delete_in_v1": True,
            "never_modify_sources_default": True,
            "apply_needs_confirm": True,
            "protect_paths": "optional extra roots apply will not write into",
            "not_in_v1": list(NOT_IN_V1),
            "file_actions": {
                "schema": "file-action-plan/v1",
                "results_schema": "file-action-results/v1",
                "command": "lam actions run PLAN.json",
                "undo_command": "lam actions undo UNDO.csv",
                "dry_run_default": True,
                "needs_apply_flag": True,
                "never_overwrite": True,
                "recycle_not_delete": True,
                "refuses_git_paths": True,
                "writes_undo_log": True,
                "separate_from_pipeline": True,
            },
        },
        "modules": [dict(m) for m in MODULES],
        "http": [dict(h) for h in HTTP_API],
        "public_python": list(PUBLIC_PYTHON),
        "constraints": {
            "python": ">=3.11",
            "tests_use_tmp_path_only": True,
            "writes_need_user_approval": True,
            "web_ui_is_lan_only": True,
        },
        "first_run": {
            "windows": "start.cmd",
            "powershell": ".\\start.ps1",
            "unix": "./start.sh",
            "any_python": "python start.py",
            "default": "serve",
        },
    }


def format_capabilities_text(data: dict[str, Any] | None = None) -> str:
    """Human-readable dump of ``describe_capabilities()``."""
    cap = data or describe_capabilities()
    lines = [
        f"{cap['name']} {cap['version']}  ({cap['license']})",
        cap["summary"],
        "",
        "Pipeline: " + " → ".join(cap["pipeline"]),
        "",
        "Commands:",
    ]
    for cmd in cap["commands"]:
        need = "" if cmd["config_required"] else " (no config file)"
        write = " [writes]" if cmd["writes"] else ""
        lines.append(f"  lam {cmd['name']}{need}{write}")
        lines.append(f"      {cmd['summary']}")
    lines += ["", "Layouts:"]
    for name, pattern in cap["layouts"].items():
        lines.append(f"  {name}: {pattern}")
    lines += ["", "Not in v0.1:"]
    for item in cap["safety"]["not_in_v1"]:
        lines.append(f"  - {item}")
    lines += ["", "Public Python:"]
    for name in cap["public_python"]:
        lines.append(f"  {name}")
    lines += ["", f"Source: {cap['source']}"]
    return "\n".join(lines) + "\n"
