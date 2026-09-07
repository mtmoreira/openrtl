"""Immutable import provenance, reviewed baseline and exact change scope contracts."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from openrtl.domain.design_session import (
    JsonObject, MAX_CONTEXT_BYTES, STAGES, canonical, content_digest, object_value,
    require, sequence, source_path, text, validate_manifest, validate_spec,
)


def digest_value(value: object) -> str:
    result = text(value, maximum=71)
    require(re.fullmatch(r"sha256:[a-f0-9]{64}", result) is not None, "import_digest_invalid")
    return result


def import_source(value: object) -> str:
    result = text(value, maximum=240)
    path = PurePosixPath(result)
    require(not path.is_absolute() and path.as_posix() == result and "\\" not in result and
            all(p not in ("", ".", "..") and not p.startswith(".") for p in result.split("/")),
            "import_source_path_invalid")
    require(path.suffix in (".sv", ".svh", ".v", ".vh", ".py", ".md", ".txt", ".json"), "import_source_extension_invalid")
    denied = ("credential", "credentials", "secret", "secrets", "token", "tokens", "cookies", "api_key", "private_key")
    require(all(not any(p.lower() == key or p.lower().startswith(key + ".") for key in denied) for p in path.parts),
            "sensitive_import_name_rejected")
    return result


def validate_import_plan(value: object) -> JsonObject:
    plan = object_value(value, {"schema", "files"})
    require(plan["schema"] == "openrtl.design-import.v1", "import_schema_invalid")
    rows = sequence(plan["files"], maximum=64)
    require(bool(rows), "import_files_missing")
    seen: set[str] = set()
    for item in rows:
        row = object_value(item, {"source", "target", "digest"})
        source = import_source(row["source"])
        target = source_path(row["target"])
        require(PurePosixPath(source).suffix == PurePosixPath(target).suffix and target not in seen,
                "import_target_duplicate_or_extension_changed")
        digest_value(row["digest"])
        seen.add(target)
    return plan


def baseline_plan(spec: object, imports: JsonObject, manifest: object) -> JsonObject:
    selected = validate_spec(spec)
    require(not selected["questions"] and bool(selected["ports"]), "baseline_spec_incomplete")
    files = {p: row["digest"] for p, row in imports.items()}
    chosen = validate_manifest(manifest, files, selected)
    require(any(p.startswith("model/test_") for p in files), "baseline_model_tests_missing")
    return {"schema": "openrtl.design-baseline.v1", "specification": selected, "files": files,
            "manifest": chosen, "evidence": "unverified_requires_fresh_simulation"}


def validate_change_plan(value: object) -> JsonObject:
    plan = object_value(value, {"schema", "base_input_digest", "base_files", "specification", "stage_paths", "manifest"})
    require(plan["schema"] == "openrtl.design-change.v1", "change_schema_invalid")
    digest_value(plan["base_input_digest"])
    spec = validate_spec(plan["specification"])
    require(not spec["questions"] and bool(spec["ports"]), "change_spec_incomplete")
    require(isinstance(plan["base_files"], dict) and 0 < len(plan["base_files"]) <= 128, "change_base_invalid")
    for path, digest in plan["base_files"].items():
        source_path(path)
        digest_value(digest)
    stages = object_value(plan["stage_paths"], set(STAGES))
    prefixes = {"architecture": "docs/", "verification_plan": "docs/", "reference_model": "model/",
                "rtl": "rtl/", "assertions": "rtl/", "dv": "dv/"}
    writes: set[str] = set()
    for stage in STAGES:
        for path in sequence(stages[stage]):
            source_path(path)
            require(path.startswith(prefixes[stage]) and path not in writes, "change_stage_ownership_invalid")
            writes.add(path)
    require(bool(writes) and len(writes | set(plan["base_files"])) <= 128, "change_scope_invalid")
    validate_manifest(plan["manifest"], {p: "" for p in writes | set(plan["base_files"])}, spec)
    require(len(canonical(plan)) <= MAX_CONTEXT_BYTES, "change_plan_too_large")
    return plan


def validate_import_state(state: JsonObject) -> None:
    imports = state["imports"]
    require(isinstance(imports, dict) and len(imports) <= 64, "session_imports_invalid")
    size = 0
    for path, value in imports.items():
        source_path(path)
        row = object_value(value, {"digest", "source", "bytes", "plan_digest"})
        digest_value(row["digest"])
        digest_value(row["plan_digest"])
        import_source(row["source"])
        require(type(row["bytes"]) is int and 0 < row["bytes"] <= 256 * 1024, "import_size_invalid")
        size += row["bytes"]
    require(size <= MAX_CONTEXT_BYTES, "imports_exceed_bound")
    if state["baseline"] is not None:
        selected = state["baseline"]
        require(isinstance(selected, dict) and selected == baseline_plan(selected.get("specification"), imports, selected.get("manifest")),
                "baseline_binding_invalid")
    if state["change_plan"] is not None:
        plan = validate_change_plan(state["change_plan"])
        require(state["approved_spec"] == content_digest(plan["specification"]), "change_spec_binding_invalid")
        writes = {p for paths in plan["stage_paths"].values() for p in paths}
        require(set(state["files"]).issubset(set(plan["base_files"]) | writes), "change_file_scope_exceeded")
        require(all(state["files"].get(p) == digest for p, digest in plan["base_files"].items() if p not in writes),
                "readonly_baseline_artifact_changed")
