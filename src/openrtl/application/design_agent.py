"""Review-gated, persistent orchestration; experts propose, tools supply evidence."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Protocol
import uuid
import time

from openrtl.domain.design_session import (
    JsonObject, MAX_CONTEXT_BYTES, ROLES, STAGES, canonical, content_digest,
    object_value, require, sequence, text, validate_files, validate_manifest, validate_spec,
)


class SessionStore(Protocol):
    def read(self) -> JsonObject: ...
    def contents(self, state: JsonObject) -> dict[str, str]: ...
    def save(self, previous: JsonObject, updated: JsonObject, event: str,
             fields: JsonObject | None = None, *, files: list[JsonObject] | None = None) -> JsonObject: ...


@dataclass(frozen=True)
class ExpertReply:
    output: JsonObject
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class DesignExpert(Protocol):
    async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply: ...


class DesignSimulator(Protocol):
    async def simulate(self, files: dict[str, str], manifest: JsonObject,
                       input_digest: str, operation_id: str) -> JsonObject: ...


@dataclass(frozen=True)
class DesignPolicy:
    max_calls: int = 40
    max_repairs: int = 2

    def __post_init__(self) -> None:
        require(type(self.max_calls) is int and 1 <= self.max_calls <= 200, "call_budget_invalid")
        require(type(self.max_repairs) is int and 0 <= self.max_repairs <= 5, "repair_budget_invalid")


def design_input_digest(state: JsonObject) -> str:
    return content_digest({"spec": state["approved_spec"], "files": state["files"],
                           "manifest": state["manifest"]})


class DesignAgent:
    def __init__(self, store: SessionStore, expert: DesignExpert | None = None,
                 simulator: DesignSimulator | None = None, policy: DesignPolicy = DesignPolicy()) -> None:
        self.store, self.expert, self.simulator, self.policy = store, expert, simulator, policy

    def _idle(self) -> JsonObject:
        state = self.store.read()
        require(state["active"] is None, "interrupted_operation_requires_reconciliation")
        return state

    def propose(self, specification: object) -> JsonObject:
        """Allow an explicit structured specification without requiring a model."""
        state = self._idle()
        require(state["status"] == "discovery", "specification_already_approved")
        spec = validate_spec(specification)
        updated = copy.deepcopy(state)
        updated["spec"] = spec
        return self.store.save(state, updated, "spec.proposed", {"spec_digest": content_digest(spec)})

    async def discuss(self, message: str) -> JsonObject:
        state = self._idle()
        require(state["status"] == "discovery", "use_explain_or_explicit_revision")
        result, started = await self._generate(state, "discovery", message)
        try:
            spec = validate_spec(result)
            updated = copy.deepcopy(started)
            updated.update(spec=spec, active=None, last_error=None)
            return self.store.save(started, updated, "spec.proposed", {"spec_digest": content_digest(spec)})
        except (ValueError, TypeError, KeyError):
            self._failed(started, "expert_output_invalid")
            raise ValueError("expert_output_invalid") from None

    def approve(self, digest: str) -> JsonObject:
        state = self._idle()
        updated = copy.deepcopy(state)
        if state["status"] == "discovery":
            spec = validate_spec(state["spec"])
            require(not spec["questions"] and bool(spec["ports"]), "requirements_incomplete")
            require(digest == content_digest(spec), "reviewed_specification_digest_mismatch")
            updated.update(approved_spec=digest, status="building")
            return self.store.save(state, updated, "spec.approved", {"spec_digest": digest})
        require(state["status"] == "awaiting_acceptance", "project_not_ready_for_acceptance")
        require(state["simulation"]["input_digest"] == design_input_digest(state), "simulation_stale")
        expected = content_digest({"input": design_input_digest(state), "simulation": state["simulation"],
                                   "review": state["review"]})
        require(digest == expected, "reviewed_acceptance_digest_mismatch")
        updated["status"] = "accepted"
        return self.store.save(state, updated, "project.accepted", {"output_digest": expected})

    def revise(self) -> JsonObject:
        state = self._idle()
        updated = copy.deepcopy(state)
        # Old snapshots/blobs/evidence remain inspectable; none is silently re-approved.
        updated.update(status="discovery", stage=0, approved_spec=None, files={}, manifest=None,
                       simulation=None, review=None, repairs=0, last_error=None, summaries={})
        return self.store.save(state, updated, "spec.revised")

    def detail(self, level: str) -> JsonObject:
        require(level in ("brief", "normal", "detailed"), "detail_level_invalid")
        state = self._idle()
        updated = copy.deepcopy(state)
        updated["detail"] = level
        return self.store.save(state, updated, "detail.changed")

    def context(self, state: JsonObject, stage: str, message: str | None = None) -> JsonObject:
        files = self.store.contents(state)
        # The reference model is derived from requirements, not implementation.
        # DV sees model + verification plan, not RTL source text.
        if stage in ("architecture", "verification_plan", "reference_model"):
            files = {p: c for p, c in files.items() if p.startswith("docs/")}
        elif stage == "dv":
            files = {p: c for p, c in files.items() if not p.startswith("rtl/")}
        pack = {"schema": "openrtl.design-context.v1", "role": ROLES[stage], "stage": stage,
                "specification": state["spec"], "approved_spec_digest": state["approved_spec"],
                "artifacts": files, "artifact_digests": state["files"], "manifest": state["manifest"],
                "simulation": state["simulation"], "detail": state["detail"],
                "user_message": text(message, maximum=16000) if message else None}
        require(len(canonical(pack)) <= MAX_CONTEXT_BYTES, "expert_context_exceeds_bound")
        return pack

    async def _generate(self, state: JsonObject, stage: str,
                        message: str | None = None) -> tuple[JsonObject, JsonObject]:
        require(self.expert is not None, "expert_not_configured")
        require(state["calls"] < self.policy.max_calls, "expert_call_budget_exhausted")
        pack = self.context(state, stage, message)
        operation = uuid.uuid4().hex
        updated = copy.deepcopy(state)
        updated.update(active={"id": operation, "kind": "expert", "stage": stage}, calls=state["calls"] + 1)
        started = self.store.save(state, updated, "operation.started",
                                  {"operation_id": operation, "role": ROLES[stage],
                                   "context_digest": content_digest(pack)})
        assert self.expert is not None
        clock_start = time.monotonic()
        try:
            reply = await self.expert.generate(stage, pack, operation)
            require(isinstance(reply, ExpertReply), "expert_reply_invalid")
            require(len(canonical(reply.output)) <= MAX_CONTEXT_BYTES, "expert_output_exceeds_bound")
            metrics: JsonObject = {"operation_id": operation, "provider": text(reply.provider, maximum=128),
                                    "model": text(reply.model, maximum=128),
                                    "elapsed_ms": int((time.monotonic() - clock_start) * 1000)}
            for field, count in (("input_tokens", reply.input_tokens), ("output_tokens", reply.output_tokens)):
                require(count is None or type(count) is int and count >= 0, "expert_usage_invalid")
                if count is not None:
                    metrics[field] = count
            started = self.store.save(started, started, "operation.received", metrics)
            return reply.output, started
        except Exception:
            # Do not persist provider exception bodies, prompts, or authentication material.
            self._failed(started, "expert_invocation_failed")
            raise ValueError("expert_invocation_failed") from None

    def _failed(self, started: JsonObject, code: str) -> None:
        updated = copy.deepcopy(started)
        updated.update(active=None, last_error=code)
        self.store.save(started, updated, "operation.failed", {"error_code": code})

    async def advance(self) -> JsonObject:
        state = self._idle()
        require(state["approved_spec"] == content_digest(validate_spec(state["spec"])),
                "reviewed_specification_required")
        require(state["status"] in ("building", "needs_repair", "needs_signoff"), "stage_not_executable")
        if state["status"] == "building" and state["stage"] == len(STAGES):
            return await self._simulate(state)
        stage = ("diagnosis" if state["status"] == "needs_repair" else
                 "signoff" if state["status"] == "needs_signoff" else STAGES[state["stage"]])
        if stage == "diagnosis":
            require(state["repairs"] < self.policy.max_repairs, "repair_budget_exhausted")
        result, started = await self._generate(state, stage)
        try:
            if stage == "signoff":
                return self._signoff(started, result)
            contribution = object_value(result, {"summary", "files", "manifest"})
            text(contribution["summary"], maximum=8000)
            files = validate_files(contribution["files"], stage)
            previous_files = self.store.contents(started)
            if stage == "diagnosis":
                # M36 repairs RTL only. Revising a model/test requires explicit user revision.
                require(all(f["path"].startswith("rtl/") and f["path"] in previous_files for f in files),
                        "repair_outside_existing_rtl")
                require(any(previous_files[f["path"]] != f["content"] for f in files), "repair_has_no_change")
            else:
                require(not any(f["path"] in previous_files for f in files), "artifact_owner_conflict")
            combined = {**previous_files, **{f["path"]: f["content"] for f in files}}
            require(len(combined) <= 128 and sum(len(c.encode()) for c in combined.values()) <= MAX_CONTEXT_BYTES,
                    "project_artifacts_exceed_bound")
            updated = copy.deepcopy(started)
            if stage == "dv":
                updated["manifest"] = validate_manifest(contribution["manifest"], combined, started["spec"])
                require(any(p.startswith("model/test_") for p in combined), "independent_model_tests_missing")
            else:
                require(contribution["manifest"] is None, "stage_cannot_replace_simulation_manifest")
            updated.update(active=None, last_error=None)
            updated["summaries"][stage] = contribution["summary"]
            if stage == "diagnosis":
                updated.update(repairs=started["repairs"] + 1, status="building", review=None)
            else:
                updated["stage"] += 1
            return self.store.save(started, updated, "operation.completed",
                                   {"role": ROLES[stage], "output_digest": content_digest(result),
                                    "artifact_count": len(files)}, files=files)
        except (ValueError, KeyError, TypeError):
            self._failed(started, "expert_output_invalid")
            raise ValueError("expert_output_invalid") from None

    async def _simulate(self, state: JsonObject) -> JsonObject:
        require(self.simulator is not None, "isolated_simulator_not_configured")
        files = self.store.contents(state)
        manifest = validate_manifest(state["manifest"], files, state["spec"])
        operation = uuid.uuid4().hex
        updated = copy.deepcopy(state)
        updated["active"] = {"id": operation, "kind": "simulation", "stage": "simulation"}
        started = self.store.save(state, updated, "operation.started", {"operation_id": operation})
        assert self.simulator is not None
        try:
            report = await self.simulator.simulate(files, manifest, design_input_digest(state), operation)
            report = object_value(report, {"schema", "input_digest", "status", "evidence_kind", "run_id",
                                           "tests", "model_tests", "artifacts", "error_code", "diagnostics"})
            require(report["schema"] == "openrtl.design-simulation.v1" and
                    report["input_digest"] == design_input_digest(state) and report["run_id"] == operation,
                    "simulation_evidence_binding_invalid")
            require(report["evidence_kind"] == "isolated_verilator_cocotb", "real_simulation_evidence_required")
            require(report["status"] in ("passed", "failed"), "simulation_status_invalid")
            if report["status"] == "passed":
                require(set(report["tests"]) == set(manifest["expected_tests"]) and
                        type(report["model_tests"]) is int and report["model_tests"] > 0 and
                        bool(report["artifacts"]), "simulation_evidence_incomplete")
            require(len(canonical(report)) <= 64000, "simulation_report_too_large")
            updated = copy.deepcopy(started)
            updated.update(active=None, simulation=report, review=None, last_error=None,
                           status="needs_signoff" if report["status"] == "passed" else "needs_repair")
            return self.store.save(started, updated, "simulation.completed",
                                   {"run_id": operation, "evidence_kind": report["evidence_kind"],
                                    "output_digest": content_digest(report)})
        except Exception:
            self._failed(started, "simulation_execution_failed")
            raise ValueError("simulation_execution_failed") from None

    def _signoff(self, state: JsonObject, result: object) -> JsonObject:
        review = object_value(result, {"verdict", "summary", "findings"})
        require(review["verdict"] in ("accept", "revise"), "review_verdict_invalid")
        text(review["summary"], maximum=8000)
        for finding in sequence(review["findings"], maximum=32):
            text(finding, maximum=8000)
        require(review["verdict"] != "accept" or not review["findings"], "unresolved_signoff_findings")
        updated = copy.deepcopy(state)
        updated.update(active=None, review=review, last_error=None,
                       status="awaiting_acceptance" if review["verdict"] == "accept" else "review_blocked")
        return self.store.save(state, updated, "review.completed", {"output_digest": content_digest(review)})

    async def explain(self, message: str) -> JsonObject:
        state = self._idle()
        result, started = await self._generate(state, "explain", message)
        try:
            response = object_value(result, {"explanation", "references"})
            text(response["explanation"], maximum=16000)
            files = self.store.contents(started)
            for item in sequence(response["references"]):
                ref = object_value(item, {"path", "line"})
                require(ref["path"] in files and type(ref["line"]) is int and
                        1 <= ref["line"] <= len(files[ref["path"]].splitlines()), "explanation_anchor_invalid")
            updated = copy.deepcopy(started)
            updated.update(active=None, last_error=None)
            self.store.save(started, updated, "operation.completed", {"output_digest": content_digest(response)})
            return response
        except (ValueError, TypeError, KeyError):
            self._failed(started, "expert_output_invalid")
            raise ValueError("expert_output_invalid") from None
