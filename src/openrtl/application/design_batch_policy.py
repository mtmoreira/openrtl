"""Ordinary batch policy inputs compiled to the existing bounded delegation."""

from __future__ import annotations

import asyncio
import time
from typing import Callable

from openrtl.application.design_agent import DesignAgent
from openrtl.application.design_batch import batch_report, bind_delegation, run_batch
from openrtl.domain.design_delegation import validate_authorization
from openrtl.domain.design_readiness import require_ready
from openrtl.domain.design_session import JsonObject, content_digest, require, validate_spec


def policy_plan(agent: DesignAgent, seed: JsonObject, policy: str, *, final_acceptance: bool = False,
                max_steps: int = 128, max_seconds: int = 1800) -> JsonObject:
    require(policy in ("review", "assumptions", "explore"), "batch_policy_invalid")
    return validate_authorization({"schema": "openrtl.design-delegation.v1", "seed_spec_digest": content_digest(seed),
        "allow_assumptions": policy in ("assumptions", "explore"), "allow_requirement_proposals": policy == "explore",
        "allow_final_acceptance": final_acceptance, "max_calls": agent.policy.max_calls,
        "max_repairs": agent.policy.max_repairs, "max_steps": max_steps, "max_seconds": max_seconds})


async def run_policy_batch(agent: DesignAgent, policy: str, *, specification: object | None = None,
                           intent: str | None = None, final_acceptance: bool = False, max_steps: int = 128,
                           max_seconds: int = 1800, emit: Callable[[str], None] = print) -> JsonObject:
    require(specification is None or intent is None, "choose_specification_or_intent")
    require(policy in ("review", "assumptions", "explore") and type(final_acceptance) is bool,
            "batch_policy_invalid")
    require(type(max_steps) is int and 1 <= max_steps <= 256 and
            type(max_seconds) is int and 1 <= max_seconds <= 86400, "batch_policy_bound_invalid")
    started_ns = time.time_ns()
    state = agent.store.read()
    def stopped(outcome: str, action: str) -> JsonObject:
        return {**batch_report(agent, outcome), "next_action": action}
    if state["active"] is not None:
        return stopped("reconciliation_required", "Inspect and explicitly recover the recorded operation before resuming.")
    if intent is not None:
        require(state["spec"] is None and state["delegation"] is None, "intent_is_only_for_new_session")
        if agent.expert is None:
            return stopped("provider_permission_required", "Resume with explicit provider permission and the original intent file.")
        try:
            await asyncio.wait_for(agent.discuss(intent), timeout=max_seconds)
        except TimeoutError:
            return stopped("deadline_exhausted", "Discovery timed out. Inspect the recorded operation before explicit recovery.")
        except (ValueError, OSError, KeyError, TypeError):
            return stopped("stopped", "Inspect the discovery result and recorded call budget before continuing.")
        state = agent.store.read()
    elif specification is not None:
        seed = validate_spec(specification)
        if state["spec"] is None:
            agent.propose(seed)
            state = agent.store.read()
        else:
            expected = state["delegation"]["seed_spec"] if state["delegation"] else state["spec"]
            require(seed == expected, "batch_seed_cannot_replace_existing_spec")
    if state["spec"] is None:
        return stopped("specification_required", "Supply a specification or a plain-text intent with provider permission.")
    seed = state["delegation"]["seed_spec"] if state["delegation"] else state["spec"]
    plan = policy_plan(agent, seed, policy, final_acceptance=final_acceptance,
                       max_steps=max_steps, max_seconds=max_seconds)
    emit("Batch scope: " + policy + "; assumptions " + str(plan["allow_assumptions"]) +
         "; requirement proposals " + str(plan["allow_requirement_proposals"]) +
         "; final acceptance " + str(final_acceptance))
    emit("Limits: " + str(plan["max_calls"]) + " calls, " + str(plan["max_repairs"]) + " repairs, " +
         str(max_steps) + " steps, " + str(max_seconds) + " seconds. Saved deadlines are never renewed.")
    emit("Delegation does not grant provider or simulation permission.")
    bind_delegation(agent, plan, content_digest(plan), started_ns=started_ns)
    # Legacy data stays readable. This normal route does not label it ready.
    if "readiness" not in state["spec"]:
        return stopped("readiness_review_required", "Complete the seven readiness categories interactively before running this specification.")
    if not state["spec"]["questions"]:
        try:
            require_ready(state["spec"])
        except ValueError:
            return stopped("readiness_review_required", "Resolve the readiness decisions in conversation before continuing.")
    report = await run_batch(agent, emit=emit, reviewed_contracts=True)
    actions = {"accepted": "Inspect retained artifacts and qualification limits.",
               "awaiting_review": "Resume the conversation to review questions, assumptions or final acceptance.",
               "reconciliation_required": "Inspect and explicitly recover the recorded operation.",
               "budget_exhausted": "The saved policy budget is exhausted; it cannot be renewed by resuming.",
               "deadline_exhausted": "Inspect recorded operations before explicit recovery; the deadline remains exhausted.",
               "stopped": "Inspect status and explicit provider/simulation permissions; no operation is automatically replayed."}
    return {**report, "next_action": actions[report["outcome"]]}
