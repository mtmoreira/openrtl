"""Deterministic discovery reconciliation; no model calls or live qualification."""

from __future__ import annotations

import copy
import unittest

from openrtl.domain.design_discovery import discovery_memory, validate_discovery
from openrtl.domain.design_session import JsonObject, initial_state


def specification() -> JsonObject:
    return {"title": "Wire specification", "top": "wire_top", "behavior": "Transfer a to y.",
            "clock_reset": "Combinational; no reset.",
            "requirements": [{"id": "wire.transfer", "text": "Transfer all bits",
                              "acceptance": "Compare output for every input"}],
            "ports": [{"name": "a", "direction": "input", "width": 4},
                      {"name": "y", "direction": "output", "width": 4}],
            "questions": [], "assumptions": []}


def memory(identifier: str, content: str, kind: str = "question") -> JsonObject:
    return {"id": identifier, "kind": kind, "text": content, "provenance": "agent_proposal"}


def wrapper(*, spec: JsonObject | None = None, rows: list[JsonObject] | None = None,
            asked: list[str] | None = None) -> JsonObject:
    return {"reply": "Untrusted arbitrary conversational wording.", "specification": spec,
            "engineering_memory": rows or [], "questions_asked": asked or []}


class DiscoveryCandidateTest(unittest.TestCase):
    def test_modern_empty_reply_cannot_hide_untracked_questions(self) -> None:
        result = wrapper()
        result.update(reply="What width? What depth? What reset? What latency?",
                      resolved_questions=[], question_plan=[])
        candidate = validate_discovery(result, initial_state(), "Hello", require_plan=True)
        self.assertEqual(candidate.questions, [])
        self.assertIsNone(candidate.specification)
        self.assertIn("reviewable RTL", candidate.reply)
        self.assertNotIn("What width", candidate.reply)

    def test_omitted_facts_and_open_questions_survive_without_mutating_input(self) -> None:
        state = initial_state()
        state["engineering_memory"] = [memory("width", "The width is four bits", "decision"),
                                       memory("full", "What happens on a full queue?")]
        state["spec"] = specification()
        state["spec"]["questions"] = [{"id": "full", "text": "What happens on a full queue?"}]
        result = wrapper(spec=specification())
        before = copy.deepcopy((state, result))
        candidate = validate_discovery(result, state, "Expand the document")
        self.assertEqual(candidate.memory, state["engineering_memory"])
        self.assertEqual(candidate.specification["questions"], state["spec"]["questions"])
        self.assertEqual(candidate.questions, [])
        self.assertEqual((state, result), before)

    def test_same_id_decision_explicitly_resolves_saved_question(self) -> None:
        state = initial_state()
        state["engineering_memory"] = [memory("width", "How wide is the data?")]
        state["spec"] = specification()
        state["spec"]["questions"] = [{"id": "width", "text": "How wide is the data?"}]
        result = wrapper(spec=specification(), rows=[memory("width", "Data has four bits", "decision")])
        candidate = validate_discovery(result, state, "Four bits")
        self.assertEqual(candidate.specification["questions"], [])
        self.assertEqual(candidate.memory[0]["kind"], "decision")
        self.assertEqual(candidate.memory[0]["provenance"], "agent_proposal")
        self.assertIsNone(state["approved_spec"])

    def test_resolution_mapping_preserves_question_id_and_new_decision(self) -> None:
        state = initial_state()
        state["engineering_memory"] = [memory("width", "How wide is the data?")]
        result = wrapper(spec=specification(), rows=[memory("data_width", "Data has four bits", "decision")])
        result["resolved_questions"] = [{"id": "width", "resolution_id": "data_width"}]
        candidate = validate_discovery(result, state, "Four bits")
        self.assertEqual([row["id"] for row in candidate.memory], ["width", "data_width"])
        self.assertTrue(all(row["kind"] == "decision" for row in candidate.memory))
        self.assertTrue(all(row["provenance"] == "agent_proposal" for row in candidate.memory))

    def test_resolution_must_name_existing_question_and_new_nonquestion_memory(self) -> None:
        state = initial_state()
        state["engineering_memory"] = [memory("width", "How wide is the data?")]
        for resolution in ({"id": "unknown", "resolution_id": "decision"},
                           {"id": "width", "resolution_id": "unknown"},
                           {"id": "width", "resolution_id": "question"}):
            with self.subTest(resolution=resolution):
                result = wrapper(spec=specification(), rows=[
                    memory("decision", "Data has four bits", "decision"),
                    memory("question", "Is data four bits?")])
                result["resolved_questions"] = [resolution]
                with self.assertRaisesRegex(ValueError, "expert_question_resolution_invalid"):
                    validate_discovery(result, state, "Four bits")

    def test_retained_questions_do_not_count_as_another_round(self) -> None:
        state = initial_state()
        state["engineering_memory"] = [memory("width", "How wide is the data?")]
        result = wrapper(rows=state["engineering_memory"])
        self.assertEqual(validate_discovery(result, state, "Please continue").questions, [])

    def test_repeat_ids_and_whitespace_normalized_text_are_rejected_in_both_paths(self) -> None:
        for with_spec in (False, True):
            for identifier, question in (("width", "A changed width question?"),
                                         ("another", "  HOW   WIDE is the data?  ")):
                with self.subTest(with_spec=with_spec, identifier=identifier):
                    state = initial_state()
                    state["engineering_memory"] = [memory("width", "How wide is the data?")]
                    spec = specification() if with_spec else None
                    if spec:
                        spec["questions"] = [{"id": identifier, "text": question}]
                    result = wrapper(spec=spec, rows=[memory(identifier, question)], asked=[identifier])
                    with self.assertRaisesRegex(ValueError, "expert_clarification_repeated"):
                        validate_discovery(result, state, "Please continue")

    def test_captured_null_specification_refinement_cannot_replace_existing_draft(self) -> None:
        state = initial_state()
        state["spec"] = specification()
        state["spec"]["questions"] = [{"id": "q_full_policy", "text": "What should happen when full?"}]
        result = wrapper(rows=[memory("q_full_policy", "What should happen when full?")],
                         asked=["q_full_policy"])
        with self.assertRaisesRegex(ValueError, "expert_clarification_repeated"):
            validate_discovery(result, state, "Can we be more thorough in that specification?",
                               clarification_rounds=4)
        self.assertIsNotNone(state["spec"])

    def test_existing_draft_cannot_be_replaced_by_hidden_prose_question(self) -> None:
        state = initial_state()
        state["spec"] = specification()
        result = wrapper()
        result["reply"] = "How wide should the data be?"
        with self.assertRaisesRegex(ValueError, "expert_specification_refinement_missing"):
            validate_discovery(result, state, "Expand the specification")

    def test_existing_memory_suppresses_unstructured_question_prose(self) -> None:
        state = initial_state()
        state["engineering_memory"] = [memory("width", "The width is four", "decision")]
        result = wrapper()
        result["reply"] = "How wide should the data be?"
        candidate = validate_discovery(result, state, "Continue")
        self.assertEqual(candidate.reply, "Retained 1 design decisions and 0 open questions.")
        self.assertEqual(candidate.questions, [])
        self.assertEqual(validate_discovery(result, initial_state(), "Hello").reply, result["reply"])

    def test_history_blocks_resolved_question_reintroduced_under_new_id(self) -> None:
        state = initial_state()
        state["engineering_memory"] = [memory("width", "The width is four", "decision")]
        history = [{"id": "width", "text": "How wide is the data?"},
                   {"id": "width", "text": "What is the payload width?"}]
        result = wrapper(rows=[memory("new_width", " HOW wide  IS THE DATA? ")], asked=["new_width"])
        with self.assertRaisesRegex(ValueError, "expert_clarification_repeated"):
            validate_discovery(result, state, "Continue", question_history=history)

    def test_modern_contract_requires_complete_wrapper_and_plan(self) -> None:
        result = wrapper(rows=[memory("depth", "What queue depth is needed?")], asked=["depth"])
        with self.assertRaisesRegex(ValueError, "expert_discussion_fields_invalid"):
            validate_discovery(result, initial_state(), "Continue", require_plan=True,
                               clarification_rounds=4)
        with self.assertRaisesRegex(ValueError, "expert_discussion_fields_invalid"):
            validate_discovery(specification(), initial_state(), "Continue", require_plan=True)
        result["resolved_questions"] = []
        result["question_plan"] = [{"id": "depth", "topic": "configuration", "reason": "Choose queue storage."}]
        with self.assertRaisesRegex(ValueError, "expert_clarification_late_topic"):
            validate_discovery(result, initial_state(), "Continue", require_plan=True,
                               clarification_rounds=4)
        result["questions_asked"] = []
        result["question_plan"] = []
        with self.assertRaisesRegex(ValueError, "expert_question_plan_invalid"):
            validate_discovery(result, initial_state(), "Continue", require_plan=True)

    def test_legacy_same_id_assumption_resolves_without_promoting_authority(self) -> None:
        state = initial_state()
        state["spec"] = specification()
        state["spec"]["questions"] = [{"id": "alu.overflow", "text": "What is the overflow behavior?"}]
        spec = specification()
        spec["assumptions"] = [{"id": "alu.overflow", "text": "Overflow wraps modulo word width.",
                                "rationale": "A reviewable default."}]
        candidate = validate_discovery(spec, state, "Use a routine default")
        self.assertEqual(candidate.specification["questions"], [])
        self.assertEqual(candidate.memory, [memory("alu.overflow", "Overflow wraps modulo word width.",
                                                   "assumption")])
        result = wrapper(spec=specification(), rows=[memory("overflow.default", "Overflow wraps.", "assumption")])
        result["resolved_questions"] = [{"id": "alu.overflow", "resolution_id": "overflow.default"}]
        candidate = validate_discovery(result, state, "Use a routine default")
        self.assertTrue(all(row["kind"] == "assumption" for row in candidate.memory))

    def test_invalid_plan_topic_and_duplicate_resolutions_fail_closed(self) -> None:
        result = wrapper(rows=[memory("reset", "What is reset polarity?")], asked=["reset"])
        result["question_plan"] = [{"id": "reset", "topic": {"unexpected": "object"}, "reason": "Required."}]
        with self.assertRaisesRegex(ValueError, "expert_question_plan_invalid"):
            validate_discovery(result, initial_state(), "Continue")
        state = initial_state()
        state["engineering_memory"] = [memory("width", "How wide is data?")]
        result = wrapper(rows=[memory("width.answer", "Width is four", "decision")])
        result["resolved_questions"] = [{"id": "width", "resolution_id": "width.answer"}] * 2
        with self.assertRaisesRegex(ValueError, "expert_question_resolution_invalid"):
            validate_discovery(result, state, "Continue")

    def test_count_only_alias_does_not_identify_a_question(self) -> None:
        for with_spec in (False, True):
            with self.subTest(with_spec=with_spec):
                result = wrapper(spec=specification() if with_spec else None,
                                 rows=[memory("fifo.width", "Choose the FIFO width")], asked=["Q1"])
                with self.assertRaisesRegex(ValueError, "expert_clarification_question_unknown"):
                    validate_discovery(result, initial_state(), "Design a queue")

    def test_exact_normalized_text_links_new_memory_and_spec_question(self) -> None:
        spec = specification()
        spec["questions"] = [{"id": "fifo.width", "text": "Choose the FIFO width"}]
        result = wrapper(spec=spec, rows=[memory("Q1", "  choose   the fifo WIDTH ")], asked=["Q1"])
        candidate = validate_discovery(result, initial_state(), "Design a queue")
        self.assertEqual(candidate.questions, ["fifo.width"])
        self.assertEqual(candidate.memory[0]["id"], "fifo.width")
        self.assertNotIn("Q1", candidate.reply)

    def test_referenced_memory_question_can_be_added_to_nonempty_spec_questions(self) -> None:
        spec = specification()
        spec["questions"] = [{"id": "reset", "text": "What is reset polarity?"}]
        candidate = validate_discovery(wrapper(spec=spec, rows=[memory("width", "How wide is data?")],
                                               asked=["width"]), initial_state(), "Design a queue")
        self.assertEqual([row["id"] for row in candidate.specification["questions"]], ["reset", "width"])
        self.assertEqual(candidate.questions, ["width"])

    def test_conflicting_question_text_and_resurrection_are_rejected(self) -> None:
        spec = specification()
        spec["questions"] = [{"id": "width", "text": "How wide is the data?"}]
        with self.assertRaisesRegex(ValueError, "expert_question_memory_conflict"):
            validate_discovery(wrapper(spec=spec, rows=[memory("width", "What is queue depth?")]),
                               initial_state(), "Design a queue")
        state = initial_state()
        state["engineering_memory"] = [memory("width", "The data width is four", "decision")]
        with self.assertRaisesRegex(ValueError, "expert_clarification_repeated"):
            validate_discovery(wrapper(rows=[memory("width", "How wide is data?")], asked=["width"]),
                               state, "Continue")

    def test_question_plan_is_exact_and_late_questions_must_be_material(self) -> None:
        result = wrapper(rows=[memory("reset", "What is reset polarity?")], asked=["reset"])
        result["question_plan"] = [{"id": "reset", "topic": "clock_reset", "reason": "Pin behavior is unresolved."}]
        self.assertEqual(validate_discovery(result, initial_state(), "Continue", clarification_rounds=3).questions,
                         ["reset"])
        result["question_plan"][0]["topic"] = "configuration"
        with self.assertRaisesRegex(ValueError, "expert_clarification_late_topic"):
            validate_discovery(result, initial_state(), "Continue", clarification_rounds=3)
        result["question_plan"] = []
        with self.assertRaisesRegex(ValueError, "expert_question_plan_invalid"):
            validate_discovery(result, initial_state(), "Continue")

    def test_replies_emit_only_structured_questions_and_draft_status(self) -> None:
        result = wrapper(rows=[memory("width", "How wide is data?")], asked=["width"])
        result["reply"] = "Choose a width. Also should we add an unrelated interrupt?"
        candidate = validate_discovery(result, initial_state(), "Design a queue")
        self.assertEqual(candidate.reply, "Please clarify these design decisions:\n\n1. How wide is data?")
        result["specification"] = specification()
        candidate = validate_discovery(result, initial_state(), "Design a queue")
        self.assertIn("1 requirements and 2 ports", candidate.reply)
        self.assertIn("1 questions remain open", candidate.reply)
        self.assertNotIn("interrupt", candidate.reply)

    def test_short_design_value_can_be_embedded_but_raw_prompts_cannot(self) -> None:
        result = wrapper(spec=specification(), rows=[memory("width", "Data width is 32", "decision")])
        self.assertEqual(validate_discovery(result, initial_state(), "32").memory[0]["text"], "Data width is 32")
        for prompt, stored in (("32", "32"),
                               ("synthetic-private-value", "Saved value: synthetic-private-value"),
                               ("Use a queue with one hundred entries", "User requested: Use a queue with one hundred entries")):
            with self.subTest(prompt=prompt):
                result["engineering_memory"][0]["text"] = stored
                with self.assertRaisesRegex(ValueError, "raw_prompt_in_engineering_memory"):
                    validate_discovery(result, initial_state(), prompt)

    def test_confirmed_facts_are_preserved_and_never_fabricated(self) -> None:
        state = initial_state()
        confirmed = memory("width", "The width is four", "decision")
        confirmed["provenance"] = "user_confirmed"
        state["engineering_memory"] = [confirmed]
        candidate = validate_discovery(wrapper(spec=specification()), state, "Expand the draft")
        self.assertEqual(candidate.memory, [confirmed])
        with self.assertRaisesRegex(ValueError, "expert_cannot_confirm_user_memory"):
            validate_discovery(wrapper(rows=[confirmed]), initial_state(), "Continue")
        with self.assertRaisesRegex(ValueError, "expert_memory_confirmation_changed"):
            validate_discovery(wrapper(rows=[memory("width", "Width is eight", "decision")]), state, "Continue")

    def test_specification_width_and_readiness_are_not_repaired(self) -> None:
        spec = specification()
        spec["ports"][0]["width"] = 0
        with self.assertRaisesRegex(ValueError, "port_width_invalid"):
            validate_discovery(wrapper(spec=spec), initial_state(), "Design a queue")
        self.assertEqual(spec["ports"][0]["width"], 0)

    def test_legacy_bare_specification_and_long_question_context_remain_supported(self) -> None:
        spec = specification()
        spec["questions"] = [{"id": "long", "text": "A" * 1500}]
        state = initial_state()
        state["spec"] = spec
        candidate = validate_discovery(spec, state, "Expand the draft")
        self.assertEqual(candidate.specification["questions"], spec["questions"])
        self.assertEqual(candidate.memory, [])
        self.assertEqual(discovery_memory(state)[0]["text"], "A" * 1500)


if __name__ == "__main__":
    unittest.main()
