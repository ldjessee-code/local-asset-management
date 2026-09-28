# Example configs

These files are **templates**. Copy one to `library.jsonc` in the program
folder (or any folder that is not a source and not the destination), then
edit the paths.

`library.jsonc` at the repo root is gitignored on purpose so personal
paths are not committed.

| File | Use |
|---|---|
| [e-test.jsonc](e-test.jsonc) | Two PNG folders on `E:` (`test_images`, `test_pics` → `test_out`) |
| [library.example.jsonc](library.example.jsonc) | Screenshots + Patreon/VTT packs (the original design case) |
| [photos.jsonc](photos.jsonc) | Camera dumps into a year/month tree |
| [spreadsheets.jsonc](spreadsheets.jsonc) | Office files grouped by extension |
| [file-action-plan.sample.json](file-action-plan.sample.json) | Sample `file-action-plan/v1` (fake `C:\Example\...` paths) |
| [file-action-results.sample.json](file-action-results.sample.json) | Sample dry-run results for that plan |

Or skip the copy: `lam serve` and fill in sources + output in the UI.
