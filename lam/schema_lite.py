# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small JSON Schema (draft 2020-12) subset. Stdlib only.

Enough for the file-action plan/results schemas: type, const, enum,
required, properties, additionalProperties, items, minLength, pattern,
minimum, allOf, if/then/else, $ref to local ``#/$defs/...``.
"""

from __future__ import annotations

import re
from typing import Any

Json = Any


def validate_json(instance: Json, schema: dict, *, root: dict | None = None, path: str = "$") -> list[str]:
    """Return human-readable error strings. Empty means the instance matches."""
    if root is None:
        root = schema
    errors: list[str] = []

    if "$ref" in schema:
        ref_schema = _resolve_ref(root, schema["$ref"])
        errors.extend(validate_json(instance, ref_schema, root=root, path=path))
        # 2020-12 applies sibling keywords too; we only need $ref as the item schema.
        other = {k: v for k, v in schema.items() if k != "$ref"}
        if other:
            errors.extend(validate_json(instance, other, root=root, path=path))
        return errors

    if "if" in schema:
        if_errs = validate_json(instance, schema["if"], root=root, path=path)
        if not if_errs:
            if "then" in schema:
                errors.extend(validate_json(instance, schema["then"], root=root, path=path))
        elif "else" in schema:
            errors.extend(validate_json(instance, schema["else"], root=root, path=path))

    for sub in schema.get("allOf", ()):
        errors.extend(validate_json(instance, sub, root=root, path=path))

    if "anyOf" in schema:
        any_ok = False
        any_collected: list[str] = []
        for sub in schema["anyOf"]:
            sub_errs = validate_json(instance, sub, root=root, path=path)
            if not sub_errs:
                any_ok = True
                break
            any_collected.extend(sub_errs)
        if not any_ok:
            errors.append(f"{path}: no anyOf branch matched")

    if "oneOf" in schema:
        matches = 0
        for sub in schema["oneOf"]:
            if not validate_json(instance, sub, root=root, path=path):
                matches += 1
        if matches != 1:
            errors.append(f"{path}: expected exactly one oneOf match, got {matches}")

    declared = schema.get("type")
    if declared is not None:
        names = (declared,) if isinstance(declared, str) else tuple(declared)
        if not any(_type_matches(instance, name) for name in names):
            errors.append(f"{path}: expected type {declared!r}, got {_js_type(instance)}")
            return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']}")

    if isinstance(instance, dict):
        for key in schema.get("required", ()):
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        props = schema.get("properties") or {}
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child = f"{path}.{key}"
            if key in props:
                errors.extend(validate_json(value, props[key], root=root, path=child))
            elif additional is False:
                errors.append(f"{path}: additional property {key!r} is not allowed")
            elif isinstance(additional, dict):
                errors.extend(validate_json(value, additional, root=root, path=child))

    if isinstance(instance, list) and "items" in schema:
        item_schema = schema["items"]
        for i, item in enumerate(instance):
            errors.extend(validate_json(item, item_schema, root=root, path=f"{path}[{i}]"))
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: expected at least {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: expected at most {schema['maxItems']} items")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: does not match pattern {schema['pattern']}")

    if _js_type(instance) in {"integer", "number"}:
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} is below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} is above maximum {schema['maximum']}")

    return errors


def _resolve_ref(root: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"only local $ref values are supported: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"unresolved $ref {ref}")
        node = node[part]
    if not isinstance(node, dict):
        raise ValueError(f"$ref {ref} did not resolve to an object")
    return node


def _js_type(value: Json) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_matches(value: Json, declared: str) -> bool:
    got = _js_type(value)
    if declared == "number":
        return got in {"integer", "number"}
    if declared == "integer":
        return got == "integer"
    return got == declared
