# Token fetcher (`lam token`)

`lam token` keeps a **site session cookie** in a persistent Chromium profile
and hands the Cookie header to other tools (`lam patreon` next, or anything
that needs a logged-in HTTP session).

The operator logs in by hand once. The tool never types a password, never
solves captcha, and never prints a cookie value except into the secrets file
or User env var the operator asked for.

## One-time workflow (Patreon)

Install the optional extra, then log in once in a visible window:

```text
.venv\Scripts\python.exe -m pip install playwright
.venv\Scripts\python.exe -m playwright install chromium

lam token login patreon
```

Log in (password, 2FA, captcha are all yours). Close the window or press
Enter in the console. Then:

```text
lam token status patreon --check
lam token get patreon --set-user-env
```

`get` writes `%LOCALAPPDATA%\lam\secrets\patreon_cookie.txt` (one line: the
full Cookie header). `--set-user-env` also sets `PATREON_SESSION_COOKIE` as a
Windows User environment variable. New shells see it; the current one may
need a restart.

`lam patreon` (separate build) should call the library API instead of
copying a cookie by hand:

```python
from lam.token import get_cookie_header

header = get_cookie_header("patreon")  # Secret; use header.reveal() at the HTTP call
```

## Where files live

Defaults (outside the repo and outside Dropbox):

| What | Default |
|---|---|
| Browser profile | `%LOCALAPPDATA%\lam\browser-profiles\<site_id>\` |
| Secrets file | `%LOCALAPPDATA%\lam\secrets\<site_id>_cookie.txt` |
| Cookie metadata (names, expiry, no values) | `%LOCALAPPDATA%\lam\secrets\<site_id>_meta.json` |

A site profile may override `profile_dir` or `secrets_file`. Any path inside
the repo working tree, at or under the Dropbox root (`F:\Dropbox`, or
`LAM_DROPBOX_ROOT`), or at or under `C:\Example` is refused (exit 2). Sibling
names such as `F:\DropboxOld` are allowed (whole-component match).

The secrets file is written via a temp file in the same directory, then
`os.replace`. Replacing the site's own secrets file is the one allowed
replace. An unrelated existing file is refused. Profiles are never deleted.

Optional site-profiles JSON: `--profiles PATH` or `LAM_SITE_PROFILES`. With
neither, a built-in `patreon` profile is used.

## Commands

| Command | Browser | Network | Writes |
|---|---|---|---|
| `lam token login <site>` | Visible Chromium, persistent profile | Only what you do in the window | Creates the profile directory |
| `lam token get <site>` | Headless, reads cookies | No | Secrets file (and User env with `--set-user-env`) |
| `lam token status [<site>]` | No | No | No |
| `lam token status [<site>] --check` | Headless, to build the Cookie header | One who-am-I GET (at most 2 retries) | Updates metadata `last_check` |

`get` and `status` print only: cookie **names**, count, total header length,
earliest expiry (ISO with offset, or `session`), whether required cookies are
present. They never print values.

## Site-profiles format (`lam-site-profiles/v1`)

Schema file: `lam/schemas/site-profiles.v1.schema.json`.
Registry: `lam/schemas/registry.py` (status `supported` / `deprecated` /
`removed`, same policy as file-action plans). Unknown or removed ids fail
with exit 2 before any work and list supported (and deprecated) ids.
A deprecated id runs with a warning.

Sample: [examples/site-profiles.sample.json](../examples/site-profiles.sample.json)
(`patreon` plus a fake `example-wiki` for tests).

| Field | Required | Meaning |
|---|---|---|
| `schema` | yes | `lam-site-profiles/v1` |
| `sites` | yes | Array of site objects |
| `sites[].id` | yes | Lower-case id (`patreon`) |
| `sites[].login_url` | yes | Opened by `login` |
| `sites[].cookie_domains` | yes | Domains whose cookies are eligible (e.g. `.patreon.com`) |
| `sites[].display_name` | no | Human label |
| `sites[].required_cookies` | no | Must be present and unexpired (`session_id` for Patreon) |
| `sites[].header_cookies` | no | `"all"` (default) or an explicit name list. Patreon is `"all"` because [patreon-dl wants the full Cookie header](https://github.com/patrickkfkan/patreon-dl/wiki/How-to-obtain-Cookie). |
| `sites[].validate` | no | `{url, expect_json_path, expect_status}` for `status --check` |
| `sites[].env_var` | no | User env var name for `--set-user-env` |
| `sites[].secrets_file` | no | Override secrets path |
| `sites[].profile_dir` | no | Override profile directory |
| `sites[].enabled` | no | Default true. `false`: skipped by `status`, refused by `login`/`get` |

Duplicate site ids are rejected.

## Exit codes

Shared with `lam patreon`. Each failure prints one line starting `GATE <kind>:`.

| Code | When | Typical `GATE` kind |
|---|---|---|
| 0 | Ok | — |
| 1 | Partial / unexpected item failure | `error` |
| 2 | Usage, schema, or refused path | `schema`, `usage`, `path` |
| 3 | Auth missing or expired (no profile, required cookie missing/expired, HTTP 401/403) | `auth` |
| 4 | Captcha or Cloudflare challenge | `challenge` |
| 5 | 2FA / verification page | `challenge` |
| 6 | HTTP 429 | `rate` |
| 7 | Network (DNS, timeout, connection) | `network` |
| 8 | Playwright not installed | `deps` |
| 9 | Path/drive unavailable | `drive` |

Example gate texts:

```text
GATE deps: playwright not installed - run: .venv\Scripts\python.exe -m pip install playwright; .venv\Scripts\python.exe -m playwright install chromium
GATE auth: no saved login for patreon - run: lam token login patreon
GATE auth: required cookie session_id missing for patreon
GATE auth: required cookie session_id expired for patreon
GATE path: secrets path is under Dropbox: ...
GATE schema: unsupported site-profiles schema lam-site-profiles/v9; supported: lam-site-profiles/v1
GATE challenge: captcha or Cloudflare challenge
GATE challenge: 2FA or verification required
GATE rate: HTTP 429
```

## Library API (for `lam patreon`)

```python
from lam.token import (
    get_cookie_header,
    login,
    site_status,
    Secret,
    TokenAuthError,
    TokenChallengeError,
    TokenVerificationError,
    TokenRateLimitError,
    TokenNetworkError,
    TokenDependencyError,
    TokenUsageError,
    TokenDriveError,
)

header = get_cookie_header("patreon")  # -> Secret
raw = header.reveal()                  # full Cookie header, e.g. "session_id=...; other=..."
```

`get_cookie_header(site: str, *, write_secrets: bool = False, set_user_env: bool = False) -> Secret`

- Default does **not** write the secrets file (the CLI `get` passes `write_secrets=True`).
- `str(header)` and `repr(header)` are always `<redacted>` / `Secret(<redacted>)`.
- Typed exceptions carry `.exit_code` matching the table above.

`login(site)` opens the visible window. `site_status(site=None, *, check=False)`
returns metadata dicts (no cookie values).

## Optional Playwright extra

Playwright is not a core dependency. Import happens only inside
`lam.token.browser` functions.

```text
pip install -e ".[token]"
.venv\Scripts\python.exe -m playwright install chromium
```

Or the two commands in the `GATE deps:` line. `lam token status` (offline)
works without Playwright.

## Security

- This is **your** session on **your** machine. Do not send the secrets file
  or the Cookie header to anyone.
- The tool never types credentials, never fills forms, and never solves
  captcha or Cloudflare challenges.
- Cookie values are not printed, logged, put in exception text, reports,
  manifests, or subprocess command lines.
- Profiles and secrets stay outside the git repo and outside Dropbox.
- `--set-user-env` is explicit. Tests inject a fake setter; they never write
  the real registry.
