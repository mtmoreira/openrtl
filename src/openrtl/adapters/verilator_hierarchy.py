"""Bounded instance index from Verilator 5.046 JSON AST output.

The adapter consumes compiler output; it never infers hierarchy from RTL text.
JSON shape is version-bound and fails closed when required links are absent.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re

from openrtl.domain.design_session import JsonObject, content_digest, require, source_path


MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_NODES = 100_000
MAX_INSTANCES = 4_096
MAX_DEPTH = 64
LOCATION = re.compile(r"^([^,]+),(\d+):(\d+),(\d+):(\d+)$")


def _nodes(value: object) -> list[JsonObject]:
    require(isinstance(value, dict), "hierarchy_tree_invalid")
    pending: list[tuple[object, int]] = [(value, 0)]
    found: list[JsonObject] = []
    while pending:
        item, depth = pending.pop()
        require(depth <= MAX_DEPTH and len(found) < MAX_NODES, "hierarchy_tree_too_large")
        if not isinstance(item, dict):
            continue
        if "type" in item:
            found.append(item)
        for child in item.values():
            if isinstance(child, list):
                pending.extend((row, depth + 1) for row in reversed(child) if isinstance(row, dict))
            elif isinstance(child, dict):
                pending.append((child, depth + 1))
    return found


def _source(node: JsonObject, meta: JsonObject, sources: dict[str, str],
            source_root: str) -> JsonObject | None:
    match = LOCATION.fullmatch(node.get("loc", "")) if isinstance(node.get("loc"), str) else None
    if match is None:
        return None
    file_row = meta.get("files", {}).get(match.group(1))
    if not isinstance(file_row, dict) or not isinstance(file_row.get("filename"), str):
        return None
    filename = file_row["filename"]
    prefix = source_root.rstrip("/") + "/"
    if filename.startswith(prefix):
        relative = filename[len(prefix):]
    elif not filename.startswith("/"):
        relative = filename
    else:
        return None
    try:
        path = source_path(relative)
    except ValueError:
        return None
    if path not in sources:
        return None
    line = int(match.group(2))
    require(line > 0, "hierarchy_source_line_invalid")
    return {"path": path, "line": line, "digest": sources[path]}


def parse_verilator_hierarchy(tree_bytes: bytes, meta_bytes: bytes, *,
                              revision: int, source_root: str,
                              sources: dict[str, str], top: str,
                              input_digest: str, tool_version: str,
                              options: list[str]) -> JsonObject:
    """Index one explicitly selected top from an exact compiler-produced AST."""
    require(type(revision) is int and revision >= 0, "hierarchy_revision_invalid")
    require(isinstance(tree_bytes, bytes) and isinstance(meta_bytes, bytes) and
            len(tree_bytes) <= MAX_JSON_BYTES and len(meta_bytes) <= MAX_JSON_BYTES,
            "hierarchy_output_too_large")
    require(tool_version == "5.046", "hierarchy_tool_version_unsupported")
    require(isinstance(top, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", top) is not None,
            "hierarchy_top_invalid")
    require(isinstance(source_root, str) and source_root.startswith("/") and
            ".." not in PurePosixPath(source_root).parts, "hierarchy_source_root_invalid")
    require(isinstance(sources, dict) and sources and
            all(source_path(path) == path and re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
                for path, digest in sources.items()), "hierarchy_sources_invalid")
    require(isinstance(input_digest, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", input_digest),
            "hierarchy_input_digest_invalid")
    require(isinstance(options, list) and len(options) <= 64 and
            all(isinstance(item, str) and len(item) <= 256 for item in options),
            "hierarchy_options_invalid")
    try:
        tree, meta = json.loads(tree_bytes), json.loads(meta_bytes)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("hierarchy_json_invalid") from None
    require(isinstance(meta, dict) and isinstance(meta.get("files"), dict), "hierarchy_metadata_invalid")
    found = _nodes(tree)
    modules = {row.get("addr"): row for row in found
               if row.get("type") == "MODULE" and isinstance(row.get("addr"), str)}
    named = [row for row in modules.values() if row.get("origName") == top or row.get("name") == top]
    require(len(named) == 1, "hierarchy_top_ambiguous_or_missing")
    top_node = named[0]
    cells: dict[str, list[JsonObject]] = {}
    for module in modules.values():
        descendants: list[JsonObject] = []
        pending: list[object] = list(reversed(
            [child for value in module.values() if isinstance(value, list) for child in value]))
        while pending:
            item = pending.pop()
            if not isinstance(item, dict):
                continue
            if item.get("type") == "MODULE":
                continue
            if item.get("type") == "CELL":
                descendants.append(item)
            for value in item.values():
                if isinstance(value, list):
                    pending.extend(reversed(value))
        cells[module["addr"]] = descendants

    instances: list[JsonObject] = []
    def visit(module: JsonObject, identifier: str, parent: str | None,
              cell: JsonObject | None, stack: tuple[str, ...]) -> None:
        require(len(instances) < MAX_INSTANCES and len(stack) < MAX_DEPTH,
                "hierarchy_instances_too_large")
        address = module["addr"]
        require(address not in stack, "hierarchy_recursive_module_unsupported")
        definition = _source(module, meta, sources, source_root)
        require(definition is not None, "hierarchy_module_source_unavailable")
        instantiation = _source(cell, meta, sources, source_root) if cell is not None else definition
        require(instantiation is not None, "hierarchy_cell_source_unavailable")
        instances.append({"id": identifier, "parent": parent,
                          "module": module.get("origName") or module.get("name"),
                          "specialization": module.get("name"),
                          "definition": {"revision": revision, **definition},
                          "instantiation": {"revision": revision, **instantiation}})
        for child in cells[address]:
            require(isinstance(child.get("modp"), str), "hierarchy_cell_target_unavailable")
            target = modules.get(child["modp"])
            require(target is not None, "hierarchy_cell_target_unavailable")
            raw = child.get("name") or child.get("origName")
            require(isinstance(raw, str) and raw, "hierarchy_cell_name_invalid")
            child_id = raw if raw.startswith(identifier + ".") else identifier + "." + raw
            require(not any(row["id"] == child_id for row in instances), "hierarchy_instance_duplicate")
            visit(target, child_id, identifier, child, (*stack, address))

    visit(top_node, top, None, None, ())
    return {"schema": "openrtl.verilator-hierarchy.v1", "status": "elaborated",
            "tool": "verilator", "tool_version": tool_version,
            "revision": revision, "input_digest": input_digest, "top": top,
            "options_digest": content_digest(options),
            "tree_digest": "sha256:" + hashlib.sha256(tree_bytes).hexdigest(),
            "meta_digest": "sha256:" + hashlib.sha256(meta_bytes).hexdigest(),
            "instances": instances}
