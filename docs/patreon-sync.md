# Patreon sync (`lam patreon`)

Stages map packs Doug already pays for. The downloader stops at an inbox
plus a manifest. notLib expands the zips and files them under
`/Gaming/Icons_Maps_Symbols/<Creator>/MapNN_Name/` after a ping. This
command never expands a zip and never writes into Dropbox `/Gaming`.

It uses only the logged-in session. Locked posts are reported and skipped.
Links to Dropbox, Google Drive, Mega, MediaFire, and other outside hosts
are recorded as `deferred` and are not fetched.

The posts list leaves `content` null and puts the body in
`content_json_string` (link marks). Those hrefs are read the same way as
HTML. A Patreon or patreonusercontent URL with a known file extension is a
file. A `post_file` whose URL path is an image is the cover and is not
saved under the bare name `file`. An image on `attachments` or
`attachments_media` is an attachment file (`source_kind` `attachment`),
including a jpg or png map. It is not a preview. Preview images are the
`images` relationship, inline media, and that image cover. A non-image
file on the `media` relationship is staged when it has a download URL
and a known type. A
viewable post that the list parses with no attachment, no image, and no
outside link is fetched once from `/api/posts/<id>` with the same include
list. The single-post `data` object is one post.

## Commands

```text
lam patreon validate-config CREATORS.json
lam patreon list --creators CREATORS.json --last-months 12
lam patreon list --creators CREATORS.json --fixture-dir tests\fixtures\patreon --last-months 12
lam patreon sync --creators CREATORS.json
lam patreon sync --creators CREATORS.json --apply
```

`sync` is a dry run unless `--apply` is passed. A dry run creates no
staging folders. It writes a manifest only when `--results` is given
(`mode` is `dry-run`). `--apply` downloads into the inbox and writes
`drop-manifest_yyyyMMdd_HHmmss.json` there (a second run the same day gets
a new timestamp and does not overwrite the first file).

`list` is read-only. It writes nothing except an optional `--results`
file. `--fixture-dir` reads recorded JSON and does not open a socket or
need a cookie. `--since` is inclusive (local midnight). `--until` is
exclusive. The posts feed is newest first. A post at or after `--until`
is skipped and listing continues. Listing stops at the first post older
than `--since` (or older than `published_after`, when that filter is set).
`--last-months N` starts on the same calendar day N months
earlier at 00:00 local, clamping short months (31 March back one month is
28 February). The default zone is `America/Indianapolis` (override with
`LAM_PATREON_TZ`). Tests can freeze the clock with `LAM_PATREON_NOW`.

Do not pass `--since` and `--last-months` together.

## First live run

1. `lam token login patreon` (once, in the browser window that opens).
2. `lam patreon list --creators <your config> --last-months 12`
   for Kidney Boy (expect accessible posts) and Party of Two (expect a
   clean exit 0 if nothing is accessible on this tier).
3. `lam patreon sync --creators <your config>` and read the dry-run report
   before any `--apply`.

The sample config `examples/patreon-creators.sample.json` uses
`C:\Example\PatreonDL`. `--apply` on that file is refused before any
request. Keep the real config outside the repo (or under the gitignored
name `patreon-creators.json`) and point `staging_root` at `F:\PatreonDL`.

## Cookie

Source order, first hit wins. The run prints the source name only.

1. `lam.token.get_cookie_header("patreon")` (persistent profile from `lam token`).
2. Environment variable `LAM_PATREON_COOKIE`.
3. Environment variable `PATREON_SESSION_COOKIE`.
4. `LAM_PATREON_COOKIE_FILE`, or else
   `%LOCALAPPDATA%\lam\secrets\patreon_cookie.txt` (the file `lam token get` writes).

A cookie file inside this repo, under the Dropbox root, or under
`C:\Example` is refused. The value is trimmed, wrapping quotes are removed,
and a leading `Cookie:` is stripped. A newline is rejected. A value with no
`=` is treated as a bare `session_id` and prefixed, with a warning that the
full Cookie header is preferred.

How to copy a header by hand: while logged in, open dev tools, filter
network requests for `posts`, and copy the entire `Cookie` request header.
Patreon and the [patreon-dl cookie wiki](https://github.com/patrickkfkan/patreon-dl/wiki/How-to-obtain-Cookie)
both say to copy the full value and not to share it. Prefer `lam token login`
so the value is not pasted onto a command line.

The cookie is never printed, logged, written into the manifest, index, or
results file, or placed on a subprocess command line.

## What gets staged

```text
F:\PatreonDL\<Creator>\_inbox_yyyyMMdd\<post_id>_<sanitized title>\<file>
F:\PatreonDL\<Creator>\_index\patreon-index.json
```

Zips stay zipped. File names are sanitized for Windows (reserved device
names, trailing dots and spaces, length). The index remembers a media id
and a sha256. A later run skips a known media id without downloading. The
same bytes under a new id are `skipped-duplicate`. A name collision with
different bytes gets a `__<sha8>` suffix. Nothing is overwritten.

A `.part` file this command created is sent to the Recycle Bin through
`lam.actions.recycle_path`, or left in place and named in the manifest
warnings. It is not permanently deleted.

Outside hosts, DRM video, files over `max_file_mb`, and posts with no
downloadable file are `deferred`. Locked posts are `locked` (not a
failure). Chronos Builder titles match `(?i)chronos\s*builder` by default
and show up as `excluded (chronos)`.

`ready_summary` is one line notLib can key off, for example:
`Kidney_Boy: 2 posts, 6 files (5 zip, 1 image), 412 MB staged in F:\PatreonDL\Kidney_Boy\_inbox_20261001 - ready for notLib expand`.

Post id and the exact post title are kept. Map numbers are not invented.

A creator with `enabled: false` is skipped. A creator the viewer does not
belong to is a warning (`not a member`) and exit 0, not a gate. Zero posts
in the window, or posts that are all locked for this tier, are also exit 0.

## JSON formats

Registry: `lam/schemas/registry.py`. Versioning is the same policy as
[file-action-plan.md](file-action-plan.md): a new version is a new schema
file plus a registry entry (`supported`), then `deprecated` (runs with a
warning), then `removed`. Unknown and removed ids fail before any work
with exit 2 and list the supported and deprecated ids.

| Id | File | Role |
|---|---|---|
| `patreon-creators/v1` | `lam/schemas/patreon-creators.v1.schema.json` | Config: `staging_root`, `creators[]` (`slug`, `display_name`, `patreon_url`, optional `campaign_id`, `staging_folder`, `enabled`, `filters`) |
| `patreon-drop-manifest/v1` | `lam/schemas/patreon-drop-manifest.v1.schema.json` | One drop. Posts keep `post_id`, `post_title`, `post_url`, `published`, `status`, `reason`, and `files[]` |
| `patreon-index/v1` | `lam/schemas/patreon-index.v1.schema.json` | Per-creator dedupe index (media id, sha256, post status) |
| `patreon-post-list/v1` | `lam/schemas/patreon-post-list.v1.schema.json` | JSON from `lam patreon list --format json` or `--results` |

Samples: `examples/patreon-creators.sample.json`,
`examples/patreon-drop-manifest.sample.json`.

Manifest `status` is `downloaded`, `deferred`, `failed`, `skipped-duplicate`,
`locked`, `excluded`, or `planned` (dry run). File `kind` is `zip`,
`loose-media`, or `other`. The index is written to a `.lam-tmp-...` file
and then renamed over `patreon-index.json` (never truncated in place).

`list` text, one creator:

```text
Kidney_Boy (kidneyboy) - posts 2025-09-28..2026-09-28 (America/Indianapolis)
2026-09-18  accessible  Harbor Keep  1 attachments / 0 images / outside links: none  https://example.invalid/posts/kb-181
Kidney_Boy: 3 accessible, 1 locked, 1 excluded, 0 deferred (5 listed)
```

A creator with nothing accessible still exits 0:

```text
Party_of_Two: 0 accessible posts in window (2 listed, 2 locked for your tier)
Empty_One: 0 posts in window
```

## Exit codes

Numbers match `lam token`. The verification kind string here is
`verification` (exit 5). `lam token` uses the kind string `challenge` for
that same number.

| Code | Meaning |
|---|---|
| 0 | Ok, including "nothing accessible" |
| 1 | Item failure (a bad next link, or a file that failed to download). The manifest still records the post |
| 2 | Schema or usage (bad config, refused path, bad date flags) |
| 3 | Auth missing, expired, or not logged in |
| 4 | Captcha or Cloudflare challenge |
| 5 | 2FA or verification required |
| 6 | Rate limited (HTTP 429) |
| 7 | Network (DNS, timeout, connection) |
| 8 | Dependency missing, or the `patreon-dl` backend was requested (not enabled in v1) |
| 9 | Staging drive is not mounted |

Each gate prints one line and, when a manifest or `--results` file is in
progress, records `gate.exit_code` and `gate.line` plus the posts already
collected. A gate on one creator stops the run.

```text
GATE auth: Patreon session expired - run: lam token login patreon (or refresh PATREON_SESSION_COOKIE) and rerun (no files written)
GATE challenge: captcha or Cloudflare challenge - rerun after it clears (no files written)
GATE verification: 2FA or verification required - finish it in the browser and rerun (no files written)
GATE rate: HTTP 429 - wait and rerun (no files written)
GATE network: Patreon request failed - check the connection and rerun (no files written)
GATE deps: patreon-dl backend needs Node and patreon-dl on PATH (no install attempted)
GATE deps: patreon-dl subprocess backend is not enabled in v1 (http is the default; no files written)
GATE drive: staging root is unavailable - mount the drive and rerun (no files written)
```

Path and schema failures use `GATE path:` or `GATE schema:` and exit 2.
Those are printed before any Patreon request. Staging under
`F:\Dropbox\Gaming`, anywhere under the Dropbox root, or under `C:\Example`
is refused. `F:\DropboxOld` is not treated as Dropbox.

Requests are sequential, one creator at a time, with a minimum delay
(0.4s, or 0.25s in the rate-limit tests). HTTP 429 honors `Retry-After`
up to 5 seconds, at most two retries (three attempts total), then exit 6.

## Backend

The default backend is a small Python client (`HttpPatreonSource`) against
the same logged-in web JSON the site uses (`/api/current_user`, campaigns,
`/api/posts`), with a browser-like User-Agent. `whoami` runs before any
listing. `--backend patreon-dl` does not launch Node; it exits 8. Official
OAuth API v2 is not used: listing posts requires the `campaigns.posts`
scope and the campaigns list is campaigns the token owns, which does not
cover a patron reading another creator's attachments.

## Boundaries

- No live call is made by the test suite or by `--fixture-dir`.
- No comments, likes, messages, or mail.
- No mirror or archive sites.
- No paywall bypass. If `current_user_can_view` is false, the media URL is
  not requested.
