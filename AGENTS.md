# Notes for coding agents

This repo is a **local file librarian**. The Python package `lam` is the
product. The browser is a remote control, not the hasher.

## Do / do not

- **Do** run `pytest` after engine changes (`pip install -e ".[dev]"` once).
- **Do** keep first-run as `start.cmd` / `start.sh` / `start.py` (venv + install + `lam`).
- **Do** keep `library.jsonc` gitignored; put templates in `examples/`.
- **Do** keep tests on `tmp_path` fixtures. Do not scan or write the operator’s
  real disks unless they name those paths and approve the write.
- **Do not** add delete, pHash, embeddings, or VTT-specific logic in v0.1.
- **Do not** put hashing or copies in JavaScript/WASM.

## Safety (writes)

- `scan` / `report` / `plan` do not change source files.
- `apply` copies (or hardlinks) only after **explicit user approval**
  (`lam apply --yes` or the UI confirm checkbox). No silent writes.
- `apply.never_modify_sources` defaults to true.
- Optional `protect_paths` in the config lists extra drives or folders that
  must not be written to. The configured `library_root` and `quarantine` stay
  the allowed write targets.

## Where truth lives

| Question | Read |
|---|---|
| What can it do? | `lam/capabilities.py` (also `lam capabilities --json`) |
| Public Python API | `lam/__init__.py` (`__all__`) |
| Design intent | `library-index-spec.md` |
| How the pieces fit | `docs/architecture.md` |
| Human install / CLI | `README.md` |
| License | `LICENSE` (AGPL-3.0-or-later, unmodified FSF text) |

## Pipeline

`scan` (read-only index) → `report` → `plan` (dest paths only) → `apply`
(copy/hardlink, needs confirm). No delete.

## Config

Default file: `library.jsonc` in cwd. Must not sit inside a source or the
destination tree (`lam.config.validate_config`). Setup UI can write it.
