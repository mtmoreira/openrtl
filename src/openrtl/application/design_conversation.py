"""Review-bound conversation operations; language never grants tool capability."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from openrtl.application.design_agent import DesignAgent, design_input_digest
from openrtl.domain.design_readiness import require_ready
from openrtl.domain.design_session import JsonObject, content_digest, require, text


def route(message: str, state: JsonObject) -> str:
    phrase = text(message, maximum=16000).casefold().strip().rstrip(".! ")
    exact = {"review": "review", "show the review": "review", "show readiness": "review",
             "approve this specification": "approve-specification", "approve this change": "approve-change",
             "accept this design": "approve-acceptance", "continue": "continue", "next step": "next",
             "revoke provider permission": "revoke-provider", "revoke simulation permission": "revoke-simulation",
             "revise the specification": "revise", "quit": "quit"}
    if phrase in exact:
        return exact[phrase]
    # Unqualified agreement, negation or mixed approval requests need clarification.
    if phrase in ("yes", "ok", "okay", "approve", "looks good", "go ahead") or re.search(
            r"\b(approve|accept|permission|authorize)\b|\b(don't|do not|never)\s+(continue|run|start|execute|change)\b", phrase):
        return "clarify"
    if state["status"] != "discovery":
        request = re.sub(r"^(?:(?:can|could|would) you (?:please )?|i (?:want|would like) (?:you )?to |please )", "", phrase)
        if request.startswith(("improve verification", "improve the tests", "add tests", "improve dv")):
            return "propose-dv"
        if request.startswith(("add ", "change ", "modify ", "implement ", "make it ")):
            return "propose-feature"
        if request.startswith(("optimize ", "try an optimization")):
            return "propose-optimization"
    if phrase.startswith(("explain", "why ", "how ", "what ", "describe ")) or phrase.endswith("?"):
        return "discuss" if state["status"] == "discovery" and not state.get("imports") else "explain"
    if state["status"] == "discovery":
        return "discuss"
    return "clarify"


@dataclass(frozen=True)
class ShownReview:
    kind: str
    state_digest: str
    payload_digest: str


def render_spec(spec: JsonObject, emit: Callable[[str], None]) -> None:
    emit(spec["title"] + " — top: " + spec["top"])
    emit("Behavior: " + spec["behavior"])
    emit("Clock/reset: " + spec["clock_reset"])
    if "hardware_specification" not in spec:
        emit("Specification format: legacy; completeness was not recorded.")
    else:
        from openrtl.domain.hardware_specification import specification_completeness
        document = spec["hardware_specification"]
        completeness, unresolved = specification_completeness(spec)
        suffix = (" (" + ", ".join(unresolved) + ")") if unresolved else ""
        emit("Specification format: " + document["schema"] +
             "; completeness: " + completeness + suffix)
        if document["parameters"]:
            for row in document["parameters"]:
                emit("Parameter " + row["name"] + ": " + row["type"] +
                     " | Default: " + row["default"] +
                     " | Legal values: " + row["legal_values"] +
                     " | " + row["description"])
        else:
            emit("Parameters: none.")
        for row in document["sections"]:
            emit("Section " + row["id"].replace("_", " ") + " [" + row["status"] +
                 "]: " + row["content"])
    for port in spec["ports"]:
        emit("Port " + port["name"] + ": " + port["direction"] + ", " + str(port["width"]) + " bits")
    for row in spec["requirements"]:
        emit("Requirement " + row["id"] + ": " + row["text"] + " | Acceptance: " + row["acceptance"])
    for row in spec["questions"]:
        emit("Open question " + row["id"] + ": " + row["text"])
    for row in spec["assumptions"]:
        emit("Assumption " + row["id"] + ": " + row["text"] + " | Rationale: " + row["rationale"])
    if "readiness" not in spec:
        emit("Legacy specification: explicit readiness decisions have not been recorded.")
    else:
        for row in spec["readiness"]["items"]:
            emit("Readiness " + row["category"].replace("_", " ") + " [" + row["status"] + "]: " + row["decision"])
            emit("  Requirements: " + (", ".join(row["requirement_ids"]) or "none") +
                 "; ports: " + (", ".join(row["ports"]) or "none"))


def review_payload(state: JsonObject, kind: str) -> object:
    require(state["active"] is None, "review_requires_idle_session")
    if kind == "specification":
        require(state["status"] == "discovery" and state["spec"] is not None, "specification_review_unavailable")
        return state["spec"]
    if kind == "change":
        require(state["proposal"] is not None, "change_review_unavailable")
        return state["proposal"]
    require(kind == "acceptance" and state["status"] == "awaiting_acceptance", "acceptance_review_unavailable")
    return {"input": design_input_digest(state), "simulation": state["simulation"], "review": state["review"]}


def show_review(agent: DesignAgent, emit: Callable[[str], None]) -> ShownReview | None:
    state = agent.store.read()
    if state["active"] is not None:
        emit("An interrupted operation must be reconciled before review.")
        return None
    kind = "change" if state["proposal"] else ("specification" if state["status"] == "discovery" else "acceptance")
    if (kind == "specification" and state["spec"] is None) or (kind == "acceptance" and state["status"] != "awaiting_acceptance"):
        emit("No approval is available at this stage.")
        return None
    payload = review_payload(state, kind)
    emit("Review for " + kind + ":")
    # Full content is deliberate: a brief or truncated summary cannot grant approval.
    if kind == "change":
        proposal = state["proposal"]
        before, after = state["spec"], proposal["plan"]["specification"]
        emit("Change intent: " + proposal["intent"] + ". " + proposal["summary"])
        emit("Changed fields: " + ", ".join(k for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)))
        emit("Previous specification:")
        render_spec(before, emit)
        emit("Proposed specification:")
        render_spec(after, emit)
        for stage, paths in proposal["plan"]["stage_paths"].items():
            emit("Writable " + stage + ": " + (", ".join(paths) or "none; retain existing artifacts"))
        manifest = proposal["plan"]["manifest"]
        emit("Simulation top: " + manifest["top"] + "; seed: " + str(manifest["seed"]))
        for field in ("sources", "test_modules", "expected_tests"):
            emit(field.replace("_", " ") + ": " + ", ".join(manifest[field]))
        for row in manifest["requirement_tests"]:
            emit("Checks for " + row["requirement_id"] + ": " + ", ".join(row["tests"]))
    else:
        render_spec(state["spec"], emit)
    if kind == "acceptance":
        emit("Artifacts to inspect:")
        for path in state["files"]:
            emit("  " + path)
        emit("Simulation evidence: " + state["simulation"]["evidence_kind"] + "; " + state["simulation"]["status"])
        emit("Signoff: " + state["review"]["summary"])
        for finding in state["review"]["findings"]:
            emit("Finding: " + finding)
        emit("Acceptance rechecks retained simulation bytes. It does not establish synthesis, formal proof or a release.")
    if kind in ("specification", "change"):
        spec = state["spec"] if kind == "specification" else state["proposal"]["plan"]["specification"]
        try:
            require_ready(spec)
        except ValueError:
            emit("Readiness review is incomplete. Resolve open questions, all seven decision categories, "
                 "and every versioned specification section before approval.")
            return None
        emit("Readiness decisions are explicit; review their adequacy. This checklist does not prove completeness.")
    phrase = {"specification": "approve this specification", "change": "approve this change", "acceptance": "accept this design"}[kind]
    emit("To approve exactly this review, say: " + phrase)
    emit("This grants only " + kind + " approval; provider calls, simulation and broad delegation are separate.")
    return ShownReview(kind, content_digest(state), content_digest(payload))


def approve_shown(agent: DesignAgent, review: ShownReview | None, kind: str) -> JsonObject:
    require(review is not None and review.kind == kind, "show_current_review_before_approval")
    assert review is not None
    state = agent.store.read()
    require(content_digest(state) == review.state_digest and
            content_digest(review_payload(state, kind)) == review.payload_digest, "shown_review_stale")
    if kind == "change":
        require_ready(state["proposal"]["plan"]["specification"])
        return agent.approve_improvement(review.payload_digest)
    if kind == "specification":
        require_ready(state["spec"])
    else:
        # Reverify retained artifact bytes, not only the report fields shown earlier.
        agent.store.measurement(state)
    return agent.approve(review.payload_digest)


def revoke(agent: DesignAgent, capability: str) -> None:
    require(capability in ("provider", "simulation"), "capability_invalid")
    if capability == "provider":
        agent.expert = None
    else:
        agent.simulator = None
        agent.recovery = None
