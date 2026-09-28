"""Versioned, block-neutral contracts for an executing design conversation."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, cast

from openrtl.domain.provider_failures import EXPERT_OPERATION_ERROR_CODES, PROVIDER_FAILURE_CODES


JsonObject = dict[str, Any]
SESSION_SCHEMA = "openrtl.design-session.v6"
WEB_SESSION_SCHEMA = "openrtl.design-session.v5"
COACHING_SESSION_SCHEMA = "openrtl.design-session.v4"
IMPORT_SESSION_SCHEMA = "openrtl.design-session.v3"
PREVIOUS_SESSION_SCHEMA = "openrtl.design-session.v2"
LEGACY_SESSION_SCHEMA = "openrtl.design-session.v1"
STAGES = ("architecture", "verification_plan", "reference_model", "rtl", "assertions", "dv")
ROLES = {
    "discovery": "design_lead",
    "architecture": "design_architect",
    "verification_plan": "verification_architect",
    "reference_model": "reference_model_engineer",
    "rtl": "rtl_engineer",
    "assertions": "assertion_engineer",
    "dv": "dv_engineer",
    "diagnosis": "diagnosis_closure_engineer",
    "signoff": "signoff_reviewer",
    "explain": "learning_coach",
    "change_planning": "change_planning_engineer",
    "analyze": "diagnosis_reviewer",
}
MAX_ARTIFACT_BYTES = 256 * 1024
MAX_CONTEXT_BYTES = 512 * 1024


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def content_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def object_value(value: object, keys: set[str]) -> JsonObject:
    require(isinstance(value, dict) and set(value) == keys, "object_fields_invalid")
    return cast(JsonObject, value)


def text(value: object, *, maximum: int = MAX_ARTIFACT_BYTES) -> str:
    require(isinstance(value, str), "text_type_invalid")
    result = cast(str, value)
    require(bool(result.strip()) and len(result.encode("utf-8")) <= maximum, "text_size_invalid")
    require(all(ord(c) >= 32 or c in "\n\t" for c in result) and "\x7f" not in result,
            "text_control_character_invalid")
    return result


def name(value: object) -> str:
    result = text(value, maximum=128)
    require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", result) is not None, "identifier_invalid")
    return result


class SpecificationIdentifierError(ValueError):
    """A trusted specification location, never the rejected identifier value."""

    def __init__(self, location: Literal["top", "port", "parameter"], *,
                 index: int | None = None) -> None:
        require(location in ("top", "port", "parameter"), "identifier_diagnostic_invalid")
        require(index is None if location == "top" else
                type(index) is int and 0 <= index < 64, "identifier_diagnostic_invalid")
        super().__init__("identifier_invalid")
        self.field = ("specification.top" if location == "top" else
                      f"specification.ports[{index}].name" if location == "port" else
                      f"specification.hardware_specification.parameters[{index}].name")


def specification_name(value: object, location: Literal["top", "port", "parameter"], *,
                       index: int | None = None) -> str:
    """Attach a closed field location without changing identifier/text rules."""
    try:
        return name(value)
    except ValueError as error:
        if str(error) == "identifier_invalid":
            raise SpecificationIdentifierError(location, index=index) from None
        raise


def stable_id(value: object) -> str:
    result = text(value, maximum=128)
    require(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", result) is not None, "stable_id_invalid")
    return result


def source_path(value: object) -> str:
    result = text(value, maximum=240)
    require(re.fullmatch(r"(?:rtl|dv|model|docs)/[A-Za-z0-9_/.-]+", result) is not None,
            "source_path_invalid")
    require(all(part not in {"", ".", ".."} and not part.startswith(".")
                for part in result.split("/")), "source_path_invalid")
    suffix = result.rsplit(".", 1)[-1]
    prefix = result.split("/", 1)[0]
    allowed = {"rtl": {"sv", "svh", "v", "vh"}, "dv": {"py"}, "model": {"py"}, "docs": {"md", "txt", "json"}}
    require(suffix in allowed[prefix], "source_extension_invalid")
    return result


def sequence(value: object, *, maximum: int = 64) -> list[Any]:
    require(isinstance(value, list) and len(value) <= maximum, "list_invalid")
    return cast(list[Any], value)


def validate_spec(value: object, *, require_hardware_specification: bool = False) -> JsonObject:
    fields = {"title", "top", "behavior", "clock_reset", "requirements", "ports", "questions", "assumptions"}
    if isinstance(value, dict) and "readiness" in value:
        fields.add("readiness")
    if isinstance(value, dict) and "hardware_specification" in value:
        fields.add("hardware_specification")
    spec = object_value(value, fields)
    text(spec["title"], maximum=256)
    specification_name(spec["top"], "top")
    text(spec["behavior"], maximum=32000)
    text(spec["clock_reset"], maximum=8000)
    requirements = sequence(spec["requirements"])
    require(bool(requirements), "requirements_missing")
    ids = []
    for item in requirements:
        row = object_value(item, {"id", "text", "acceptance"})
        ids.append(stable_id(row["id"]))
        text(row["text"], maximum=8000)
        text(row["acceptance"], maximum=8000)
    require(len(ids) == len(set(ids)), "requirement_ids_duplicate")
    ports = sequence(spec["ports"])
    port_names = []
    for index, item in enumerate(ports):
        port = object_value(item, {"name", "direction", "width"})
        port_names.append(specification_name(port["name"], "port", index=index))
        require(port["direction"] in ("input", "output", "inout"), "port_direction_invalid")
        require(type(port["width"]) is int and 1 <= port["width"] <= 65536, "port_width_invalid")
    require(len(port_names) == len(set(port_names)), "port_names_duplicate")
    for field in ("questions", "assumptions"):
        item_ids = []
        for item in sequence(spec[field], maximum=32):
            keys = {"id", "text"} if field == "questions" else {"id", "text", "rationale"}
            row = object_value(item, keys)
            item_ids.append(stable_id(row["id"]))
            text(row["text"], maximum=8000)
            if field == "assumptions":
                text(row["rationale"], maximum=8000)
        require(len(item_ids) == len(set(item_ids)), "decision_ids_duplicate")
    if "readiness" in spec:
        from openrtl.domain.design_readiness import validate_readiness
        validate_readiness(spec["readiness"], spec)
    if "hardware_specification" in spec:
        from openrtl.domain.hardware_specification import validate_hardware_specification
        validate_hardware_specification(spec["hardware_specification"])
    require(not require_hardware_specification or "hardware_specification" in spec,
            "hardware_specification_required")
    require(len(canonical(spec)) <= MAX_CONTEXT_BYTES, "specification_too_large")
    return spec


def validate_manifest(value: object, files: dict[str, str], spec: JsonObject) -> JsonObject:
    from openrtl.domain.artifact_validation import manifest_validation

    manifest = object_value(value, {"top", "sources", "test_modules", "expected_tests",
                                    "requirement_tests", "seed"})
    with manifest_validation("top"):
        require(name(manifest["top"]) == spec["top"], "top_differs_from_specification")
    with manifest_validation("sources"):
        sources = [source_path(p) for p in sequence(manifest["sources"])]
        require(bool(sources) and len(set(sources)) == len(sources), "source_list_invalid")
        for path in sources:
            require(source_path(path).startswith("rtl/") and path in files, "source_unavailable")
        require(set(sources) == {p for p in files if p.startswith("rtl/") and p.endswith((".sv", ".v"))},
                "unlisted_rtl_source")
    with manifest_validation("test_modules"):
        modules = [name(m) for m in sequence(manifest["test_modules"])]
        require(bool(modules) and len(set(modules)) == len(modules), "test_modules_invalid")
        for module in modules:
            require("dv/" + name(module) + ".py" in files, "test_module_unavailable")
    with manifest_validation("expected_tests"):
        tests = [name(t) for t in sequence(manifest["expected_tests"])]
        require(bool(tests) and len(set(tests)) == len(tests), "expected_tests_invalid")
        for test in tests:
            name(test)
    with manifest_validation("requirement_tests"):
        links = sequence(manifest["requirement_tests"])
        covered = []
        for item in links:
            row = object_value(item, {"requirement_id", "tests"})
            covered.append(stable_id(row["requirement_id"]))
            linked_tests = sequence(row["tests"])
            require(bool(linked_tests) and all(t in tests for t in linked_tests), "test_link_invalid")
        require(len(covered) == len(set(covered)) and set(covered) == {r["id"] for r in spec["requirements"]},
                "requirement_test_links_incomplete")
    with manifest_validation("seed"):
        require(type(manifest["seed"]) is int and 0 <= manifest["seed"] < 2**31, "seed_invalid")
    return manifest


def validate_files(value: object, stage: str) -> list[JsonObject]:
    files = sequence(value)
    require(bool(files), "contribution_files_missing")
    prefixes = {"architecture": ("docs/",), "verification_plan": ("docs/",),
                "reference_model": ("model/",), "rtl": ("rtl/",), "assertions": ("rtl/",),
                "dv": ("dv/",), "diagnosis": ("rtl/", "dv/", "model/")}
    seen = set()
    result = []
    for item in files:
        row = object_value(item, {"path", "content"})
        path = source_path(row["path"])
        require(path.startswith(prefixes[stage]) and path not in seen, "contribution_ownership_invalid")
        seen.add(path)
        text(row["content"])
        result.append(row)
    require(sum(len(r["content"].encode()) for r in result) <= MAX_CONTEXT_BYTES, "contribution_too_large")
    return result


def legacy_initial_state() -> JsonObject:
    return {"schema": LEGACY_SESSION_SCHEMA, "revision": 0, "status": "discovery", "stage": 0,
            "spec": None, "approved_spec": None, "files": {}, "manifest": None,
            "calls": 0, "repairs": 0, "active": None, "simulation": None,
            "review": None, "detail": "normal", "last_error": None, "summaries": {}}


def previous_initial_state() -> JsonObject:
    return {**legacy_initial_state(), "schema": PREVIOUS_SESSION_SCHEMA, "limits": None,
            "delegation": None, "warnings": [], "approval_mode": None, "acceptance_mode": None}


def imported_initial_state() -> JsonObject:
    return {**previous_initial_state(), "schema": IMPORT_SESSION_SCHEMA, "imports": {}, "baseline": None, "change_plan": None}


def coaching_initial_state() -> JsonObject:
    return {**imported_initial_state(), "schema": COACHING_SESSION_SCHEMA,
            "pace": "stage", "proposal": None, "analysis": None}


def web_initial_state() -> JsonObject:
    return {**coaching_initial_state(), "schema": WEB_SESSION_SCHEMA,
            "engineering_memory": [], "workspace_operations": {}}


def initial_state() -> JsonObject:
    return {**web_initial_state(), "schema": SESSION_SCHEMA,
            "provider": {"model": None, "limit_nano_usd": None, "spent_nano_usd": 0,
                         "pending": None, "uncertain": False, "prior_unpriced_calls": 0}}


def validate_provider_state(value: object, active: object) -> None:
    from openrtl.domain.provider_controls import selector_provider, validate_provider_selector
    row = object_value(value, {"model", "limit_nano_usd", "spent_nano_usd", "pending",
                               "uncertain", "prior_unpriced_calls"})
    require(row["model"] is not None or row["limit_nano_usd"] is None,
            "provider_selection_incomplete")
    if row["model"] is not None:
        selector = validate_provider_selector(row["model"])
        require((selector_provider(selector) == "openai") == (row["limit_nano_usd"] is not None),
                "provider_selection_incomplete")
    require(row["limit_nano_usd"] is None or type(row["limit_nano_usd"]) is int and
            10_000_000 <= row["limit_nano_usd"] <= 1_000_000_000_000,
            "provider_spend_limit_invalid")
    require(type(row["spent_nano_usd"]) is int and row["spent_nano_usd"] >= 0 and
            type(row["prior_unpriced_calls"]) is int and 0 <= row["prior_unpriced_calls"] <= 200 and
            type(row["uncertain"]) is bool, "provider_accounting_invalid")
    if row["pending"] is not None:
        pending = object_value(row["pending"], {"operation_id", "reserved_nano_usd", "model"})
        require(isinstance(active, dict) and active["kind"] == "expert" and
                active["id"] == pending["operation_id"] and
                pending["model"] == row["model"] and
                type(pending["reserved_nano_usd"]) is int and pending["reserved_nano_usd"] > 0,
                "provider_pending_invalid")


def validate_engineering_memory(value: object) -> list[JsonObject]:
    """Bounded attributed design facts, never a transcript or authority grant."""
    rows = sequence(value, maximum=64)
    seen: set[str] = set()
    for value in rows:
        row = object_value(value, {"id", "kind", "text", "provenance"})
        identifier = stable_id(row["id"])
        require(identifier not in seen, "engineering_memory_id_duplicate")
        seen.add(identifier)
        require(row["kind"] in ("requirement", "assumption", "decision", "question"),
                "engineering_memory_kind_invalid")
        text(row["text"], maximum=1024)
        require(row["provenance"] in ("agent_proposal", "user_confirmed"),
                "engineering_memory_provenance_invalid")
    require(len(canonical(rows)) <= 32 * 1024, "engineering_memory_too_large")
    return cast(list[JsonObject], rows)


def validate_workspace_operations(value: object, revision: int) -> None:
    require(isinstance(value, dict) and len(value) <= 128, "workspace_operations_invalid")
    operations = cast(dict[object, object], value)
    for identifier, value in operations.items():
        require(isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{32}", identifier) is not None,
                "workspace_operation_id_invalid")
        row = object_value(value, {"request_digest", "phase", "result_revision", "error_code"})
        require(isinstance(row["request_digest"], str) and
                re.fullmatch(r"sha256:[a-f0-9]{64}", row["request_digest"]) is not None,
                "workspace_request_digest_invalid")
        require(row["phase"] in ("queued", "active", "completed", "failed", "cancelled",
                                 "cancellation_requested", "reconciliation_needed"),
                "workspace_operation_phase_invalid")
        require(row["result_revision"] is None or
                type(row["result_revision"]) is int and 0 <= row["result_revision"] <= revision,
                "workspace_result_revision_invalid")
        require(row["error_code"] is None or row["error_code"] in
                EXPERT_OPERATION_ERROR_CODES | {"operation_failed", "operation_cancelled_uncertain"},
                "workspace_error_code_invalid")


def validate_state(value: object) -> JsonObject:
    require(isinstance(value, dict), "session_object_required")
    legacy = cast(JsonObject, value).get("schema") == LEGACY_SESSION_SCHEMA
    previous = cast(JsonObject, value).get("schema") == PREVIOUS_SESSION_SCHEMA
    imported = cast(JsonObject, value).get("schema") == IMPORT_SESSION_SCHEMA
    coaching = cast(JsonObject, value).get("schema") == COACHING_SESSION_SCHEMA
    web = cast(JsonObject, value).get("schema") == WEB_SESSION_SCHEMA
    state = object_value(value, set(legacy_initial_state() if legacy else previous_initial_state() if previous else
                                   imported_initial_state() if imported else coaching_initial_state() if coaching else
                                   web_initial_state() if web else initial_state()))
    require(state["schema"] in (SESSION_SCHEMA, WEB_SESSION_SCHEMA, COACHING_SESSION_SCHEMA, IMPORT_SESSION_SCHEMA,
                                PREVIOUS_SESSION_SCHEMA, LEGACY_SESSION_SCHEMA), "session_schema_unrecognized")
    for field in ("revision", "stage", "calls", "repairs"):
        require(type(state[field]) is int and state[field] >= 0, "session_counter_invalid")
    require(state["stage"] <= len(STAGES) and state["calls"] <= 200 and state["repairs"] <= 5,
            "session_counter_invalid")
    require(state["status"] in ("discovery", "building", "needs_repair", "needs_signoff",
                                "review_blocked", "awaiting_acceptance", "accepted"), "session_status_invalid")
    require(state["detail"] in ("brief", "normal", "detailed"), "session_detail_invalid")
    require(isinstance(state["files"], dict) and len(state["files"]) <= 128, "session_files_invalid")
    for path, digest in state["files"].items():
        source_path(path)
        require(isinstance(digest, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", digest) is not None,
                "session_artifact_digest_invalid")
    if state["spec"] is not None:
        validate_spec(state["spec"])
    if state["status"] != "discovery":
        spec = validate_spec(state["spec"])
        if "readiness" in spec:
            from openrtl.domain.design_readiness import require_ready
            require_ready(spec)
        require(state["approved_spec"] == content_digest(spec) and not spec["questions"] and bool(spec["ports"]),
                "session_approval_invalid")
    else:
        require(state["approved_spec"] is None and state["stage"] == 0 and not state["files"],
                "discovery_state_invalid")
    if state["active"] is not None:
        active = object_value(state["active"], {"id", "kind", "stage"})
        require(isinstance(active["id"], str) and re.fullmatch(r"[a-f0-9]{32}", active["id"]) is not None and
                active["kind"] in ("expert", "simulation") and active["stage"] in (*ROLES, "simulation"),
                "session_active_operation_invalid")
    require(state["last_error"] in (None, "expert_output_invalid", "expert_invocation_failed", "simulation_execution_failed",
                                    "interrupted_operation_abandoned") or state["last_error"] in PROVIDER_FAILURE_CODES,
            "session_error_code_invalid")
    require(isinstance(state["summaries"], dict) and set(state["summaries"]).issubset(ROLES), "session_summaries_invalid")
    for summary in state["summaries"].values():
        text(summary, maximum=8000)
    if state["manifest"] is not None:
        validate_manifest(state["manifest"], state["files"], state["spec"])
    if state["status"] in ("needs_repair", "needs_signoff", "review_blocked", "awaiting_acceptance", "accepted"):
        require(state["stage"] == len(STAGES) and state["manifest"] is not None and
                isinstance(state["simulation"], dict), "session_evidence_missing")
    if state["status"] in ("needs_signoff", "review_blocked", "awaiting_acceptance", "accepted"):
        expected = content_digest({"spec": state["approved_spec"], "files": state["files"], "manifest": state["manifest"]})
        require(state["simulation"].get("input_digest") == expected and state["simulation"].get("status") == "passed",
                "session_simulation_stale")
    if state["status"] in ("awaiting_acceptance", "accepted"):
        require(isinstance(state["review"], dict) and state["review"].get("verdict") == "accept" and
                state["review"].get("findings") == [], "session_signoff_invalid")
    require(len(canonical(state)) <= 2 * 1024 * 1024, "session_state_too_large")
    if not legacy:
        from openrtl.domain.design_delegation import validate_session_extensions
        validate_session_extensions(state)
    if state["schema"] in (SESSION_SCHEMA, WEB_SESSION_SCHEMA, COACHING_SESSION_SCHEMA, IMPORT_SESSION_SCHEMA):
        from openrtl.domain.design_imports import validate_import_state
        validate_import_state(state)
    if state["schema"] in (SESSION_SCHEMA, WEB_SESSION_SCHEMA, COACHING_SESSION_SCHEMA):
        from openrtl.domain.design_coaching import validate_coaching_state
        validate_coaching_state(state)
    if state["schema"] in (SESSION_SCHEMA, WEB_SESSION_SCHEMA):
        validate_engineering_memory(state["engineering_memory"])
        validate_workspace_operations(state["workspace_operations"], state["revision"])
    if state["schema"] == SESSION_SCHEMA:
        validate_provider_state(state["provider"], state["active"])
    return state
