"""Read-only local acceptance inventory; never proof of provider origin or coverage."""
from __future__ import annotations

import hashlib
import json

from openrtl.adapters.design_session_store import DesignSessionStore, safe_root
from openrtl.application.design_agent import design_input_digest
from openrtl.domain.design_session import (
    JsonObject, SESSION_SCHEMA, STAGES, content_digest, object_value, require, validate_spec,
)


def acceptance_report(store: DesignSessionStore, expected_spec: object | None = None) -> JsonObject:
    """Rehash local artifacts and separate acceptance gates from a live qualification claim."""
    state = store.read()
    require(state["schema"] == SESSION_SCHEMA, "acceptance_requires_explicit_session_upgrade")
    store.contents(state)
    store.import_contents(state)
    if expected_spec is not None:
        expected = validate_spec(expected_spec)
        require(state["spec"] == expected, "acceptance_specification_differs_from_review")
    blockers: list[str] = []
    if state["active"] is not None:
        blockers.append("interrupted_operation_requires_reconciliation")
    if state["approved_spec"] is None:
        blockers.append("requirements_not_approved")
    if state["stage"] != len(STAGES) or state["manifest"] is None:
        blockers.append("engineering_collateral_incomplete")
    simulation = state["simulation"]
    measurement = None
    if simulation is None or simulation.get("status") != "passed":
        blockers.append("passing_simulation_missing")
    elif simulation.get("schema") != "openrtl.design-simulation.v2":
        blockers.append("fresh_runtime_bound_simulation_required")
    else:
        require(simulation["input_digest"] == design_input_digest(state), "acceptance_simulation_stale")
        measurement = store.measurement(state)
        # Re-read only the exact hash-bound model result. A report-shaped fixture
        # is not enough; missing or changed owned evidence is a hard failure.
        entry = simulation["artifacts"]["model-results.json"]
        path = safe_root(store.root / entry["path"])
        require(path.is_file() and path.stat().st_size <= 64000, "acceptance_model_evidence_unavailable")
        data = path.read_bytes()
        require(len(data) <= 64000 and len(data) == entry["bytes"] and
                hashlib.sha256(data).hexdigest() == entry["sha256"], "acceptance_model_evidence_changed")
        model = object_value(json.loads(data), {"tests", "passed"})
        require(model["passed"] is True and type(model["tests"]) is int and
                model["tests"] > 0 and model["tests"] == simulation["model_tests"],
                "acceptance_model_tests_invalid")
    review = state["review"]
    if review is None or review.get("verdict") != "accept" or review.get("findings") != []:
        blockers.append("independent_role_signoff_missing")
    if state["status"] != "accepted" or state["acceptance_mode"] not in ("user", "delegated"):
        blockers.append("final_acceptance_missing")
    pending = [row["id"] for row in state["warnings"] if row["review"] != "acknowledged"]
    if pending:
        blockers.append("warnings_require_user_review")
    # Stable identifiers and hashes only: no source bodies, raw prompts, provider
    # exception text, full tool logs or unbounded findings in the summary.
    links = state["manifest"]["requirement_tests"] if state["manifest"] else []
    result: JsonObject = {
        "schema": "openrtl.design-acceptance.v1",
        "status": "local_gates_satisfied" if not blockers else "pending",
        "evidence_tier": "reverified_local_receipt" if measurement else "session_state_only",
        "live_qualification": "not_established_by_this_report",
        "revision": state["revision"], "session_digest": content_digest(state),
        "spec_digest": content_digest(state["spec"]), "expected_spec_checked": expected_spec is not None,
        "input_digest": design_input_digest(state), "artifacts": state["files"],
        "import_digests": {path: row["digest"] for path, row in state["imports"].items()},
        "requirement_tests": links, "measurement": measurement,
        "simulation_digest": content_digest(simulation), "review_digest": content_digest(review),
        "approval_mode": state["approval_mode"], "acceptance_mode": state["acceptance_mode"],
        "pending_warning_ids": pending, "blockers": blockers,
        "effects": {"provider_calls": False, "credential_resolution": False,
                    "simulation_execution": False, "session_mutation": False},
        "limits": ["Local hashes establish consistency, not independent attestation or provider origin.",
                   "Requirement-to-test links are declarations, not measured coverage or exhaustive proof.",
                   "Generated models, tests and assertions need adequacy review even when they pass.",
                   "Role separation is not proof that independent models or reviewers were used.",
                   "No hardware PPA, synthesis, formal proof, FPGA readiness or publication is established."],
    }
    require(store.read() == state, "acceptance_session_changed_during_inspection")
    return {**result, "content_digest": content_digest(result)}
