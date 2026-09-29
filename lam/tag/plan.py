# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn ``lam-tags/v1`` into a ``file-action-plan/v1`` plus a review list.

A batch moves only when every member is high-confidence for the same target.
Otherwise the whole batch is reviewed and left in place.

Copy groups: the keeper is the shortest absolute path, then the oldest mtime,
then the path text. Extras are recycled only when that keeper is actually
moved. Destination collisions and existing destinations are reviewed.
This function never moves, renames, or deletes a file.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from lam.actions import validate_plan_document
from lam.schemas.registry import PLAN_SCHEMA_ID, TAG_REVIEW_SCHEMA_ID, TAG_REVIEW_SCHEMAS, TAGS_SCHEMAS
from lam.tag.bins import LEVEL1_BINS
from lam.tag.errors import TagError
from lam.tag.io import load_json_file, local_iso_now, require_known_schema, write_json_no_clobber
from lam.tag.subbins import load_subbins


def review_path_for(plan_path: Path) -> Path:
    return plan_path.with_name(plan_path.stem + ".review.json")


def _under_git(path: str) -> bool:
    return any(part.lower() == ".git" for part in Path(path).parts)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _is_under(path: str, root: Path) -> bool:
    child = _norm(path)
    base = _norm(str(root))
    return child == base or child.startswith(base + os.sep)


def _keeper_key(item: dict[str, Any]) -> tuple:
    return (len(item["abs"]), datetime.fromisoformat(item["mtime"]), item["abs"].casefold())


def _single_reason(item: dict[str, Any], detail: str) -> str:
    if _under_git(item["abs"]) or _under_git(item.get("rel") or ""):
        return "refused: path under .git"
    if item.get("confidence") == "low":
        return "low confidence"
    if item.get("confidence") == "unsure":
        return "unsure"
    return detail or "not high confidence"


def run_tag_plan(
    tags_path: Path | str,
    *,
    dest: Path | str,
    level: int,
    out: Path | str,
    bin_name: str | None = None,
    subbins_path: Path | str | None = None,
) -> dict[str, Any]:
    data = load_json_file(Path(tags_path))
    require_known_schema(data, TAGS_SCHEMAS, "tags")
    if level not in (1, 2):
        raise TagError("level must be 1 or 2")
    if int(data.get("level") or 0) != level:
        raise TagError(f"tags level {data.get('level')} does not match --level {level}")
    allowed: set[str] = set()
    if level == 2:
        if not bin_name or subbins_path is None:
            raise TagError("level 2 plan requires --bin and --subbins")
        if bin_name not in LEVEL1_BINS:
            raise TagError(f"unknown bin {bin_name}")
        file_bin, subbins = load_subbins(Path(subbins_path))
        if file_bin != bin_name:
            raise TagError(f"subbins bin {file_bin} does not match --bin {bin_name}")
        if data.get("bin") != bin_name:
            raise TagError(f"tags bin {data.get('bin')!r} does not match --bin {bin_name}")
        allowed = {sub.name for sub in subbins}
    destination = Path(dest)
    files: list[dict[str, Any]] = list(data.get("files") or [])

    def target_of(item: dict[str, Any]) -> str | None:
        if level == 1:
            return item.get("bin")
        return item.get("subbin")

    def eligibility(item: dict[str, Any]) -> tuple[bool, str]:
        if _under_git(item.get("abs") or "") or _under_git(item.get("rel") or ""):
            return False, "refused: path under .git"
        if level == 2 and not _is_under(item["abs"], destination / str(bin_name)):
            return False, "not under destination bin"
        if item.get("confidence") != "high":
            return False, _single_reason(item, "not high confidence")
        if level == 1:
            if item.get("bin") not in LEVEL1_BINS:
                return False, "unknown bin"
            return True, ""
        sub = item.get("subbin")
        if not sub or sub not in allowed:
            return False, "subbin not in list"
        return True, ""

    batches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in files:
        batches[str(item.get("batch") or "")].append(item)

    approved: set[str] = set()
    held: dict[str, str] = {}
    batch_reason = "batch held: not all members are high-confidence for the same bin"
    for batch, members in batches.items():
        flags = [eligibility(member) for member in members]
        if all(ok for ok, _detail in flags):
            targets = {target_of(member) for member in members}
            if len(targets) == 1 and None not in targets:
                approved.add(batch)
                continue
        if len(members) == 1 and not flags[0][0]:
            held[batch] = _single_reason(members[0], flags[0][1])
        else:
            held[batch] = batch_reason

    keepers: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in files:
        copy_id = item.get("copy_group")
        if copy_id:
            grouped[str(copy_id)].append(item)
    for copy_id, members in grouped.items():
        keepers[copy_id] = min(members, key=_keeper_key)

    proposed: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for item in files:
        rel = item["rel"]
        batch = str(item.get("batch") or "")
        if batch not in approved:
            proposed[rel] = "review"
            reasons[rel] = held.get(batch, batch_reason)
            continue
        copy_id = item.get("copy_group")
        if copy_id:
            keeper = keepers[str(copy_id)]
            if keeper["rel"] != rel:
                keeper_batch = str(keeper.get("batch") or "")
                if keeper_batch in approved and eligibility(keeper)[0]:
                    proposed[rel] = "recycle"
                else:
                    proposed[rel] = "review"
                    reasons[rel] = "extra copy; original was not moved"
                continue
        proposed[rel] = "move"

    def dest_for(item: dict[str, Any]) -> Path:
        name = Path(item["abs"]).name
        if level == 1:
            return destination / str(item["bin"]) / name
        return destination / str(bin_name) / str(item["subbin"]) / name

    by_dest: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in files:
        if proposed.get(item["rel"]) != "move":
            continue
        target = dest_for(item)
        if _norm(item["abs"]) == _norm(str(target)):
            proposed[item["rel"]] = "review"
            reasons[item["rel"]] = "already in place"
            continue
        by_dest[_norm(str(target))].append(item)
    for key, group in by_dest.items():
        exists = Path(group[0] and dest_for(group[0])).exists()
        if len(group) == 1 and not exists:
            continue
        for item in group:
            proposed[item["rel"]] = "review"
            if exists:
                reasons[item["rel"]] = "destination exists"
            else:
                reasons[item["rel"]] = "destination collision in plan"

    moving = {item["rel"] for item in files if proposed.get(item["rel"]) == "move"}
    for item in files:
        if proposed.get(item["rel"]) != "recycle":
            continue
        copy_id = str(item.get("copy_group") or "")
        keeper = keepers.get(copy_id)
        if keeper is None or keeper["rel"] not in moving:
            proposed[item["rel"]] = "review"
            reasons[item["rel"]] = "extra copy; original was not moved"

    moves = [item for item in files if proposed.get(item["rel"]) == "move"]
    recycles = [item for item in files if proposed.get(item["rel"]) == "recycle"]
    actions: list[dict[str, Any]] = []
    if moves:
        mkdir_paths = [destination]
        if level == 1:
            for bin_id in sorted({str(item["bin"]) for item in moves}):
                mkdir_paths.append(destination / bin_id)
        else:
            mkdir_paths.append(destination / str(bin_name))
            for sub in sorted({str(item["subbin"]) for item in moves}):
                mkdir_paths.append(destination / str(bin_name) / sub)
        seen_dirs: set[str] = set()
        mkdir_index = 0
        for directory in mkdir_paths:
            key = _norm(str(directory))
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            mkdir_index += 1
            actions.append(
                {
                    "id": f"mkdir-{mkdir_index:04d}",
                    "op": "mkdir",
                    "dst": str(Path(os.path.abspath(directory))),
                    "note": f"lam tag level {level}",
                }
            )
    for index, item in enumerate(sorted(moves, key=lambda row: row["rel"]), start=1):
        actions.append(
            {
                "id": f"move-{index:04d}",
                "op": "move",
                "src": item["abs"],
                "dst": str(Path(os.path.abspath(dest_for(item)))),
                "sha256": item["sha256"],
                "note": f"lam tag level {level} {target_of(item)}",
            }
        )
    for index, item in enumerate(sorted(recycles, key=lambda row: row["rel"]), start=1):
        keeper = keepers.get(str(item.get("copy_group") or ""))
        keeper_rel = keeper["rel"] if keeper else ""
        actions.append(
            {
                "id": f"recycle-{index:04d}",
                "op": "recycle",
                "src": item["abs"],
                "sha256": item["sha256"],
                "note": f"extra copy; keeper is {keeper_rel}",
            }
        )

    created = local_iso_now()
    plan = {
        "schema": PLAN_SCHEMA_ID,
        "created": created,
        "created_by": "lam tag",
        "note": (
            f"Level {level} reviewable plan from lam tag. "
            "Dry-run with lam actions run. Nothing moves until --apply."
        ),
        "actions": actions,
    }
    errors = validate_plan_document(plan)
    if errors:
        raise TagError("generated plan failed validation:\n  " + "\n  ".join(errors))
    review_items = []
    for item in sorted(files, key=lambda row: row["rel"]):
        if proposed.get(item["rel"]) != "review":
            continue
        review_items.append(
            {
                "rel": item["rel"],
                "abs": item["abs"],
                "bin": item.get("bin"),
                "subbin": item.get("subbin"),
                "confidence": item.get("confidence"),
                "reason": reasons.get(item["rel"], "review"),
                "batch": item.get("batch"),
                "copy_group": item.get("copy_group"),
            }
        )
    out_path = Path(out)
    review = {
        "schema": TAG_REVIEW_SCHEMA_ID,
        "created": created,
        "plan": str(out_path.resolve()),
        "level": level,
        "items": review_items,
    }
    require_known_schema(review, TAG_REVIEW_SCHEMAS, "tag-review")
    write_json_no_clobber(out_path, plan)
    write_json_no_clobber(review_path_for(out_path), review)
    return {"plan": plan, "review": review, "plan_path": out_path, "review_path": review_path_for(out_path)}
