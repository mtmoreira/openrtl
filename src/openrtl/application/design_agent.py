"""Review-gated, persistent orchestration; experts propose, tools supply evidence."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Protocol, cast
import uuid
import time

from openrtl.domain.design_session import (
    JsonObject, MAX_CONTEXT_BYTES, ROLES, STAGES, SESSION_SCHEMA, canonical, content_digest,
    object_value, require, sequence, text,
    validate_files, validate_manifest, validate_spec,
)
from openrtl.domain.design_delegation import specification_warnings, validate_delegated_spec, warning
from openrtl.domain.design_imports import baseline_plan, digest_value, validate_change_plan
from openrtl.domain.design_coaching import analysis_input_digest, validate_analysis, validate_intent, validate_proposal
from openrtl.domain.discovery_validation import DiscoveryValidationError
from openrtl.domain.design_discovery import (
    DISCOVERY_REPLY_SENTINEL, discovery_memory, validate_discovery,
)

DESIGN_CONTEXT_SCHEMA = "openrtl.design-context.v8"
DESIGN_PROMPT_VERSION = "openrtl.design.instructions.v10"
PREFERRED_CLARIFICATION_ROUNDS = 3
MAX_QUESTIONS_PER_ROUND = 3


def _discovery_correction_candidate(result: JsonObject) -> JsonObject:
    """Keep engineering structure while omitting non-authoritative provider prose."""
    candidate = copy.deepcopy(result)
    if "reply" in candidate:
        candidate["reply"] = DISCOVERY_REPLY_SENTINEL
    return candidate


class SessionStore(Protocol):
    root: Path
    @property
    def exclusive(self) -> bool: ...
    def operation_owned(self, operation_id: str) -> bool: ...
    def read(self) -> JsonObject: ...
    def contents(self, state: JsonObject) -> dict[str, str]: ...
    def import_contents(self, state: JsonObject) -> dict[str, str]: ...
    def historical_state(self, revision: int) -> JsonObject: ...
    def events(self) -> tuple[JsonObject, ...]: ...
    def events_after(self, cursor: int, *, limit: int = 64) -> tuple[JsonObject, ...]: ...
    def measurement(self, state: JsonObject) -> JsonObject: ...
    def save(self, previous: JsonObject, updated: JsonObject, event: str,
             fields: JsonObject | None = None, *, files: list[JsonObject] | None = None,
             imports: list[JsonObject] | None = None) -> JsonObject: ...


@dataclass(frozen=True)
class ExpertReply:
    output: JsonObject
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None


class DesignExpert(Protocol):
    async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply: ...


class DesignTraceRecorder(Protocol):
    """Explicit private project capture, separate from standardized events."""

    def record(self, operation_id: str, category: str, payload: JsonObject) -> None: ...
    def records(self, operation_ids: set[str]) -> list[JsonObject]: ...
    def status(self) -> JsonObject: ...


class DesignSimulator(Protocol):
    async def simulate(self, files: dict[str, str], manifest: JsonObject,
                       input_digest: str, operation_id: str) -> JsonObject: ...


class DesignRecovery(Protocol):
    async def abandon(self, operation_id: str, input_digest: str) -> None: ...


@dataclass(frozen=True)
class DesignPolicy:
    max_calls: int = 40
    max_repairs: int = 2
    provider_model: str | None = None
    max_output_tokens: int = 16000
    max_discovery_corrections: int = 2

    def __post_init__(self) -> None:
        require(type(self.max_calls) is int and 1 <= self.max_calls <= 200, "call_budget_invalid")
        require(type(self.max_repairs) is int and 0 <= self.max_repairs <= 5, "repair_budget_invalid")
        require(type(self.max_discovery_corrections) is int and
                0 <= self.max_discovery_corrections <= 2, "discovery_correction_budget_invalid")
        require(type(self.max_output_tokens) is int and 256 <= self.max_output_tokens <= 32768,
                "output_budget_invalid")
        if self.provider_model is not None:
            from openrtl.domain.provider_controls import (
                request_reserve_nano, selector_model, selector_provider, validate_provider_selector,
            )
            selector = validate_provider_selector(self.provider_model)
            if selector_provider(selector) == "openai":
                request_reserve_nano(selector_model(selector), self.max_output_tokens)


def design_input_digest(state: JsonObject) -> str:
    return content_digest({"spec": state["approved_spec"], "files": state["files"],
                           "manifest": state["manifest"]})


class DesignAgent:
    def __init__(self, store: SessionStore, expert: DesignExpert | None = None,
                 simulator: DesignSimulator | None = None, policy: DesignPolicy = DesignPolicy(),
                 recovery: DesignRecovery | None = None, progress: Callable[[JsonObject], None] | None = None,
                 trace_store: DesignTraceRecorder | None = None) -> None:
        self.store, self.expert, self.simulator, self.policy = store, expert, simulator, policy
        self.recovery = recovery
        self.progress = progress
        self.trace_store = trace_store

    def configure_provider(self, model: str, limit_nano_usd: int | None) -> JsonObject:
        from openrtl.domain.provider_controls import selector_provider, validate_provider_selector
        model = validate_provider_selector(model)
        if selector_provider(model) == "openai":
            require(type(limit_nano_usd) is int and
                    10_000_000 <= limit_nano_usd <= 1_000_000_000_000,
                    "provider_spend_limit_invalid")
        else:
            require(limit_nano_usd is None, "provider_spend_not_applicable")
        state = self._idle()
        require(not any(row["phase"] in ("queued", "active", "cancellation_requested")
                        for row in state["workspace_operations"].values()),
                "workspace_writer_busy_or_unreconciled")
        require(not state["provider"]["uncertain"], "provider_spend_uncertain")
        if limit_nano_usd is not None:
            require(limit_nano_usd >= state["provider"]["spent_nano_usd"],
                    "provider_spend_limit_below_used")
        selected = replace(self.policy, provider_model=model)
        if state["provider"]["model"] == model and state["provider"]["limit_nano_usd"] == limit_nano_usd:
            self.policy = selected
            return state
        updated = copy.deepcopy(state)
        updated["provider"].update(model=model, limit_nano_usd=limit_nano_usd)
        event_fields: JsonObject = {"model": model}
        if limit_nano_usd is not None:
            event_fields["limit_nano_usd"] = limit_nano_usd
        saved = self.store.save(state, updated, "provider.configured", event_fields)
        self.policy = selected
        return saved

    def reconcile_provider_spend(self, expected_revision: int, decision: str) -> JsonObject:
        require(decision == "retain_full_reservation", "provider_reconciliation_decision_invalid")
        state = self._idle()
        require(type(expected_revision) is int and state["revision"] == expected_revision,
                "workspace_revision_stale")
        require(not any(row["phase"] in ("queued", "active", "cancellation_requested")
                        for row in state["workspace_operations"].values()),
                "workspace_writer_busy_or_unreconciled")
        require(state["provider"]["uncertain"] and state["provider"]["pending"] is None,
                "provider_reconciliation_not_required")
        updated = copy.deepcopy(state)
        updated["provider"]["uncertain"] = False
        return self.store.save(state, updated, "provider.spend_reconciled",
                               {"model": state["provider"]["model"],
                                "retained_nano_usd": state["provider"]["spent_nano_usd"]})

    def _idle(self) -> JsonObject:
        state = self.store.read()
        require(state["schema"] == SESSION_SCHEMA, "explicit_session_upgrade_required")
        require(state["active"] is None, "interrupted_operation_requires_reconciliation")
        return state

    def _limits(self, state: JsonObject) -> JsonObject:
        selected = {"max_calls": self.policy.max_calls, "max_repairs": self.policy.max_repairs}
        if state["limits"] is not None:
            selected = {k: min(v, state["limits"][k]) for k, v in selected.items()}
        if selected == state["limits"]:
            return state
        updated = copy.deepcopy(state)
        updated["limits"] = selected
        return self.store.save(state, updated, "limits.bound")

    def acknowledge_warning(self, warning_id: str) -> JsonObject:
        state = self._idle()
        updated = copy.deepcopy(state)
        found = [w for w in updated["warnings"] if w["id"] == warning_id]
        require(len(found) == 1, "warning_not_found")
        if found[0]["review"] == "acknowledged":
            return state
        found[0]["review"] = "acknowledged"
        return self.store.save(state, updated, "warning.reviewed", {"warning_id": warning_id})

    async def abandon(self, operation_id: str) -> JsonObject:
        state = self.store.read()
        require(state["schema"] == SESSION_SCHEMA, "explicit_session_upgrade_required")
        require(self.store.exclusive and state["active"] is not None and
                state["active"]["id"] == operation_id, "recovery_operation_mismatch")
        require(not self.store.operation_owned(operation_id), "operation_owned_by_current_writer")
        if state["active"]["kind"] == "simulation":
            require(self.recovery is not None, "explicit_runtime_reconciliation_required")
            assert self.recovery is not None
            await self.recovery.abandon(operation_id, design_input_digest(state))
        updated = copy.deepcopy(state)
        updated.update(active=None, last_error="interrupted_operation_abandoned")
        if state["active"]["kind"] == "expert" and updated["provider"]["pending"] is not None:
            updated["provider"]["pending"] = None
            updated["provider"]["uncertain"] = True
        updated["warnings"].append(warning(
            "interrupted_operation", operation_id, content_digest(state["spec"]),
            "Interrupted operation explicitly abandoned; its output is not accepted as evidence.",
            "External completion or cost may be unknown. Consumed calls are not refunded."))
        return self.store.save(state, updated, "operation.abandoned", {"operation_id": operation_id})

    def propose(self, specification: object) -> JsonObject:
        """Allow an explicit structured specification without requiring a model."""
        state = self._idle()
        require(state["status"] == "discovery", "specification_already_approved")
        spec = validate_spec(specification)
        updated = copy.deepcopy(state)
        updated["spec"] = spec
        # An explicit user-supplied draft can reopen a decision. Its question
        # records take precedence over stale memory at the same IDs; immutable
        # snapshots retain the earlier decision and its provenance.
        reopened = {row["id"] for row in spec["questions"]}
        updated["engineering_memory"] = [row for row in updated["engineering_memory"]
                                         if row["id"] not in reopened]
        self._record_spec_warnings(updated)
        return self.store.save(state, updated, "spec.proposed", {"spec_digest": content_digest(spec)})

    @staticmethod
    def _record_spec_warnings(updated: JsonObject) -> None:
        seed = updated["delegation"]["seed_spec"] if updated["delegation"] else None
        existing = {w["id"] for w in updated["warnings"]}
        updated["warnings"].extend(w for w in specification_warnings(seed, updated["spec"]) if w["id"] not in existing)

    async def discuss(self, message: str, *, emit_reply: Callable[[str], None] | None = None) -> JsonObject:
        state = self._idle()
        require(state["status"] == "discovery", "use_explain_or_explicit_revision")
        timeout = getattr(self.expert, "timeout_seconds", 120)
        deadline = time.monotonic() + timeout
        correction = None
        prior_rounds = self._clarification_rounds()
        question_history = self._question_history()
        for attempt in range(self.policy.max_discovery_corrections + 1):
            # Every correction passes the normal accounting, provider-identity,
            # capture and call-budget gates. Nothing replays after a restart.
            result, started = await self._generate(
                state, "discovery", message, discovery_correction=correction,
                discovery_deadline=deadline)
            try:
                candidate = validate_discovery(result, state, message,
                    clarification_rounds=prior_rounds, question_history=question_history,
                    require_plan=getattr(self.expert, "discovery_contract_version", None) in ("v8", "v9", "v10"),
                    require_hardware_specification=(
                        getattr(self.expert, "specification_contract_version", None) == "v1"))
            except (ValueError, TypeError, KeyError) as error:
                failure = DiscoveryValidationError(str(error) if type(error) is ValueError else None)
                self._failed(started, "expert_output_invalid", validation_code=failure.validation_code)
                # Security-boundary violations and unclassified failures are not
                # fed back as repair candidates. Transport errors never get here.
                if (not failure.validation_code or failure.category_code == "expert_output_memory_invalid" or
                        attempt == self.policy.max_discovery_corrections or time.monotonic() >= deadline):
                    raise failure from None
                correction = {"attempt": attempt + 1, "validation_code": failure.validation_code,
                              "candidate": _discovery_correction_candidate(result)}
                state = self._idle()
                continue
            break
        updated = copy.deepcopy(started)
        updated.update(active=None, last_error=None, engineering_memory=candidate.memory)
        fields: JsonObject = {"role": ROLES["discovery"],
                              "clarification_round": prior_rounds + (1 if candidate.questions else 0),
                              "question_count": len(candidate.questions)}
        event = "operation.completed"
        if candidate.specification is not None:
            updated["spec"] = candidate.specification
            self._record_spec_warnings(updated)
            fields["spec_digest"] = content_digest(candidate.specification)
            event = "spec.proposed"
        saved = self.store.save(started, updated, event, fields)
        if candidate.reply is not None and emit_reply is not None:
            emit_reply(candidate.reply)
        return saved

    def _clarification_rounds(self) -> int:
        return sum(1 for row in self.store.events()
                   if row["event"] in ("operation.completed", "spec.proposed") and
                   row["fields"].get("role") == ROLES["discovery"] and
                   ("question_count" not in row["fields"] or row["fields"]["question_count"] > 0))

    def _question_history(self) -> list[JsonObject]:
        """Project only accepted question records, never private capture/transcripts.

        A resolved question's original text remains in its immutable snapshot.
        Reusing those snapshots avoids rewriting legacy states or losing exact
        question identity when a memory entry becomes a decision.
        """
        questions: dict[tuple[str, str], JsonObject] = {}
        for event in self.store.events():
            if event["event"] != "spec.proposed" and not (
                    event["event"] == "operation.completed" and
                    event["fields"].get("role") == ROLES["discovery"]):
                continue
            state = self.store.historical_state(event["sequence"])
            for row in discovery_memory(state):
                if row["kind"] == "question":
                    questions[(row["id"], row["text"])] = {"id": row["id"], "text": row["text"]}
                    require(len(questions) <= 600, "expert_question_history_exceeds_bound")
        return list(questions.values())

    def approve(self, digest: str, *, delegated: bool = False) -> JsonObject:
        state = self._idle()
        updated = copy.deepcopy(state)
        authority = "delegated" if delegated else "user"
        if delegated:
            require(state["delegation"] is not None, "explicit_delegation_required")
            require(time.time_ns() < state["delegation"]["deadline_ns"], "delegation_deadline_exhausted")
        if state["status"] == "discovery":
            spec = validate_spec(state["spec"])
            if "readiness" in spec:
                from openrtl.domain.design_readiness import require_ready
                require_ready(spec)
            require(not spec["questions"] and bool(spec["ports"]), "requirements_incomplete")
            require(digest == content_digest(spec), "reviewed_specification_digest_mismatch")
            seed = state["delegation"]["seed_spec"] if delegated else None
            if delegated:
                validate_delegated_spec(validate_spec(seed), spec, state["delegation"]["authorization"])
            existing = {w["id"] for w in updated["warnings"]}
            for row in specification_warnings(seed, spec):
                if not delegated:
                    row["review"] = "acknowledged"
                if row["id"] not in existing:
                    updated["warnings"].append(row)
                elif not delegated:
                    for prior in updated["warnings"]:
                        if prior["id"] == row["id"]:
                            prior["review"] = "acknowledged"
            updated.update(approved_spec=digest, status="building", approval_mode=authority)
            return self.store.save(state, updated, "spec.approved", {"spec_digest": digest, "authority": authority})
        require(state["status"] == "awaiting_acceptance", "project_not_ready_for_acceptance")
        require(state["simulation"]["input_digest"] == design_input_digest(state), "simulation_stale")
        expected = content_digest({"input": design_input_digest(state), "simulation": state["simulation"],
                                   "review": state["review"]})
        require(digest == expected, "reviewed_acceptance_digest_mismatch")
        if delegated:
            require(state["delegation"]["authorization"]["allow_final_acceptance"], "final_acceptance_not_delegated")
        updated["status"] = "accepted"
        updated["acceptance_mode"] = authority
        return self.store.save(state, updated, "project.accepted", {"output_digest": expected, "authority": authority})

    def revise(self) -> JsonObject:
        state = self._idle()
        updated = copy.deepcopy(state)
        # Old snapshots/blobs/evidence remain inspectable; none is silently re-approved.
        updated.update(status="discovery", stage=0, approved_spec=None, files={}, manifest=None,
                       simulation=None, review=None, last_error=None, summaries={},
                       delegation=None, approval_mode=None, acceptance_mode=None, baseline=None, change_plan=None,
                       proposal=None, analysis=None)
        return self.store.save(state, updated, "spec.revised")

    def detail(self, level: str) -> JsonObject:
        require(level in ("brief", "normal", "detailed"), "detail_level_invalid")
        state = self._idle()
        updated = copy.deepcopy(state)
        updated["detail"] = level
        return self.store.save(state, updated, "detail.changed")

    def plan_baseline(self, manifest: object) -> JsonObject:
        state = self._idle()
        require(state["status"] == "discovery", "baseline_requires_discovery")
        self.store.import_contents(state)
        return baseline_plan(state["spec"], state["imports"], manifest)

    def approve_baseline(self, manifest: object, approved_digest: str) -> JsonObject:
        state = self._idle()
        plan = self.plan_baseline(manifest)
        require(content_digest(plan) == approved_digest, "reviewed_baseline_digest_mismatch")
        contents = self.store.import_contents(state)
        updated = copy.deepcopy(state)
        self._record_spec_warnings(updated)
        for row in updated["warnings"]:
            if row["spec_digest"] == content_digest(state["spec"]):
                row["review"] = "acknowledged"
        updated.update(baseline=plan, approved_spec=content_digest(state["spec"]), approval_mode="user",
                       delegation=None, status="building", stage=len(STAGES), manifest=plan["manifest"],
                       simulation=None, review=None, acceptance_mode=None, last_error=None, proposal=None, analysis=None)
        return self.store.save(state, updated, "baseline.approved", {"output_digest": approved_digest, "authority": "user"},
                               files=[{"path": p, "content": c} for p, c in contents.items()])

    def plan_change(self, request: object) -> JsonObject:
        state = self._idle()
        require(state["stage"] == len(STAGES) and state["manifest"] is not None, "change_requires_complete_baseline")
        selected = object_value(request, {"specification", "stage_paths", "manifest"})
        return validate_change_plan({"schema": "openrtl.design-change.v1", "base_input_digest": design_input_digest(state),
                                     "base_files": state["files"], **selected})

    def approve_change(self, value: object, approved_digest: str) -> JsonObject:
        state = self._idle()
        plan = validate_change_plan(value)
        require(content_digest(plan) == approved_digest, "reviewed_change_digest_mismatch")
        if "readiness" in plan["specification"]:
            from openrtl.domain.design_readiness import require_ready
            require_ready(plan["specification"])
        require(state["stage"] == len(STAGES) and state["manifest"] is not None and
                plan["base_input_digest"] == design_input_digest(state) and plan["base_files"] == state["files"],
                "change_baseline_stale")
        updated = copy.deepcopy(state)
        updated.update(spec=plan["specification"], approved_spec=content_digest(plan["specification"]),
                       approval_mode="user", acceptance_mode=None, delegation=None, change_plan=plan,
                       status="building", stage=0, manifest=None, simulation=None, review=None, last_error=None, summaries={},
                       proposal=None, analysis=None)
        self._record_spec_warnings(updated)
        for row in updated["warnings"]:
            if row["spec_digest"] == updated["approved_spec"]:
                row["review"] = "acknowledged"
        return self.store.save(state, updated, "change.approved", {"output_digest": approved_digest, "authority": "user"})

    def pace(self, selected: str) -> JsonObject:
        require(selected in ("stage", "continuous"), "coaching_pace_invalid")
        state = self._idle()
        updated = copy.deepcopy(state)
        updated["pace"] = selected
        return self.store.save(state, updated, "pace.changed")

    async def propose_improvement(self, message: str, *, intent: str = "feature") -> JsonObject:
        state = self._idle()
        require(intent in ("feature", "dv", "optimization"), "change_intent_invalid")
        require(state["stage"] == len(STAGES) and state["manifest"] is not None, "change_requires_complete_baseline")
        result, started = await self._generate(state, "change_planning", message, intent=intent)
        try:
            output = object_value(result, {"summary", "specification", "stage_paths", "manifest"})
            plan = validate_change_plan({"schema": "openrtl.design-change.v1", "base_input_digest": design_input_digest(state),
                                         "base_files": state["files"], "specification": output["specification"],
                                        "stage_paths": output["stage_paths"], "manifest": output["manifest"]},
                                        require_hardware_specification=(
                                            getattr(self.expert, "specification_contract_version", None) == "v1" and
                                            (intent == "feature" or
                                             "hardware_specification" in state["spec"])))
            validate_intent(plan, intent, state)
            proposal = validate_proposal({"schema": "openrtl.design-change-proposal.v1", "intent": intent,
                                          "summary": output["summary"], "plan": plan, "status": "awaiting_review"})
            updated = copy.deepcopy(started)
            updated.update(active=None, last_error=None, proposal=proposal)
            self.store.save(started, updated, "change.proposed", {"output_digest": content_digest(proposal)})
            return proposal
        except (ValueError, KeyError, TypeError):
            self._failed(started, "expert_output_invalid")
            raise ValueError("expert_output_invalid") from None

    def approve_improvement(self, digest: str) -> JsonObject:
        state = self._idle()
        proposal = validate_proposal(state["proposal"])
        require(content_digest(proposal) == digest, "reviewed_proposal_digest_mismatch")
        validate_intent(proposal["plan"], proposal["intent"], state)
        # M38 independently rechecks base input and file hashes before mutation.
        return self.approve_change(proposal["plan"], content_digest(proposal["plan"]))

    async def analyze(self, message: str) -> JsonObject:
        state = self._idle()
        require(state["spec"] is not None and bool(state["files"] or state["imports"]), "analysis_inputs_missing")
        result, started = await self._generate(state, "analyze", message)
        try:
            output = object_value(result, {"summary", "findings"})
            report = validate_analysis({"schema": "openrtl.design-analysis.v1", "input_digest": analysis_input_digest(state), **output})
            files = {**self.store.import_contents(state), **self.store.contents(state)}
            requirements = {r["id"] for r in state["spec"]["requirements"]}
            for finding in report["findings"]:
                require(finding["requirement_id"] in requirements, "analysis_requirement_unknown")
                if finding["basis"] == "simulation":
                    require(state["simulation"] is not None and state["simulation"]["input_digest"] == design_input_digest(state),
                            "analysis_simulation_evidence_missing")
                require(finding["basis"] == "hypothesis" or bool(finding["references"]), "analysis_anchors_required")
                for ref in finding["references"]:
                    require(ref["path"] in files and ref["line"] <= len(files[ref["path"]].splitlines()), "analysis_anchor_invalid")
            updated = copy.deepcopy(started)
            updated.update(active=None, last_error=None, analysis=report)
            self.store.save(started, updated, "analysis.recorded", {"output_digest": content_digest(report)})
            return report
        except (ValueError, KeyError, TypeError):
            self._failed(started, "expert_output_invalid")
            raise ValueError("expert_output_invalid") from None

    def compare(self, baseline_revision: int) -> JsonObject:
        from openrtl.application.design_comparison import compare_runs
        state = self._idle()
        require(baseline_revision < state["revision"], "comparison_requires_prior_revision")
        before = self.store.historical_state(baseline_revision)
        return compare_runs(before, state, self.store.measurement(before), self.store.measurement(state))

    def context(self, state: JsonObject, stage: str, message: str | None = None, *, intent: str | None = None) -> JsonObject:
        files = self.store.contents(state)
        references = {p: c for p, c in self.store.import_contents(state).items() if p not in files}
        # The reference model is derived from requirements, not implementation.
        # DV sees model + verification plan, not RTL source text.
        if stage in ("architecture", "verification_plan", "reference_model"):
            files = {p: c for p, c in files.items() if p.startswith("docs/")}
            references = {p: c for p, c in references.items() if p.startswith("docs/")}
        elif stage == "dv":
            files = {p: c for p, c in files.items() if not p.startswith("rtl/")}
            references = {p: c for p, c in references.items() if not p.startswith("rtl/")}
        prior_rounds = self._clarification_rounds() if stage == "discovery" else 0
        pack = {"schema": DESIGN_CONTEXT_SCHEMA, "role": ROLES[stage], "stage": stage,
                "specification": state["spec"], "approved_spec_digest": state["approved_spec"],
                "engineering_memory": discovery_memory(state) if stage == "discovery" else
                                      state.get("engineering_memory", []),
                "artifacts": files, "artifact_digests": state["files"], "manifest": state["manifest"],
                "reference_artifacts": references, "reference_status": "untrusted_imports_not_run_evidence",
                "change_scope": state["change_plan"],
                "improvement_intent": intent, "pace": state["pace"],
                "simulation": state["simulation"], "detail": state["detail"],
                "user_message": text(message, maximum=16000) if message else None}
        if stage == "discovery":
            pack["question_history"] = self._question_history()
            pack["conversation_policy"] = {
                "clarification_round": prior_rounds + 1,
                "preferred_round_limit": PREFERRED_CLARIFICATION_ROUNDS,
                "max_questions_this_round": MAX_QUESTIONS_PER_ROUND,
                "existing_question_ids": [row["id"] for row in pack["engineering_memory"]
                                          if row["kind"] == "question"],
                "after_preferred_limit": "ask_only_for_a_concrete_correctness_or_interface_blocker",
            }
        require(len(canonical(pack)) <= MAX_CONTEXT_BYTES, "expert_context_exceeds_bound")
        return pack

    async def _generate(self, state: JsonObject, stage: str,
                        message: str | None = None, *, intent: str | None = None,
                        discovery_correction: JsonObject | None = None,
                        discovery_deadline: float | None = None) -> tuple[JsonObject, JsonObject]:
        require(self.expert is not None, "expert_not_configured")
        state = self._limits(state)
        require(state["calls"] < state["limits"]["max_calls"], "expert_call_budget_exhausted")
        pack = self.context(state, stage, message, intent=intent)
        if discovery_correction is not None:
            pack["discovery_correction"] = discovery_correction
        if discovery_deadline is not None:
            pack["discovery_deadline"] = discovery_deadline
        require(len(canonical(pack)) <= MAX_CONTEXT_BYTES, "expert_context_exceeds_bound")
        reserve = 0
        selected_provider = None
        selected_model = None
        if self.policy.provider_model is not None:
            from openrtl.domain.provider_controls import (
                request_reserve_nano, selector_model, selector_provider,
            )
            budget = state["provider"]
            selected_provider = selector_provider(self.policy.provider_model)
            selected_model = selector_model(self.policy.provider_model)
            require(budget["model"] == self.policy.provider_model, "provider_spend_not_configured")
            require(not budget["uncertain"] and budget["pending"] is None,
                    "provider_spend_uncertain")
            if selected_provider == "openai":
                require(budget["limit_nano_usd"] is not None, "provider_spend_not_configured")
                reserve = request_reserve_nano(selected_model, self.policy.max_output_tokens)
                require(budget["spent_nano_usd"] + reserve <= budget["limit_nano_usd"],
                        "provider_spend_budget_exhausted")
            else:
                require(budget["limit_nano_usd"] is None, "provider_spend_not_applicable")
        operation = uuid.uuid4().hex
        updated = copy.deepcopy(state)
        updated.update(active={"id": operation, "kind": "expert", "stage": stage}, calls=state["calls"] + 1)
        if reserve:
            updated["provider"]["spent_nano_usd"] += reserve
            updated["provider"]["pending"] = {"operation_id": operation,
                                                "reserved_nano_usd": reserve,
                                                "model": self.policy.provider_model}
        start_fields: JsonObject = {"operation_id": operation, "role": ROLES[stage],
                                   "context_digest": content_digest({
                                       key: value for key, value in pack.items()
                                       if key != "discovery_deadline"}),
                                   "context_schema": DESIGN_CONTEXT_SCHEMA,
                                   "prompt_version": DESIGN_PROMPT_VERSION,
                                   "tool_calls": 0, "shell_commands": 0}
        clients = [identifier for identifier, row in state["workspace_operations"].items()
                   if row["phase"] == "active"]
        if len(clients) == 1:
            start_fields["client_operation_id"] = clients[0]
        started = self.store.save(state, updated, "operation.started", start_fields)
        assert self.expert is not None
        clock_start = time.monotonic()
        try:
            if self.trace_store is not None:
                self.trace_store.record(operation, "user_input", {"message": message, "stage": stage})
            from openrtl.application.design_diagnostics import observed
            reply = await observed(self.expert.generate(stage, pack, operation), started, self.progress)
            require(isinstance(reply, ExpertReply), "expert_reply_invalid")
            if self.trace_store is not None:
                self.trace_store.record(operation, "assistant_output", {"output": reply.output})
            require(len(canonical(reply.output)) <= MAX_CONTEXT_BYTES, "expert_output_exceeds_bound")
            metrics: JsonObject = {"operation_id": operation, "provider": text(reply.provider, maximum=128),
                                    "model": text(reply.model, maximum=128),
                                    "elapsed_ms": int((time.monotonic() - clock_start) * 1000)}
            for field, count in (("input_tokens", reply.input_tokens), ("output_tokens", reply.output_tokens),
                                 ("cached_input_tokens", reply.cached_input_tokens),
                                 ("reasoning_tokens", reply.reasoning_tokens)):
                require(count is None or type(count) is int and count >= 0, "expert_usage_invalid")
                if count is not None:
                    metrics[field] = count
            received = copy.deepcopy(started)
            if selected_provider is not None:
                require(reply.provider == selected_provider and reply.model == selected_model,
                        "provider_result_identity_invalid")
            if reserve:
                from openrtl.domain.provider_controls import estimated_cost_nano
                require(reply.input_tokens is not None and reply.output_tokens is not None,
                        "provider_usage_unavailable")
                actual = estimated_cost_nano(reply.model, cast(int, reply.input_tokens),
                                             cast(int, reply.output_tokens))
                require(actual <= reserve, "provider_spend_reserve_exceeded")
                received["provider"]["spent_nano_usd"] -= reserve - actual
                received["provider"]["pending"] = None
                metrics["estimated_cost_nano_usd"] = actual
            started = self.store.save(started, received, "operation.received", metrics)
            return reply.output, started
        except Exception as error:
            # Do not persist provider exception bodies, prompts, or authentication material.
            if self.policy.provider_model is not None:
                from openrtl.application.provider_failures import classify_provider_failure
                code = classify_provider_failure(error)
            else:
                code = "expert_invocation_failed"
            elapsed_ms = int((time.monotonic() - clock_start) * 1000)
            if self.trace_store is not None:
                self.trace_store.record(operation, "provider_failure",
                                        {"error_code": code, "elapsed_ms": elapsed_ms,
                                         "stage": stage})
            self._failed(started, code, elapsed_ms=elapsed_ms)
            raise ValueError(code) from None

    def _failed(self, started: JsonObject, code: str, *, elapsed_ms: int | None = None,
                validation_code: str | None = None) -> None:
        updated = copy.deepcopy(started)
        updated.update(active=None, last_error=code)
        if updated["provider"]["pending"] is not None:
            updated["provider"]["pending"] = None
            updated["provider"]["uncertain"] = True
        fields: JsonObject = {"error_code": code}
        if validation_code is not None:
            fields["validation_code"] = validation_code
        if elapsed_ms is not None:
            fields["elapsed_ms"] = elapsed_ms
        self.store.save(started, updated, "operation.failed", fields)

    async def advance(self) -> JsonObject:
        state = self._idle()
        require(state["approved_spec"] == content_digest(validate_spec(state["spec"])),
                "reviewed_specification_required")
        require(state["status"] in ("building", "needs_repair", "needs_signoff"), "stage_not_executable")
        if state["status"] == "building" and state["stage"] == len(STAGES):
            return await self._simulate(state)
        stage = ("diagnosis" if state["status"] == "needs_repair" else
                 "signoff" if state["status"] == "needs_signoff" else STAGES[state["stage"]])
        if state["change_plan"] is not None and stage in STAGES and not state["change_plan"]["stage_paths"][stage]:
            updated = copy.deepcopy(state)
            updated["stage"] += 1
            updated["summaries"][stage] = "Artifacts retained by the reviewed change scope; fresh simulation still required."
            if stage == "dv":
                updated["manifest"] = validate_manifest(state["change_plan"]["manifest"], self.store.contents(state), state["spec"])
            return self.store.save(state, updated, "stage.reused", {"role": ROLES[stage]})
        if stage == "diagnosis":
            state = self._limits(state)
            require(state["repairs"] < state["limits"]["max_repairs"], "repair_budget_exhausted")
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
                if started["change_plan"] is not None:
                    allowed = {p for s in ("rtl", "assertions") for p in started["change_plan"]["stage_paths"][s]}
                    require(all(f["path"] in allowed for f in files), "repair_outside_reviewed_change_scope")
            elif started["change_plan"] is not None:
                require({f["path"] for f in files} == set(started["change_plan"]["stage_paths"][stage]),
                        "change_stage_paths_differ_from_review")
            else:
                require(not any(f["path"] in previous_files for f in files), "artifact_owner_conflict")
            combined = {**previous_files, **{f["path"]: f["content"] for f in files}}
            require(len(combined) <= 128 and sum(len(c.encode()) for c in combined.values()) <= MAX_CONTEXT_BYTES,
                    "project_artifacts_exceed_bound")
            updated = copy.deepcopy(started)
            if stage == "dv":
                updated["manifest"] = validate_manifest(contribution["manifest"], combined, started["spec"])
                if started["change_plan"] is not None:
                    require(updated["manifest"] == started["change_plan"]["manifest"], "change_manifest_differs_from_review")
                require(any(p.startswith("model/test_") for p in combined), "independent_model_tests_missing")
            else:
                require(contribution["manifest"] is None, "stage_cannot_replace_simulation_manifest")
            updated.update(active=None, last_error=None, analysis=None)
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
        start_fields: JsonObject = {"operation_id": operation, "role": "simulation"}
        clients = [identifier for identifier, row in state["workspace_operations"].items()
                   if row["phase"] == "active"]
        if len(clients) == 1:
            start_fields["client_operation_id"] = clients[0]
        started = self.store.save(state, updated, "operation.started", start_fields)
        assert self.simulator is not None
        clock_start = time.monotonic()
        try:
            from openrtl.application.design_diagnostics import observed
            report = await observed(self.simulator.simulate(files, manifest, design_input_digest(state), operation), started, self.progress)
            fields = {"schema", "input_digest", "status", "evidence_kind", "run_id",
                      "tests", "model_tests", "artifacts", "error_code", "diagnostics"}
            if report.get("schema") == "openrtl.design-simulation.v2":
                fields.add("runtime")
            report = object_value(report, fields)
            require(report["schema"] in ("openrtl.design-simulation.v1", "openrtl.design-simulation.v2") and
                    report["input_digest"] == design_input_digest(state) and report["run_id"] == operation,
                    "simulation_evidence_binding_invalid")
            if report["schema"] == "openrtl.design-simulation.v2":
                runtime = object_value(report["runtime"], {"profile_digest", "runner_digest"})
                digest_value(runtime["profile_digest"])
                digest_value(runtime["runner_digest"])
            require(report["evidence_kind"] == "isolated_verilator_cocotb", "real_simulation_evidence_required")
            require(report["status"] in ("passed", "failed"), "simulation_status_invalid")
            if report["status"] == "passed":
                require(set(report["tests"]) == set(manifest["expected_tests"]) and
                        type(report["model_tests"]) is int and report["model_tests"] > 0 and
                        bool(report["artifacts"]), "simulation_evidence_incomplete")
            require(len(canonical(report)) <= 64000, "simulation_report_too_large")
            updated = copy.deepcopy(started)
            updated.update(active=None, simulation=report, review=None, last_error=None, analysis=None,
                           status="needs_signoff" if report["status"] == "passed" else "needs_repair")
            return self.store.save(started, updated, "simulation.completed",
                                   {"run_id": operation, "evidence_kind": report["evidence_kind"],
                                    "elapsed_ms": int((time.monotonic() - clock_start) * 1000),
                                    "output_digest": content_digest(report)})
        except Exception:
            # A daemon may outlive the client. Retain the intent until explicit
            # recovery proves that its exact owned container has been removed.
            updated = copy.deepcopy(started)
            updated["last_error"] = "simulation_execution_failed"
            self.store.save(started, updated, "simulation.failed", {"error_code": "simulation_execution_failed"})
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
            files = {**self.store.import_contents(started), **self.store.contents(started)}
            require(not files or bool(response["references"]), "source_explanation_requires_anchors")
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
