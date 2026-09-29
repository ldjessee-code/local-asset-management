# Local Asset Management

Generic multi-source file librarian: inventory, exact-dedupe, archive
pairing, configurable physical layout, quarantine. Not tied to a any app or service.

Primary use on a home library drive is game screenshots plus tabletop
map/token packs (often Patreon zips). The same engine and a different
config covers spreadsheets, zip-only archives, or a photo dump.

v0.1 is a **Python engine** with a **CLI** and a **LAN web UI** (HTML/CSS/JS).
The browser is a remote control. Hashing and copies always run on the
machine that can see the disks — not in the browser.

Design notes: [library-index-spec.md](library-index-spec.md).
What it can do (for humans and AIs): `lam capabilities` or
[lam/capabilities.py](lam/capabilities.py).
Developers: [docs/architecture.md](docs/architecture.md), [AGENTS.md](AGENTS.md).

## What v0.1 does

- Many input roots, overlapping content
- JSONC config (JSON with `//` and `/* */` comments)
- `scan` / `report` / `plan` / `apply`
- Exact byte duplicates only (size → 64 KB prefix → SHA-256)
- Zip listing without extract; nested zip listed, not exploded
- New library tree via **copy** (or hardlink on the same volume)
- Quarantine extras; **no delete** in the library pipeline
- Layout is a named template per source, not inferred
- Optional **`lam actions`** JSON runner (separate from the pipeline) to
  move / copy / mkdir / recycle with a dry run, `--apply`, and an undo log
- Optional **`lam token`** to keep a site session cookie in a persistent
  browser profile (`login` / `get` / `status`) for tools such as a Patreon
  downloader. See [docs/token-fetcher.md](docs/token-fetcher.md).

Not in v0.1: Foundry/Roll20 awareness, perceptual hash, embeddings,
auto-tagging, clustering as folder names.

## Requirements

- Python 3.11+ (the start script will say so if it is missing)
- A machine that can see the files (local disks, mapped drives, or UNC)

Run the engine on the computer that has the mounts. Tests stay on
temporary fixtures. Live disks are only used when you point the config
at them.

## Start (one command)

After `git clone` (or a zip of this repo), from the project folder:

| System | First run and every run after |
|---|---|
| Windows | double-click `start.cmd`, or `start.cmd` in cmd/PowerShell |
| PowerShell | `.\start.ps1` (if scripts are blocked, use `start.cmd`) |
| Linux / macOS | `./start.sh` |

That creates `.venv`, installs the app, and opens the UI (`lam serve`).
You do not activate the venv. First run needs the network; later runs are
local and fast.

```text
start.cmd
start.cmd --host 0.0.0.0
start.cmd scan
./start.sh capabilities
```

No extra args → UI. A flag like `--host` is passed to `serve`. A command
name (`scan`, `report`, `plan`, `apply`, `capabilities`) is passed to `lam`.

If `./start.sh` is not executable: `bash start.sh` or `python3 start.py`.

### Developers (tests)

```text
python start.py
.venv\Scripts\pip install -e ".[dev]"
pytest
```

On Unix: `.venv/bin/pip install -e ".[dev]"`.

`lam capabilities` prints what the program can do. `lam capabilities --json`
is the same data as `GET /api/capabilities`.

## Config

The default file is **`library.jsonc`** (or `library.json`) in the folder
where you run `lam`. You do not pass `--config` unless you want a different
file. `LAM_CONFIG` also works.

Keep that file **outside** every source folder and **outside** the
destination (including `media`, `_quarantine`, `index`, `logs`, `reports`).
The engine refuses to run if the config sits in one of those trees, so a
copy cannot overwrite it.

```text
lam scan
lam report
lam plan
lam apply --yes
```

### File-action plans (`lam actions`)

A planner (a person, a script, or a local model) writes a JSON plan
(`file-action-plan/v1`, or another id the schema registry still accepts).
The runner validates it, dry-runs every pre-check by default, and executes
only with `--apply`. Nothing is overwritten. `recycle` sends items to the
Windows Recycle Bin; there is no permanent delete. A cross-volume file move
verifies the copy, then sends the source to the Recycle Bin the same way.
Paths under a `.git` directory are refused. Paths at or under `C:\Example`
are refused (`refused: example path`) on dry-run, `--apply`, and undo.
`--apply` appends an undo CSV (`MOVE` / `COPY` / `RECYCLE` / `MKDIR`) next
to the results file.

This command does **not** use `library.jsonc` and does **not** change how
`lam apply` or `never_modify_sources` behave.

```powershell
# Dry run (default): writes results JSON only
python -m lam actions run examples\file-action-plan.sample.json

# Execute, with optional output paths
python -m lam actions run plan.json --apply --results results.json --undo-log undo.csv

# Replay an undo log (dry run, then execute)
python -m lam actions undo undo.csv
python -m lam actions undo undo.csv --apply
```

Sample plan and results: [examples/file-action-plan.sample.json](examples/file-action-plan.sample.json),
[examples/file-action-results.sample.json](examples/file-action-results.sample.json).
Schema files: `lam/schemas/file-action-plan.v1.schema.json` and
`lam/schemas/file-action-results.v1.schema.json`.
Field reference, exit codes, and the versioning policy:
[docs/file-action-plan.md](docs/file-action-plan.md).
Schema registry: `lam/schemas/registry.py`.

### Site session cookies (`lam token`)

`lam token login patreon` opens a visible Chromium window on a persistent
profile so you can log in by hand. `lam token get patreon` reads those
cookies and writes the Cookie header to a secrets file under
`%LOCALAPPDATA%\lam\secrets\` (never into the repo or Dropbox). Playwright
is an optional extra: `pip install -e ".[token]"` then
`python -m playwright install chromium`. Details, exit codes, and the
library API: [docs/token-fetcher.md](docs/token-fetcher.md).

### Patreon staging (`lam patreon`)

`lam patreon list` and `lam patreon sync` stage posts the logged-in patron
can already see. Dry-run is the default. `--apply` writes an inbox under
the configured staging root (normally `F:\PatreonDL`) and a manifest for
notLib. It does not unzip files and does not write into Dropbox `/Gaming`.
Cookie setup, the four JSON formats, and exit codes:
[docs/patreon-sync.md](docs/patreon-sync.md). The sample creators file
points at `C:\Example` on purpose, so `--apply` cannot write.

```text
lam patreon validate-config examples\patreon-creators.sample.json
lam patreon list --creators examples\patreon-creators.sample.json --fixture-dir tests\fixtures\patreon --last-months 12
lam patreon sync --creators PATH\patreon-creators.json
```

Or create it from the UI with no file at all:

```text
lam serve
```

The setup screen asks for source folders, an output folder, and where to
save `library.jsonc`. Paths are checked before anything is copied. Copies
go in `output\media`; index, logs, and quarantine sit beside that.

Templates (committed; copy one to `library.jsonc`): see
[examples/](examples/). `--config` is only an override:

```text
lam --config examples\e-test.jsonc scan
```

Paths in the config that are not absolute are resolved relative to the
config file. Sources must exist as folders. The destination is created
if needed.

### Layout names

| Name | Good for |
|---|---|
| `keep_relpath` | Don’t surprise me |
| `by_year` | Screenshots, camera dumps |
| `by_year_month` | Photo library later |
| `pack_tree` | Artist/Patreon sets |
| `by_ext` | “All the zips”, spreadsheets |
| `sidecar_with_parent` | Readmes next to art |
| `zip_store` | Where the zip files themselves go |

`priority` is the only automatic “keep this copy” rule when two files
share a SHA-256. Tie-break: deeper relative path, then shorter path,
then first seen.

`apply.never_modify_sources` defaults to true: v0.1 copies (or hardlinks)
into the library and quarantine. It does not move or delete sources.

Optional `protect_paths` lists extra drives or folders that apply will
refuse to write into. The configured library and quarantine remain the
allowed write targets.

## Web UI

The engine process serves the UI. Open it from a laptop or a phone on
the home LAN. The phone never mounts the NAS; it talks to the engine.

```text
start.cmd
start.cmd --host 0.0.0.0 --port 8765
```

Unix: `./start.sh` or `./start.sh --host 0.0.0.0 --port 8765`.

Default bind is `127.0.0.1:8765` (localhost only). Use `--host 0.0.0.0`
to reach it from a phone. A token is required; the CLI prints a URL
that includes it. Set `LAM_TOKEN` to keep the same token across restarts.

This is a **home/LAN** tool. Do not put it on the public internet.

## Safety

- `scan` and `report` are read-only
- `plan` writes a plan into the index DB, not the library tree
- **No copies until you approve:** `lam apply --yes` or the UI checkbox
- No delete in the library pipeline (v0.1). `lam apply` still never
  modifies sources when `never_modify_sources` is true
- `protect_paths` (optional) blocks writes outside the library and quarantine
- `lam actions` is a separate opt-in runner: dry-run default, `--apply` to
  execute, never overwrite, Recycle Bin not delete, `.git` paths refused,
  `C:\Example` refused, undo log on apply

## License

SPDX: **AGPL-3.0-or-later**

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or (at
your option) any later version.

The license text is the unmodified FSF document in [`LICENSE`](LICENSE)
(plain text, no extension — that is the usual layout).

## Commercial licensing

If you cannot comply with the AGPL (for example you need to ship this
inside a proprietary product, or run a modified instance as a service
without offering source), a **commercial license is available**. See
[COMMERCIAL.md](COMMERCIAL.md).
