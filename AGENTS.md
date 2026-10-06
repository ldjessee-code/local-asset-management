# Notes for coding agents

This repo is a **local file librarian**. The Python package `lam` is the
product. The browser is a remote control, not the hasher.

## Do / do not

- **Do** run `pytest` after engine changes (`pip install -e ".[dev]"` once).
- **Do** keep first-run as `start.cmd` / `start.sh` / `start.py` (venv + install + `lam`).
- **Do** keep `library.jsonc` gitignored; put templates in `examples/`.
- **Do** keep tests on `tmp_path` fixtures. Do not scan or write the operator’s
  real disks unless they name those paths and approve the write.
- **Do not** add permanent delete, pHash, embeddings, or VTT-specific logic
  in v0.1. Recycle (Recycle Bin) exists only on `lam actions` with `--apply`.
- **Do not** put hashing or copies in JavaScript/WASM.

## Safety (writes)

- `scan` / `report` / `plan` do not change source files.
- `apply` copies (or hardlinks) only after **explicit user approval**
  (`lam apply --yes` or the UI confirm checkbox). No silent writes.
- `apply.never_modify_sources` defaults to true. The existing
  `scan → report → plan → apply` pipeline still never deletes or modifies
  sources.
- Optional `protect_paths` in the config lists extra drives or folders that
  must not be written to. The configured `library_root` and `quarantine` stay
  the allowed write targets.
- **`lam actions` is a separate opt-in runner**, not part of that pipeline.
  Dry-run is the default. It moves, copies, creates directories, or sends
  files to the Recycle Bin only with `--apply`. It never overwrites, never
  writes under a `.git` path, refuses `C:\Example`, and never permanently
  deletes. Recycle is the Windows Recycle Bin (recoverable), including the
  source of a verified cross-volume file move. `--apply` writes an undo CSV.

## Where truth lives

| Question | Read |
|---|---|
| What can it do? | `lam/capabilities.py` (also `lam capabilities --json`) |
| Public Python API | `lam/__init__.py` (`__all__`); token API is `lam.token` |
| Design intent | `library-index-spec.md` |
| How the pieces fit | `docs/architecture.md` |
| Human install / CLI | `README.md` |
| JSON file-action plan / results format | `docs/file-action-plan.md`, `lam/schemas/` |
| Site session cookies (`lam token`) | `docs/token-fetcher.md`, `lam/token/` |
| Patreon list / sync formats | `docs/patreon-sync.md`, `lam/schemas/registry.py`, `lam/patreon/` |
| Layered sorting (`lam tag`) | `docs/layered-sorting.md`, `lam/tag/` |
| License | `LICENSE` (AGPL-3.0-or-later, unmodified FSF text) |

## JSON formats

The schema registry is `lam/schemas/registry.py`. Each id is `supported`,
`deprecated` (with a removal note), or `removed`. Unknown and removed ids
fail before any work.

### File-action plan / results

`lam actions` reads a JSON plan and writes a JSON results file.

- Plan schema: `lam/schemas/file-action-plan.v1.schema.json` (`file-action-plan/v1`)
- Results schema: `lam/schemas/file-action-results.v1.schema.json` (`file-action-results/v1`)
- Field reference, exit codes, and the versioning policy: `docs/file-action-plan.md`

Paths at or under `C:\Example` are refused (`refused: example path`) on dry-run,
`--apply`, and undo, so `examples/file-action-plan.sample.json` cannot touch disk.

### Site profiles (`lam-site-profiles/v1`)

`lam token` loads a versioned site-profiles document (built-in `patreon` if
you pass no file). Schema file: `lam/schemas/site-profiles.v1.schema.json`.
Field reference, exit codes, library API: `docs/token-fetcher.md`. Sample:
`examples/site-profiles.sample.json`.

### Patreon (`lam patreon`)

`lam patreon` lists or stages posts the logged-in patron can already view.
Registry: `lam/schemas/registry.py`. Field reference and exit codes:
`docs/patreon-sync.md`.

- Creators config: `lam/schemas/patreon-creators.v1.schema.json` (`patreon-creators/v1`)
- Drop manifest: `lam/schemas/patreon-drop-manifest.v1.schema.json` (`patreon-drop-manifest/v1`)
- Dedupe index: `lam/schemas/patreon-index.v1.schema.json` (`patreon-index/v1`)
- Post list: `lam/schemas/patreon-post-list.v1.schema.json` (`patreon-post-list/v1`)

Dry-run is the default. `--apply` writes `F:\PatreonDL\<Creator>\_inbox_yyyyMMdd\`
and the manifest only. It does not expand zips and does not write Dropbox
`/Gaming`. The sample config stages under `C:\Example`, which is refused.

### Layered sorting (`lam tag`)

`lam tag scan` writes a tag document. `lam tag plan` writes one
`file-action-plan/v1` plus a review sibling. Neither command moves files.
Field reference: `docs/layered-sorting.md`. Registry: `lam/schemas/registry.py`.

- Tags: `lam/schemas/lam-tags.v1.schema.json` (`lam-tags/v1`)
- Sub-bins: `lam/schemas/lam-subbins.v1.schema.json` (`lam-subbins/v1`)
- Sidecar: `lam/schemas/lam-tag-sidecar.v1.schema.json` (`lam-tag-sidecar/v1`)
- Review list: `lam/schemas/lam-tag-review.v1.schema.json` (`lam-tag-review/v1`)

Sample sub-bins (placeholder names only): `examples/subbins.sample.json`.

## Secrets and browser profiles

Never print, log, commit, or put in a report a cookie value, or any slice
of it. That includes `lam patreon` manifests, indexes, results files, and
exception text. Persistent browser profiles and secrets files live outside
the repo and outside Dropbox (default `%LOCALAPPDATA%\lam\...`). Paths
inside the working tree, under `F:\Dropbox`, or under `C:\Example` are
refused. `lam token` never types credentials. Do not put a Patreon cookie
on a command line.

## Proof (token fetcher)

| Claim | Where |
|---|---|
| Sample site-profiles document loads; missing fields, unknown/removed schema ids, duplicates, `enabled:false` | `tests/token/test_profiles.py` |
| `lam-site-profiles/v1` in registry and `lam capabilities` | `tests/token/test_capabilities.py` |
| Path guards (repo, Dropbox, `C:\Example`, siblings, `LOCALAPPDATA` defaults) | `tests/token/test_paths.py` |
| Cookie header build; secret hygiene (value never in stdout/stderr/logs/repr) | `tests/token/test_cookies.py`, `tests/token/test_hygiene.py` |
| Secrets temp+rename; login/get fake browser; Playwright missing gate | `tests/token/test_store.py`, `tests/token/test_login.py`, `tests/token/test_get.py`, `tests/token/test_playwright_missing.py` |
| `status --check` mocked httpx; `--set-user-env`; library API | `tests/token/test_check.py`, `tests/token/test_env.py`, `tests/token/test_api.py` |

## Proof (patreon sync)

`pytest` (the repo test command) collects `tests/patreon/`.

| Claim | Where |
|---|---|
| `patreon-creators/v1` loads; missing fields, unknown/removed/deprecated ids, disabled creator, slug and regex checks | `tests/patreon/test_g01_creators_config.py` |
| `validate-config` exit 0/2 and no socket | `tests/patreon/test_g02_validate_config.py` |
| Cookie source order, path refusal, redaction | `tests/patreon/test_g03_cookies.py` |
| Local date window, month clamp, DST | `tests/patreon/test_g04_dates.py` |
| Posts page parsing | `tests/patreon/test_g05_parse.py` |
| List positive, empty, locked, not-a-member, pagination | `tests/patreon/test_g06_list_positive.py`, `tests/patreon/test_g07_list_negative.py`, `tests/patreon/test_g08_pagination.py` |
| Locked posts and outside links are not fetched | `tests/patreon/test_g09_locked.py`, `tests/patreon/test_g10_outside.py` |
| Dry-run writes no staging tree; apply layout and path refusal | `tests/patreon/test_g11_dry_run.py`, `tests/patreon/test_g12_apply_paths.py` |
| Dedupe, no overwrite, manifest/index schemas, no permanent delete | `tests/patreon/test_g13_duplicates.py`, `tests/patreon/test_g14_manifest_index.py`, `tests/patreon/test_g18_never_delete.py` |
| Gates 3–7, rate limit, Chronos exclusion | `tests/patreon/test_g15_gates.py`, `tests/patreon/test_g16_rate.py`, `tests/patreon/test_g17_chronos.py` |
| Registry, capabilities, and this file name the schema ids | `tests/patreon/test_g19_registry.py`, `tests/patreon/test_g20_docs.py` |

## Proof (layered sorting)

`pytest` collects `tests/tag/`. Tag tests block real sockets.

| Claim | Where |
|---|---|
| Scan fields, SHA-256, copy groups, batches, burst window, `.git` and symlink skip | `tests/tag/test_scan.py` |
| Each level-1 rule and level-2 hints | `tests/tag/test_rules.py` |
| Model cascade (agree, disagree, Unsure, same answer twice, unreachable, request body, `qwen3.8` refused) | `tests/tag/test_model.py` |
| Plan moves only high confidence, keeps batches together, recycles extra copies, no overwrite, dry-run | `tests/tag/test_plan.py` |
| Level 2 sub-bins; unknown schema ids exit 2 | `tests/tag/test_level2.py` |
| Sidecar field names; ExifTool missing and fake runner | `tests/tag/test_sidecar.py` |
| New schema ids in the registry and `lam capabilities` | `tests/tag/test_capabilities.py` |
| This file and `docs/layered-sorting.md` name the schema ids | `tests/tag/test_docs.py` |

## Pipeline

`scan` (read-only index) → `report` → `plan` (dest paths only) → `apply`
(copy/hardlink, needs confirm). No delete.

`lam actions run PLAN.json` is a **separate** JSON file-action runner
(`file-action-plan/v1`). Dry-run default; `--apply` executes. See README.

## Config

Default file: `library.jsonc` in cwd. Must not sit inside a source or the
destination tree (`lam.config.validate_config`). Setup UI can write it.

## Court queue

Court file chores go through gpu-queue as work type `lam`. The court calls this repo's existing CLI (`python -m lam`) with an allowlisted argv: `capabilities`, `scan`, `report`, `plan`, `apply`, `tag scan`, and `tag plan`. This repo's code is unchanged. The queue does not call `lam patreon`, `lam token`, `lam serve`, `lam actions`, or `lam undo`, and it does not pass a delete.

## Git

Bots and Grok Build commit and push only to `review`. Before committing, check `git branch --show-current` is `review`. Make small commits, one per section or change, with a conventional message. Never commit or push to `main` or `master`. Never merge, rebase, force-push, or delete branches. Doug merges and may cherry-pick. Read-only git (`status`, `diff`, `log`, `show`) is fine. Do not write under `.git\`. Never commit a cookie or any slice of one (see Secrets).
