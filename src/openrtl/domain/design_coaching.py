"""Reviewable coaching output, never authority to apply a model suggestion."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from openrtl.domain.design_imports import digest_value, validate_change_plan
from openrtl.domain.design_session import JsonObject, canonical, content_digest, object_value, require, sequence, text


def analysis_input_digest(state: JsonObject) -> str:
    return content_digest({"specification": state["spec"], "files": state["files"], "imports": state.get("imports", {}),
                           "manifest": state["manifest"], "simulation": state["simulation"]})


def simulation_duration(value: object) -> Decimal:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("simulation_duration_invalid")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError("simulation_duration_invalid") from None
    exponent = result.as_tuple().exponent
    require(result.is_finite() and isinstance(exponent, int), "simulation_duration_invalid")
    if not isinstance(exponent, int):
        raise ValueError("simulation_duration_invalid")
    require(Decimal(0) <= result <= Decimal("1e18") and exponent >= -18, "simulation_duration_invalid")
    return result


def validate_proposal(value: object) -> JsonObject:
    row = object_value(value, {"schema", "intent", "summary", "plan", "status"})
    require(row["schema"] == "openrtl.design-change-proposal.v1" and row["status"] == "awaiting_review",
            "change_proposal_schema_invalid")
    require(row["intent"] in ("feature", "dv", "optimization"), "change_intent_invalid")
    text(row["summary"], maximum=8000)
    validate_change_plan(row["plan"])
    require(len(canonical(row)) <= 512 * 1024, "change_proposal_too_large")
    return row


def validate_intent(plan: JsonObject, intent: str, state: JsonObject) -> None:
    require(intent in ("feature", "dv", "optimization"), "change_intent_invalid")
    if intent != "feature":
        require(plan["specification"] == state["spec"], "improvement_cannot_change_requirements")
        allowed = {"verification_plan", "dv"} if intent == "dv" else {"rtl"}
        require(all(not paths or stage in allowed for stage, paths in plan["stage_paths"].items()),
                "improvement_write_scope_invalid")
        if intent == "optimization":
            require(plan["manifest"] == state["manifest"], "optimization_workload_must_be_retained")


def validate_analysis(value: object) -> JsonObject:
    row = object_value(value, {"schema", "input_digest", "summary", "findings"})
    require(row["schema"] == "openrtl.design-analysis.v1", "analysis_schema_invalid")
    digest_value(row["input_digest"])
    text(row["summary"], maximum=8000)
    for value in sequence(row["findings"], maximum=32):
        finding = object_value(value, {"requirement_id", "basis", "description", "recommended_check", "references"})
        require(finding["basis"] in ("source", "simulation", "hypothesis"), "analysis_basis_invalid")
        text(finding["requirement_id"], maximum=128)
        text(finding["description"], maximum=4000)
        text(finding["recommended_check"], maximum=4000)
        for value in sequence(finding["references"], maximum=16):
            ref = object_value(value, {"path", "line"})
            text(ref["path"], maximum=240)
            require(type(ref["line"]) is int and ref["line"] > 0, "analysis_line_invalid")
    require(len(canonical(row)) <= 64000, "analysis_too_large")
    return row


def validate_coaching_state(state: JsonObject) -> None:
    require(state["pace"] in ("stage", "continuous"), "coaching_pace_invalid")
    if state["proposal"] is not None:
        validate_proposal(state["proposal"])
    if state["analysis"] is not None:
        validate_analysis(state["analysis"])
