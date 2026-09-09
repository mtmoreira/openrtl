"""Explicit batch authority and independently reviewable assumption warnings."""

from __future__ import annotations

from openrtl.domain.design_session import (
    JsonObject, content_digest, object_value, require, sequence, text, validate_spec,
)


def validate_authorization(value: object) -> JsonObject:
    plan = object_value(value, {"schema", "seed_spec_digest", "allow_assumptions",
                                "allow_requirement_proposals", "allow_final_acceptance",
                                "max_calls", "max_repairs", "max_steps", "max_seconds"})
    require(plan["schema"] == "openrtl.design-delegation.v1", "delegation_schema_invalid")
    digest = text(plan["seed_spec_digest"], maximum=71)
    require(len(digest) == 71 and digest.startswith("sha256:") and
            all(c in "0123456789abcdef" for c in digest[7:]), "delegation_seed_digest_invalid")
    for field in ("allow_assumptions", "allow_requirement_proposals", "allow_final_acceptance"):
        require(type(plan[field]) is bool, "delegation_boolean_invalid")
    for field, low, high in (("max_calls", 1, 200), ("max_repairs", 0, 5),
                              ("max_steps", 1, 256), ("max_seconds", 1, 86400)):
        require(type(plan[field]) is int and low <= plan[field] <= high, "delegation_limit_invalid")
    return plan


def validate_delegated_spec(seed: JsonObject, proposed: JsonObject, plan: JsonObject) -> None:
    validate_spec(seed)
    validate_spec(proposed)
    require(content_digest(seed) == plan["seed_spec_digest"], "delegation_seed_changed")
    require(not proposed["questions"] and bool(proposed["ports"]), "delegated_spec_incomplete")
    require(plan["allow_assumptions"] or not proposed["assumptions"], "assumptions_not_delegated")
    required_ids = {q["id"] for q in seed["questions"]}
    require(required_ids.issubset({a["id"] for a in proposed["assumptions"]}), "batch_question_resolution_unrecorded")
    if not plan["allow_requirement_proposals"]:
        require(all(seed[k] == proposed[k] for k in seed if k not in ("questions", "assumptions")),
                "requirement_changes_not_delegated")


def warning(kind: str, subject: str, spec_digest: str, description: str, rationale: str) -> JsonObject:
    body = {"kind": kind, "subject": subject, "spec_digest": spec_digest,
            "text": description, "rationale": rationale}
    return {**body, "id": "warning." + content_digest(body)[7:31], "review": "pending"}


def specification_warnings(seed: JsonObject | None, spec: JsonObject) -> list[JsonObject]:
    digest = content_digest(spec)
    result = [warning("assumption", a["id"], digest, a["text"], a["rationale"]) for a in spec["assumptions"]]
    if seed is not None:
        for field in ("title", "top", "behavior", "clock_reset", "requirements", "ports", "readiness"):
            if seed.get(field) == spec.get(field):
                continue
            if field in ("requirements", "ports"):
                key = "id" if field == "requirements" else "name"
                before = {row[key]: row for row in seed[field]}
                after = {row[key]: row for row in spec[field]}
                subjects = [field + "." + name for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)]
            else:
                subjects = [field]
            result.extend(warning("requirement_proposal", subject, digest,
                                  "Batch proposed a change to " + subject + "; inspect the exact specification.",
                                  "Authorized by broad delegation, not individually reviewed.") for subject in subjects)
    return result


def validate_session_extensions(state: JsonObject) -> None:
    limits = state["limits"]
    if limits is not None:
        object_value(limits, {"max_calls", "max_repairs"})
        require(type(limits["max_calls"]) is int and 1 <= limits["max_calls"] <= 200 and
                type(limits["max_repairs"]) is int and 0 <= limits["max_repairs"] <= 5,
                "session_limits_invalid")
    for field in ("approval_mode", "acceptance_mode"):
        require(state[field] in (None, "user", "delegated", "legacy_user"), "session_authority_mode_invalid")
    warnings = sequence(state["warnings"], maximum=256)
    ids = set()
    for item in warnings:
        row = object_value(item, {"id", "kind", "subject", "spec_digest", "text", "rationale", "review"})
        require(row["kind"] in ("assumption", "requirement_proposal", "interrupted_operation") and
                row["review"] in ("pending", "acknowledged"), "session_warning_invalid")
        for field in ("subject", "spec_digest", "text", "rationale"):
            text(row[field], maximum=8000)
        expected = warning(row["kind"], row["subject"], row["spec_digest"], row["text"], row["rationale"])
        require(row["id"] == expected["id"] and row["id"] not in ids, "warning_identity_invalid")
        ids.add(row["id"])
    delegated = state["delegation"]
    if delegated is not None:
        row = object_value(delegated, {"authorization", "seed_spec", "digest", "steps", "started_ns", "deadline_ns"})
        plan = validate_authorization(row["authorization"])
        require(content_digest(validate_spec(row["seed_spec"])) == plan["seed_spec_digest"] and
                row["digest"] == content_digest(plan), "session_delegation_binding_invalid")
        require(type(row["steps"]) is int and 0 <= row["steps"] <= plan["max_steps"] and
                type(row["started_ns"]) is int and row["started_ns"] > 0 and
                row["deadline_ns"] == row["started_ns"] + plan["max_seconds"] * 10**9,
                "session_delegation_budget_invalid")
        require(limits is not None and limits["max_calls"] <= plan["max_calls"] and
                limits["max_repairs"] <= plan["max_repairs"], "delegation_limits_not_durable")
    if state["approval_mode"] == "delegated":
        require(delegated is not None, "delegated_approval_without_authority")
        validate_delegated_spec(delegated["seed_spec"], state["spec"], delegated["authorization"])
        expected_warnings = specification_warnings(delegated["seed_spec"], state["spec"])
        require({w["id"] for w in expected_warnings}.issubset(ids), "delegated_warnings_missing")
    if state["acceptance_mode"] == "delegated":
        require(delegated is not None and delegated["authorization"]["allow_final_acceptance"],
                "final_acceptance_not_delegated")
