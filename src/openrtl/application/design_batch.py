"""Bounded unattended execution over the same reviewed engineering state machine."""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Callable

from openrtl.application.design_agent import DesignAgent, design_input_digest
from openrtl.domain.design_delegation import validate_authorization
from openrtl.domain.design_session import JsonObject, SESSION_SCHEMA, content_digest, require, validate_spec


def bind_delegation(agent: DesignAgent, value: object, approved_digest: str) -> JsonObject:
    plan = validate_authorization(value)
    require(content_digest(plan) == approved_digest, "reviewed_delegation_digest_mismatch")
    state = agent.store.read()
    require(state["schema"] == SESSION_SCHEMA, "explicit_session_upgrade_required")
    if state["delegation"] is not None:
        require(state["delegation"]["authorization"] == plan, "existing_delegation_cannot_be_replaced")
        return state
    require(state["active"] is None, "interrupted_operation_requires_reconciliation")
    require(state["status"] == "discovery", "delegation_requires_unapproved_seed")
    seed = validate_spec(state["spec"])
    require(content_digest(seed) == plan["seed_spec_digest"], "delegation_seed_changed")
    updated = copy.deepcopy(state)
    prior = state["limits"] or {"max_calls": agent.policy.max_calls, "max_repairs": agent.policy.max_repairs}
    updated["limits"] = {k: min(prior[k], plan[k], getattr(agent.policy, k)) for k in prior}
    now = time.time_ns()
    updated["delegation"] = {"authorization": plan, "seed_spec": seed, "digest": approved_digest,
                             "steps": 0, "started_ns": now, "deadline_ns": now + plan["max_seconds"] * 10**9}
    return agent.store.save(state, updated, "delegation.granted", {"delegation_digest": approved_digest})


def batch_report(agent: DesignAgent, outcome: str) -> JsonObject:
    state = agent.store.read()
    delegation = state["delegation"]
    return {"schema": "openrtl.design-batch-report.v1", "outcome": outcome,
            "revision": state["revision"], "status": state["status"], "calls": state["calls"],
            "repairs": state["repairs"], "limits": state["limits"], "active": state["active"],
            "steps": delegation["steps"] if delegation else None,
            "deadline_ns": delegation["deadline_ns"] if delegation else None,
            "delegation_digest": delegation["digest"] if delegation else None,
            "approval_mode": state["approval_mode"], "acceptance_mode": state["acceptance_mode"],
            "warnings": state["warnings"], "spec_digest": content_digest(state["spec"]),
            "artifacts": state["files"], "simulation_digest": content_digest(state["simulation"])}


async def run_batch(agent: DesignAgent, *, emit: Callable[[str], None] = print) -> JsonObject:
    """No provider/runtime permission is inferred from saved delegation."""
    while True:
        state = agent.store.read()
        require(state.get("delegation") is not None, "explicit_delegation_required")
        delegation = state["delegation"]
        plan = delegation["authorization"]
        if state["active"] is not None:
            return batch_report(agent, "reconciliation_required")
        if state["status"] == "accepted":
            return batch_report(agent, "accepted")
        if state["status"] == "review_blocked" or (
                state["status"] == "awaiting_acceptance" and not plan["allow_final_acceptance"]):
            return batch_report(agent, "awaiting_review")
        if state["status"] == "discovery" and state["spec"]["questions"] and not plan["allow_assumptions"]:
            return batch_report(agent, "awaiting_review")
        remaining = (delegation["deadline_ns"] - time.time_ns()) / 10**9
        if remaining <= 0 or delegation["steps"] >= plan["max_steps"]:
            return batch_report(agent, "budget_exhausted")
        updated = copy.deepcopy(state)
        updated["delegation"]["steps"] += 1
        agent.store.save(state, updated, "batch.step_started", {"delegation_digest": delegation["digest"]})
        emit("Batch step " + str(updated["delegation"]["steps"]) + ": " + state["status"])
        try:
            await asyncio.wait_for(_step(agent), timeout=remaining)
        except TimeoutError:
            # Cancellation deliberately leaves an in-flight intent for recovery.
            return batch_report(agent, "deadline_exhausted")
        except (ValueError, OSError, KeyError, TypeError):
            # Do not echo arbitrary provider errors or guess that a failure is safe to retry.
            return batch_report(agent, "stopped")


async def _step(agent: DesignAgent) -> None:
    state = agent._idle()
    if state["status"] == "discovery":
        if state["spec"]["questions"]:
            await agent.discuss(
                "Resolve the open specification questions for an unattended exploration. "
                "For EVERY question, retain its exact ID in an explicit assumption with a rationale. "
                "Return the complete specification with no open questions. Preserve all other scope unless "
                "the explicit batch authorization allows requirement proposals. Authorization: " +
                str(state["delegation"]["authorization"]["allow_requirement_proposals"]))
        else:
            agent.approve(content_digest(state["spec"]), delegated=True)
    elif state["status"] == "awaiting_acceptance":
        digest = content_digest({"input": design_input_digest(state), "simulation": state["simulation"],
                                 "review": state["review"]})
        agent.approve(digest, delegated=True)
    else:
        await agent.advance()
