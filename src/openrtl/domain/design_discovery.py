"""Pure discovery-candidate checks and explicit engineering-decision continuity."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from openrtl.domain.design_session import (
    JsonObject, object_value, require, sequence, stable_id, text,
    validate_engineering_memory, validate_spec,
)


@dataclass(frozen=True)
class DiscoveryCandidate:
    specification: JsonObject | None
    memory: list[JsonObject]
    questions: list[str]
    reply: str | None


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def discovery_memory(state: JsonObject) -> list[JsonObject]:
    """Expose saved facts and unresolved draft questions, without raw conversation.

    Specification questions can be longer than persisted memory entries. Keep their
    full text in this context projection; do not truncate it into a new decision.
    """
    memory = copy.deepcopy(validate_engineering_memory(state.get("engineering_memory", [])))
    identifiers = {row["id"] for row in memory}
    for row in (state.get("spec") or {}).get("questions", []):
        if row["id"] not in identifiers:
            memory.append({"id": row["id"], "kind": "question", "text": row["text"],
                           "provenance": "agent_proposal"})
    return memory


def _question_map(memory: list[JsonObject], specification: JsonObject | None) -> dict[str, str]:
    questions = {row["id"]: row["text"] for row in memory if row["kind"] == "question"}
    nonquestions = {row["id"] for row in memory if row["kind"] != "question"}
    for row in (specification or {}).get("questions", []):
        identifier = row["id"]
        require(identifier not in nonquestions, "expert_question_memory_conflict")
        require(identifier not in questions or
                _normalized(questions[identifier]) == _normalized(row["text"]),
                "expert_question_memory_conflict")
        questions[identifier] = row["text"]
    return questions


def _check_memory_content(memory: list[JsonObject], message: str, reply: str | None) -> None:
    prompt = _normalized(message)
    # Relax transcript matching only for recognizable engineering literals,
    # never arbitrary short private strings (for example credential-like input).
    short_value = (re.fullmatch(
        r"(?:[0-9]{1,10}(?:\.[0-9]{1,6})?|zero|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|sixteen|thirty two|sixty four)(?: (?:bits?|entries|cycles?|[kmgt]?hz))?",
        prompt) is not None or
        prompt in {"true", "false", "yes", "no", "high", "low", "active high", "active low",
                   "active-high", "active-low", "synchronous", "asynchronous", "signed", "unsigned"})
    for row in memory:
        value = _normalized(row["text"])
        require(row["provenance"] == "agent_proposal", "expert_cannot_confirm_user_memory")
        require(not prompt or (value != prompt and (short_value or prompt not in value)),
                "raw_prompt_in_engineering_memory")
        require(reply is None or _normalized(reply) not in value, "raw_reply_in_engineering_memory")


def _align_new_question_aliases(memory: list[JsonObject], specification: JsonObject | None,
                                prior_ids: set[str]) -> dict[str, str]:
    """Link only equal question text, never list lengths or ordinal labels."""
    if specification is None:
        return {}
    spec_questions = specification["questions"]
    spec_ids = {row["id"] for row in spec_questions}
    aliases: dict[str, str] = {}
    memory_ids = {row["id"] for row in memory}
    for row in memory:
        if row["kind"] != "question" or row["id"] in spec_ids or row["id"] in prior_ids:
            continue
        matches = [question["id"] for question in spec_questions
                   if _normalized(question["text"]) == _normalized(row["text"])]
        peers = [question for question in memory if question["kind"] == "question" and
                 _normalized(question["text"]) == _normalized(row["text"])]
        if len(matches) == 1 and len(peers) == 1 and matches[0] not in memory_ids:
            aliases[row["id"]] = matches[0]
            row["id"] = matches[0]
            memory_ids.add(matches[0])
    return aliases


def _asked_ids(value: object, current: dict[str, str], aliases: dict[str, str]) -> list[str]:
    values = sequence(value, maximum=3)
    result: list[str] = []
    for value in values:
        identifier = text(value, maximum=128)
        identifier = aliases.get(identifier, identifier)
        if identifier not in current:
            matches = [key for key, question in current.items()
                       if _normalized(question) == _normalized(identifier)]
            require(len(matches) == 1, "expert_clarification_question_unknown")
            identifier = matches[0]
        result.append(identifier)
    require(len(result) == len(set(result)), "expert_clarification_question_duplicate")
    require(len(result) == len({_normalized(current[key]) for key in result}),
            "expert_clarification_question_duplicate")
    return result


def _check_plan(value: object, asked: list[str], aliases: dict[str, str], rounds: int) -> None:
    plan = sequence(value, maximum=3)
    identifiers: list[str] = []
    for value in plan:
        row = object_value(value, {"id", "topic", "reason"})
        identifier = stable_id(row["id"])
        identifiers.append(aliases.get(identifier, identifier))
        require(row["topic"] in ("interface", "clock_reset", "behavior", "acceptance", "configuration"),
                "expert_question_plan_invalid")
        text(row["reason"], maximum=1024)
        require(rounds < 3 or row["topic"] != "configuration", "expert_clarification_late_topic")
    require(identifiers == asked, "expert_question_plan_invalid")


def validate_discovery(result: object, state: JsonObject, message: str, *,
                       clarification_rounds: int = 0,
                       question_history: list[JsonObject] | None = None,
                       require_plan: bool = False,
                       require_hardware_specification: bool = False) -> DiscoveryCandidate:
    """Accept a coherent proposal without treating model text as user authority.

    Reconciliation preserves omitted saved facts and unresolved questions. Only
    explicit question-to-decision transitions close questions; no widths, readiness
    anchors, answers, or approvals are invented here.
    """
    wrapped = isinstance(result, dict) and "specification" in result
    require(not require_plan or wrapped, "expert_discussion_fields_invalid")
    reply: str | None = None
    incoming: list[JsonObject] = []
    resolutions: list[JsonObject] = []
    requested: object = []
    if wrapped:
        assert isinstance(result, dict)
        required = {"reply", "specification"}
        optional = {"engineering_memory", "questions_asked", "resolved_questions", "question_plan"}
        require(required <= set(result) <= required | optional, "expert_discussion_fields_invalid")
        require(not require_plan or set(result) == required | optional,
                "expert_discussion_fields_invalid")
        reply = text(result["reply"], maximum=8000)
        proposal = result["specification"]
        incoming = copy.deepcopy(validate_engineering_memory(result.get("engineering_memory", [])))
        _check_memory_content(incoming, message, reply)
        requested = result.get("questions_asked", [])
        resolutions = sequence(result.get("resolved_questions", []), maximum=32)
    else:
        proposal = result
    specification = (copy.deepcopy(validate_spec(
        proposal, require_hardware_specification=require_hardware_specification))
        if proposal is not None else None)
    require(specification is not None or reply is not None, "expert_discussion_reply_missing")

    prior_memory = copy.deepcopy(validate_engineering_memory(state.get("engineering_memory", [])))
    prior_spec = state.get("spec")
    prior_questions = _question_map(prior_memory, prior_spec)
    prior_ids = {row["id"] for row in prior_memory} | set(prior_questions)
    history_ids: set[str] = set()
    history_texts: set[str] = set()
    for value in sequence(question_history or [], maximum=600):
        row = object_value(value, {"id", "text"})
        history_ids.add(stable_id(row["id"]))
        history_texts.add(_normalized(text(row["text"], maximum=8000)))
    prior_ids.update(history_ids)
    aliases = _align_new_question_aliases(incoming, specification, prior_ids)
    prior = {row["id"]: row for row in prior_memory}
    merged = copy.deepcopy(prior)
    incoming_by_id = {row["id"]: row for row in incoming}
    resolved: set[str] = set()
    for row in incoming:
        identifier = row["id"]
        old = prior.get(identifier)
        if old is not None and old["provenance"] == "user_confirmed":
            require(old["kind"] == row["kind"] and old["text"] == row["text"],
                    "expert_memory_confirmation_changed")
            continue
        if identifier in prior_questions and row["kind"] != "question":
            resolved.add(identifier)
        if old is not None and old["kind"] != "question" and row["kind"] == "question":
            require(False, "expert_clarification_repeated")
        merged[identifier] = row
    resolution_ids: set[str] = set()
    for value in resolutions:
        row = object_value(value, {"id", "resolution_id"})
        identifier, target = stable_id(row["id"]), stable_id(row["resolution_id"])
        require(identifier in prior_questions and identifier not in resolution_ids and
                target in incoming_by_id and incoming_by_id[target]["kind"] != "question",
                "expert_question_resolution_invalid")
        resolution_ids.add(identifier)
        decision = incoming_by_id[target]
        if identifier in incoming_by_id:
            explicit = incoming_by_id[identifier]
            require(explicit["kind"] != "question" and
                    _normalized(explicit["text"]) == _normalized(decision["text"]),
                    "expert_question_resolution_invalid")
        merged[identifier] = {"id": identifier, "kind": decision["kind"], "text": decision["text"],
                              "provenance": "agent_proposal"}
        resolved.add(identifier)

    # Legacy structured drafts already express reviewable assumptions with stable
    # IDs. Reusing an open question's exact ID is an explicit typed resolution.
    for assumption in (specification or {}).get("assumptions", []):
        identifier = assumption["id"]
        if identifier not in prior_questions:
            continue
        require(identifier not in incoming_by_id or
                incoming_by_id[identifier]["kind"] != "question",
                "expert_question_resolution_invalid")
        if identifier in resolved:
            require(_normalized(merged[identifier]["text"]) == _normalized(assumption["text"]),
                    "expert_question_resolution_invalid")
        resolved.add(identifier)
        if len(assumption["text"].encode("utf-8")) <= 1024:
            merged[identifier] = {"id": identifier, "kind": "assumption", "text": assumption["text"],
                                  "provenance": "agent_proposal"}
        else:
            # The full assumption remains in the draft; memory must retain its
            # established size bound instead of truncating the decision.
            merged.pop(identifier, None)

    current = _question_map(list(merged.values()), specification)
    require(not (resolved & set(current)), "expert_question_resolution_invalid")
    asked = _asked_ids(requested, current, aliases)
    new_questions = [identifier for identifier in current if identifier not in prior_questions]
    if require_plan:
        require(set(new_questions) <= set(asked), "expert_question_plan_invalid")
    elif not asked:
        # Legacy structured replies may omit questions_asked; their new question
        # records are the only reliable source for what can be displayed.
        asked = new_questions
        require(len(asked) <= 3, "expert_clarification_question_limit")
    prior_texts = {_normalized(value) for value in prior_questions.values()} | history_texts
    require(all(identifier not in prior_ids and _normalized(current[identifier]) not in prior_texts
                for identifier in asked), "expert_clarification_repeated")
    if specification is None and prior_spec is not None:
        require(False, "expert_specification_refinement_missing")
    if isinstance(result, dict) and "question_plan" in result:
        _check_plan(result["question_plan"], asked, aliases, clarification_rounds)

    if specification is not None:
        questions = {row["id"]: row for row in specification["questions"]}
        # Omission is not resolution. Carry unresolved saved questions forward.
        for identifier, value in prior_questions.items():
            if identifier not in resolved and identifier not in questions:
                questions[identifier] = {"id": identifier, "text": value}
        for identifier in asked:
            if identifier not in questions:
                require(identifier in incoming_by_id and incoming_by_id[identifier]["kind"] == "question",
                        "expert_clarification_question_unknown")
                questions[identifier] = {"id": identifier, "text": current[identifier]}
        require(all(row["id"] in questions for row in incoming if row["kind"] == "question"),
                "expert_question_memory_conflict")
        specification["questions"] = list(questions.values())
        specification = validate_spec(
            specification, require_hardware_specification=require_hardware_specification)
        _question_map(list(merged.values()), specification)
    # Keep spec-only questions in the saved memory when its established bounds
    # permit it. Longer legacy questions remain represented in the specification.
    for identifier, value in ({} if specification is None else
                              {row["id"]: row["text"] for row in specification["questions"]}).items():
        if identifier not in merged and len(value.encode("utf-8")) <= 1024:
            merged[identifier] = {"id": identifier, "kind": "question", "text": value,
                                  "provenance": "agent_proposal"}
    memory = validate_engineering_memory(list(merged.values()))
    if specification is not None:
        reply = (f"Updated the draft specification with {len(specification['requirements'])} requirements "
                 f"and {len(specification['ports'])} ports. "
                 f"{len(specification['questions'])} questions remain open for review.")
    elif memory:
        reply = (f"Retained {sum(row['kind'] != 'question' for row in memory)} design decisions "
                 f"and {sum(row['kind'] == 'question' for row in memory)} open questions.")
    elif require_plan:
        reply = ("I help turn circuit requirements into reviewable RTL and tests. "
                 "Tell me what circuit you want to design, including its intended behavior and interfaces.")
    if asked:
        assert reply is not None
        prefix = (reply + "\n\n") if specification is not None else "Please clarify these design decisions:\n\n"
        reply = prefix + "\n".join(f"{index}. {current[identifier]}"
                                     for index, identifier in enumerate(asked, 1))
    return DiscoveryCandidate(specification, memory, asked, reply)
