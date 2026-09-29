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
| [file-action-plan.sample.json](file-action-plan.sample.json) | Sample `file-action-plan/v1`. Every path is under `C:\Example`, which the runner refuses |
| [file-action-results.sample.json](file-action-results.sample.json) | Sample dry-run results for that plan |
| [site-profiles.sample.json](site-profiles.sample.json) | Sample `lam-site-profiles/v1` (`patreon` plus a fake `example-wiki`) |
| [patreon-creators.sample.json](patreon-creators.sample.json) | Sample `patreon-creators/v1`. `staging_root` is under `C:\Example`, which `lam patreon sync` refuses |
| [patreon-drop-manifest.sample.json](patreon-drop-manifest.sample.json) | Sample `patreon-drop-manifest/v1` with fake posts (not a real drop) |

Or skip the copy: `lam serve` and fill in sources + output in the UI.
