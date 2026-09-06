# File library indexer — spec for Grok Build

Generic multi-source file librarian: inventory, exact-dedupe, archive pairing,
configurable physical layout, quarantine. Not tied to a VTT or photo app.

Primary use case on this machine: game screenshots + tabletop map/token packs
(often Patreon zips, used in Roll20). Same tool + a different config covers
spreadsheets, zip-only archives, or a photo library.

Status: design. Do not scan a real drive from the agent workspace.
Run the finished tool on the Windows box (8 TB library drive).

Supersedes `vtt-asset-index-spec.md`.

---

## What v1 is

- Many input roots, overlapping content
- JSONC config (JSON with `//` and `/* */` comments next to the keys)
- Read-only `scan` + `report` first
- Exact byte duplicates only
- Zip listing without extract (`zipfile`); nested zip listed, not exploded
- New library tree via **copy** (spacious) or later hardlink (tight)
- Quarantine extras; no delete in v1
- Layout is a named template per source, chosen in config — not inferred

What v1 is not: Foundry/Roll20-aware, K-Means folders, pHash, embeddings,
auto-tagging, RDF, library-science classification. Those are phase notes at
the end.

---

## Replicating fclones / Czkawka / rmlint in Python

You do not need Rust for the **algorithm**. Those tools share one pattern.
Python can do the same work. Rust wins on millions of files and nasty disks,
not on the idea.

### The pattern (all three, roughly)

1. Walk with `scandir` (not a slow `os.walk` callback soup). Record path, size, mtime, device, inode.
2. Group by **size**. A size that appears once cannot be a duplicate. Never read those files.
3. For each size-bucket with 2+ files: hash the **first 64 KB** (or 4 KB). Group by that prefix.
4. For prefix-buckets with 2+ files: hash the **whole file** (SHA-256 is fine; fclones uses a 128-bit hash for speed).
5. Emit groups. Cache `(dev, inode, size, mtime) -> hash` so the next scan is cheap.
6. Optional extras they also do, worth copying later:
   - skip hashing if two paths are already hardlinked (same dev+inode)
   - schedule reads per device so an HDD is not random-thrashed
   - thread pool for CPU hash, few concurrent readers per spinning disk

Czkawka’s *similar image* mode is a different product (perceptual hash).
Do not copy that into v1.

### Should we write it in Python anyway?

Yes, for this project.

- One language you already use.
- Personal library scale (hundreds of thousands of files, not tens of millions) is fine in Python if you do size-first and cache hashes in SQLite.
- No extra binary required.
- `fclones` remains an optional subprocess (`helpers.fclones: "auto"`) if a scan is too slow. Same output schema either way.

Do not try to translate fclones Rust into Python line-for-line. Reimplement the **pipeline**, not the crate.

`dedupe_trees` (Python, 2018, MIT) is the policy cousin for screenshots: several trees, choose a canonical copy by source priority, sequester the rest. Steal the idea. Do not depend on the old package.

---

## Layout: several choices now, clustering later

Physical folders must be **stable**. Adding one file should not rename a cluster
and reshuffle the tree. That is why K-Means is a future *view*, not a v1 dest path.

v1 `layout` names (config picks one per source):

| Name | Dest pattern | Good for |
|---|---|---|
| `keep_relpath` | `{library_root}/{source_name}/{relpath}` | “don’t surprise me” |
| `by_year` | `{library_root}/{group}/{year}/{stem}{ext}` | screenshots, camera dumps |
| `by_year_month` | `{library_root}/{group}/{year}/{month}/{stem}{ext}` | photo library later |
| `pack_tree` | `{library_root}/packs/{bundle}/{pack}/{relpath}` | artist/Patreon sets (VTT primary case) |
| `by_ext` | `{library_root}/{ext}/{stem}{ext}` | “all the zips”, spreadsheets |
| `sidecar_with_parent` | same dir as the paired file, or `{parent}/_notes/{relpath}` | readmes next to art |

`bundle` is just “first meaningful folder under that source” (artist name in the VTT case). It is a path token, not an ontology.

Several layouts can coexist in one library_root. A source points at one name.

**K-Means / library science:** out of scope for v1 research and code. Parking lot at the bottom. If we ever cluster, it writes suggested albums into the DB or a sidecar catalog, not into folder names.

---

## Config: JSON with comments (JSONC)

Comments live **in** the config, beside the key they describe. One file.
Standard `json` module will not parse that. v1 strips `//` and `/* */` then
`json.loads`. No PyYAML. Optional later: `json5` if we want trailing commas.

CLI:

```text
python library.py --config D:\tools\library.jsonc scan
python library.py --config D:\tools\library.jsonc report
python library.py --config D:\tools\library.jsonc plan
python library.py --config D:\tools\library.jsonc apply --yes
```

Also accept `.json` if it has no comments.

### Example config (primary use case: screenshots + tabletop packs)

```jsonc
{
  // spacious = copy into library, leave sources alone
  // tight    = hardlink on same NTFS volume when possible
  "profile": "spacious",

  "sources": [
    {
      "path": "D:/Screenshots",
      "layout": "by_year",     // date-style tree; good for game captures
      "priority": 10           // higher wins when two files share a hash
    },
    {
      "path": "D:/Patreon",
      "layout": "pack_tree",   // primary VTT/Roll20 pack layout
      "priority": 50
    },
    {
      "path": "D:/VTT/working",
      "layout": "pack_tree",
      "priority": 20
    },
    {
      "path": "D:/Downloads/art",
      "layout": "keep_relpath",
      "priority": 5
    }
  ],

  "quarantine": "D:/Library/_quarantine",
  "library_root": "D:/Library/media",
  "index_db": "D:/Library/index/library.sqlite",
  "log_dir": "D:/Library/logs",

  // groups are extension lists only — no “this is a token” inference
  "groups": {
    "images": [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"],
    "sidecars": [".txt", ".md", ".pdf", ".docx", ".nfo", ".json", ".html", ".url"],
    "archives": [".zip"],
    "archives_optional": [".7z", ".rar"]
  },

  "layout": {
    "keep_relpath": "{library_root}/{source_name}/{relpath}",
    "by_year": "{library_root}/screenshots/{year}/{stem}{ext}",
    "by_year_month": "{library_root}/photos/{year}/{month}/{stem}{ext}",
    "pack_tree": "{library_root}/packs/{bundle}/{pack}/{relpath}",
    "by_ext": "{library_root}/by-ext/{ext}/{stem}{ext}",
    "sidecar_with_parent": "{library_root}/packs/{bundle}/{pack}/_notes/{relpath}",
    "zip_store": "{library_root}/_zips/{bundle}/{zip_name}"
  },

  "exclude_dir_names": ["node_modules", ".git", "__MACOSX", ".DS_Store"],

  "zip": {
    "index_nested": true,      // list zip-in-zip; do not extract during scan
    "extract_on_apply": false,
    "max_depth": 2
  },

  "helpers": {
    "fclones": "auto"          // use CLI if on PATH; else Python pipeline
  },

  "apply": {
    "default_op": "copy",      // copy | hardlink | move
    "never_modify_sources": true,
    "quarantine_exact_dupes": true
  }
}
```

A spreadsheets-only config is the same file with different `groups` and `layout`.
A zip-hoard config would set `groups` to archives only and `layout` to `by_ext`.

`priority` is the only automatic “keep this copy” rule. Tie-break: deeper
relative path, then shorter path, then first seen. Reason goes in the plan row.

---

## Data model (SQLite)

`files` — path, source_id, size, mtime, ext, sha256 (nullable), group_name, bundle_id  
`archives` — path, parent_archive_id, size, sha256  
`archive_entries` — archive_id, inner_path, size, crc32, sha256 nullable  
`bundles` — inferred folder unit (artist/pack in the VTT case; generic “bundle/pack” in code)  
`sidecars` — path, parent_file_id or bundle_id  
`duplicate_groups` — hash, canonical_file_id, policy  
`hash_cache` — dev, inode, size, mtime, sha256  
`actions_log` — ts, command, dry_run, src, dest, op, reason

Hash pipeline: size → 64 KB prefix → full sha256, only for collisions.
No pHash in v1.

---

## Archive / moved-file cases

Record all four. Do not delete extras in v1.

| State | Apply when spacious |
|---|---|
| Zip only | copy zip to `zip_store` |
| Extracted tree only | copy tree via that source’s layout |
| Zip + matching extract | keep both; flag `pair_complete` |
| Loose file whose hash is inside a zip or pack tree | plan `belongs_to` that bundle; copy into dest tree; leave source |

Nested zip: list inner names/sizes/CRC with `zipfile`. Do not extract on scan.

`bundle` / `pack` names: first meaningful folder under the source that is not a
date dump. If unknown, `_unknown`. No scraping Patreon or Roll20.

**Archives in Python**

- v1 required: stdlib `zipfile` (and `tarfile` if we bother)
- optional: `py7zr` for `.7z`, `rarfile` + UnRAR/7-Zip for `.rar`
- missing optional codec → record the archive as an opaque file

---

## Sidecars (notes, PDFs, your markdown)

If a sidecar sits inside a bundle/pack (or inside that zip), it stays with that
tree (`sidecar_with_parent` or `{relpath}`). Same-stem `.txt` next to an image
stays next to it. Orphans use `keep_relpath` under `_unsorted`. No NLP.

---

## Reports a later Grok/GrokBot pass may read

`reports/summary.md` and `reports/exceptions.md` only.

- counts and bytes per source
- zip-only / extracted-only / paired
- exact duplicate groups and wasted bytes
- sidecars attached vs orphan
- hash seen both loose and inside a zip (moved-not-copied)
- files whose ext is in no group

Do not paste the SQLite file or a full path dump into a chat.

---

## Implementation notes for Grok Build

- Python 3.11+, stdlib: pathlib, hashlib, sqlite3, zipfile, argparse, json, csv, concurrent.futures
- JSONC: small strip-comments helper, then `json.loads`
- Windows paths stored normalized (`/`)
- Progress every N files + `logs/scan.jsonl`
- Tests: fixture with two sources, one zip, extracted twin, one moved file, one README

Milestone 1: `scan` + `report` + Python size/prefix/full hash pipeline.  
Milestone 2: `plan` / `apply` copy + quarantine.  
Do not implement clustering, pHash, or delete.

---

## Later phases (do not design further now)

Parked on purpose. Photo library is the likely home for most of these.
Tabletop packs rarely need them.

- Perceptual hash (pHash / dHash / aHash) for near-duplicate screenshots
- DCT / wavelet fingerprints as compact stand-ins (same family as pHash)
- Image metadata extract (EXIF, dimensions) — cheap, maybe milestone 3
- Vector embeddings + ANN search for “find maps that look like this”
- Auto tags from a local vision model
- Graph / RDF for provenance (“this token came from pack X used in campaign Y”)
- K-Means or similar on embeddings as a **suggested album view**, never as the
  only folder tree (clusters move when you add files)
- Library-science work: faceted catalog (creator, date, form, subject,
  provenance) as query axes on top of one boring physical tree; controlled
  vocabulary; classification schedules. Research later, not v1.
