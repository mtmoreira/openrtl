"""Review imported baselines and fill missing collateral without rewriting RTL."""
from __future__ import annotations

import copy

from openrtl.application.design_agent import DesignAgent
from openrtl.domain.design_imports import validate_change_plan
from openrtl.domain.design_readiness import require_ready
from openrtl.domain.design_session import JsonObject, STAGES, content_digest, object_value, require, text


def completion_plan(agent: DesignAgent, request: object) -> JsonObject:
    state = agent._idle()
    require(state["status"] == "discovery" and bool(state["imports"]), "completion_requires_imported_discovery")
    require_ready(state["spec"])
    selected = object_value(request, {"specification", "stage_paths", "manifest"})
    require(selected["specification"] == state["spec"], "completion_must_preserve_reviewed_requirements")
    files = {p: row["digest"] for p, row in state["imports"].items()}
    agent.store.import_contents(state)
    require(any(p.startswith("rtl/") and p.endswith((".sv", ".v")) for p in files), "completion_requires_rtl")
    plan = validate_change_plan({"schema": "openrtl.design-change.v1", "base_input_digest": content_digest(state),
                                "base_files": files, **selected})
    scopes = plan["stage_paths"]
    writes = {p for paths in scopes.values() for p in paths}
    require(not scopes["rtl"] and not scopes["assertions"] and not writes.intersection(files),
            "completion_cannot_overwrite_imports_or_rtl")
    require(any(p.startswith("model/test_") for p in set(files) | writes), "completion_model_tests_missing")
    return plan


async def propose_import_work(agent: DesignAgent, *, completion: bool) -> JsonObject:
    state = agent._idle()
    require(state["status"] == "discovery" and bool(state["imports"]), "imported_discovery_required")
    require_ready(state["spec"])
    purpose = ("Propose missing architecture, verification plan, independent model tests and DV. Retain every imported file. "
               "Do not write any rtl or assertions stage paths. Return the exact current specification and a complete simulation manifest."
               if completion else "Propose a simulation manifest for the imported complete baseline. Return the exact current specification "
               "and empty stage_paths for all stages. Do not invent missing test modules or claim existing tests adequately cover requirements.")
    output, started = await agent._generate(state, "change_planning", purpose,
                                            intent="import_completion" if completion else "import_baseline")
    try:
        selected = object_value(output, {"summary", "specification", "stage_paths", "manifest"})
        text(selected["summary"], maximum=8000)
        require(selected["specification"] == state["spec"], "import_plan_changed_requirements")
        object_value(selected["stage_paths"], set(STAGES))
        if not completion:
            require(all(paths == [] for paths in selected["stage_paths"].values()), "baseline_cannot_generate_files")
        updated = copy.deepcopy(started)
        updated.update(active=None, last_error=None)
        # The proposal is transient; its reviewed contract and approved files are persisted on adoption.
        agent.store.save(started, updated, "operation.completed", {"output_digest": content_digest(selected)})
        request = {k: selected[k] for k in ("specification", "stage_paths", "manifest")}
        plan = completion_plan(agent, request) if completion else agent.plan_baseline(selected["manifest"])
        return {"summary": selected["summary"], "plan": plan}
    except (ValueError, KeyError, TypeError):
        current = agent.store.read()
        if current["active"] is not None:
            agent._failed(current, "expert_output_invalid")
        raise ValueError("expert_output_invalid") from None


def approve_completion(agent: DesignAgent, plan: JsonObject) -> JsonObject:
    state = agent._idle()
    request = {k: plan[k] for k in ("specification", "stage_paths", "manifest")}
    require(completion_plan(agent, request) == plan, "import_completion_review_stale")
    contents = agent.store.import_contents(state)
    updated = copy.deepcopy(state)
    updated.update(approved_spec=content_digest(state["spec"]), approval_mode="user", delegation=None,
                   status="building", stage=0, change_plan=plan, baseline=None, manifest=None,
                   simulation=None, review=None, acceptance_mode=None, proposal=None, analysis=None, last_error=None)
    agent._record_spec_warnings(updated)
    for warning in updated["warnings"]:
        if warning["spec_digest"] == updated["approved_spec"]:
            warning["review"] = "acknowledged"
    return agent.store.save(state, updated, "change.approved", {"output_digest": content_digest(plan), "authority": "user"},
                            files=[{"path": p, "content": c} for p, c in contents.items()])
