"""Synthetic repeated-resolution coverage; no provider calls or private capture."""

from __future__ import annotations

import copy
import unittest

from openrtl.domain.design_discovery import DiscoveryCandidate, validate_discovery
from openrtl.domain.design_session import JsonObject, initial_state
from tests.test_design_discovery import memory, specification, wrapper


class DiscoveryResolutionIdempotencyTest(unittest.TestCase):
    def fixture(self, *, kind: str = "decision", include_aliases: bool = False,
                self_targets: bool = False
                ) -> tuple[JsonObject, JsonObject, list[JsonObject], DiscoveryCandidate]:
        """Produce a first accepted proposal, then its equivalent saved state."""
        questions = [
            {"id": "q_queue_reset", "text": "Which reset polarity should the queue use?"},
            {"id": "q_queue_full", "text": "What should the queue do while full?"},
        ]
        answers = ["Reset is active low.", "The queue rejects enqueue transfers while full."]
        state = initial_state()
        state["spec"] = specification()
        state["spec"]["questions"] = copy.deepcopy(questions)
        state["engineering_memory"] = [memory(row["id"], row["text"]) for row in questions]
        targets = ([row["id"] for row in questions] if self_targets else
                   ["decision_queue_reset", "decision_queue_full"])
        rows = [memory(target, answer, kind) for target, answer in zip(targets, answers)]
        if include_aliases and not self_targets:
            rows.extend(memory(question["id"], answer, kind)
                        for question, answer in zip(questions, answers))
        result = wrapper(spec=specification(), rows=rows)
        result.update(reply="structured", question_plan=[], resolved_questions=[
            {"id": question["id"], "resolution_id": target}
            for question, target in zip(questions, targets)
        ])
        original_state, original_result = copy.deepcopy(state), copy.deepcopy(result)
        first = validate_discovery(result, state, "Apply these engineering choices",
                                   question_history=questions, require_plan=True)
        self.assertEqual(state, original_state)
        self.assertEqual(result, original_result)
        self.assertIsNotNone(first.specification)
        self.assertEqual(first.questions, [])
        self.assertEqual(len(first.memory), 2 if self_targets else 4)
        state["spec"] = copy.deepcopy(first.specification)
        state["engineering_memory"] = copy.deepcopy(first.memory)
        return state, result, questions, first

    def repeat(self, state: JsonObject, result: JsonObject,
               history: list[JsonObject]) -> DiscoveryCandidate:
        return validate_discovery(result, state, "Expand the reviewable draft",
                                  question_history=history, require_plan=True)

    def test_exact_full_proposal_repeat_has_no_semantic_effect_or_input_mutation(self) -> None:
        for include_aliases in (False, True):
            with self.subTest(include_aliases=include_aliases):
                state, result, history, first = self.fixture(include_aliases=include_aliases)
                before = copy.deepcopy((state, result, history))
                repeated = self.repeat(state, result, history)
                self.assertEqual(repeated, first)
                self.assertEqual((state, result, history), before)
                self.assertIsNone(state["approved_spec"])

    def test_same_id_resolution_target_is_idempotent(self) -> None:
        state, result, history, first = self.fixture(self_targets=True)
        self.assertEqual(self.repeat(state, result, history), first)

    def test_repeated_assumptions_remain_assumptions(self) -> None:
        state, result, history, first = self.fixture(kind="assumption", include_aliases=True)
        repeated = self.repeat(state, result, history)
        self.assertEqual(repeated, first)
        self.assertTrue(all(row["kind"] == "assumption" for row in repeated.memory))
        self.assertTrue(all(row["provenance"] == "agent_proposal" for row in repeated.memory))

    def test_saved_alias_and_target_provenance_are_preserved_independently(self) -> None:
        for confirm_alias, confirm_target in ((True, False), (False, True), (True, True)):
            for include_aliases in (False, True):
                with self.subTest(confirm_alias=confirm_alias, confirm_target=confirm_target,
                                  include_aliases=include_aliases):
                    state, result, history, _ = self.fixture(include_aliases=include_aliases)
                    for row in state["engineering_memory"]:
                        if ((row["id"] == "q_queue_reset" and confirm_alias) or
                                (row["id"] == "decision_queue_reset" and confirm_target)):
                            row["provenance"] = "user_confirmed"
                    before = copy.deepcopy((state, result, history))
                    repeated = self.repeat(state, result, history)
                    self.assertEqual(repeated.memory, state["engineering_memory"])
                    self.assertEqual((state, result, history), before)
                    self.assertTrue(all(row["provenance"] == "agent_proposal"
                                        for row in result["engineering_memory"]))

    def test_history_and_all_saved_resolution_endpoints_are_required(self) -> None:
        for missing in ("history", "alias", "target", "incoming_target"):
            with self.subTest(missing=missing):
                state, result, history, _ = self.fixture()
                if missing == "history":
                    history = [row for row in history if row["id"] != "q_queue_reset"]
                elif missing in ("alias", "target"):
                    absent = "q_queue_reset" if missing == "alias" else "decision_queue_reset"
                    state["engineering_memory"] = [row for row in state["engineering_memory"]
                                                    if row["id"] != absent]
                else:
                    result["engineering_memory"] = [row for row in result["engineering_memory"]
                                                     if row["id"] != "decision_queue_reset"]
                before = copy.deepcopy((state, result, history))
                with self.assertRaisesRegex(ValueError, "expert_question_resolution_invalid"):
                    self.repeat(state, result, history)
                self.assertEqual((state, result, history), before)

    def test_incoming_target_must_match_saved_kind_and_byte_exact_text(self) -> None:
        for field, value in (("text", "Reset is active high."),
                             ("text", "RESET IS ACTIVE LOW."),
                             ("text", "Reset  is active low."),
                             ("text", "Reset is active low. "),
                             ("kind", "assumption")):
            with self.subTest(field=field, value=value):
                state, result, history, _ = self.fixture()
                target = next(row for row in result["engineering_memory"]
                              if row["id"] == "decision_queue_reset")
                target[field] = value
                before = copy.deepcopy((state, result, history))
                with self.assertRaisesRegex(ValueError, "expert_question_resolution_invalid"):
                    self.repeat(state, result, history)
                self.assertEqual((state, result, history), before)

    def test_supplied_alias_must_match_saved_kind_and_byte_exact_text(self) -> None:
        for field, value in (("text", "Reset is active high."),
                             ("text", "RESET IS ACTIVE LOW."),
                             ("text", "Reset is active low. "),
                             ("kind", "assumption")):
            with self.subTest(field=field, value=value):
                state, result, history, _ = self.fixture(include_aliases=True)
                alias = next(row for row in result["engineering_memory"]
                             if row["id"] == "q_queue_reset")
                alias[field] = value
                with self.assertRaisesRegex(ValueError, "expert_question_resolution_invalid"):
                    self.repeat(state, result, history)

    def test_saved_alias_and_target_must_agree_even_if_incoming_target_matches(self) -> None:
        for endpoint in ("q_queue_reset", "decision_queue_reset"):
            for field, value in (("text", "Reset is active high."), ("kind", "assumption")):
                with self.subTest(endpoint=endpoint, field=field, value=value):
                    state, result, history, _ = self.fixture()
                    saved = next(row for row in state["engineering_memory"] if row["id"] == endpoint)
                    saved[field] = value
                    if endpoint == "decision_queue_reset":
                        incoming = next(row for row in result["engineering_memory"]
                                        if row["id"] == endpoint)
                        incoming[field] = value
                    with self.assertRaisesRegex(ValueError, "expert_question_resolution_invalid"):
                        self.repeat(state, result, history)

    def test_duplicate_resolution_entries_are_still_rejected(self) -> None:
        state, result, history, _ = self.fixture()
        result["resolved_questions"].append(copy.deepcopy(result["resolved_questions"][0]))
        with self.assertRaisesRegex(ValueError, "expert_question_resolution_invalid"):
            self.repeat(state, result, history)

    def test_repeated_resolution_cannot_reopen_original_question_id(self) -> None:
        state, result, history, _ = self.fixture()
        result["engineering_memory"].append(memory(history[0]["id"], history[0]["text"]))
        result["questions_asked"] = [history[0]["id"]]
        result["question_plan"] = [{"id": history[0]["id"], "topic": "clock_reset",
                                    "reason": "Reset pin behavior must be explicit."}]
        with self.assertRaisesRegex(ValueError, "expert_clarification_repeated"):
            self.repeat(state, result, history)

    def test_repeated_resolution_cannot_reopen_historical_text_under_new_id(self) -> None:
        state, result, history, _ = self.fixture()
        new_id = "another_reset_question"
        result["engineering_memory"].append(memory(new_id, history[0]["text"]))
        result["specification"]["questions"] = [{"id": new_id, "text": history[0]["text"]}]
        result["questions_asked"] = [new_id]
        result["question_plan"] = [{"id": new_id, "topic": "clock_reset",
                                    "reason": "Reset pin behavior must be explicit."}]
        with self.assertRaisesRegex(ValueError, "expert_clarification_repeated"):
            self.repeat(state, result, history)

    def test_idempotency_does_not_relax_specification_or_port_validation(self) -> None:
        for failure in ("shape", "width"):
            with self.subTest(failure=failure):
                state, result, history, _ = self.fixture()
                if failure == "shape":
                    result["specification"]["unexpected_field"] = "Unrecognized structure"
                    expected = "object_fields_invalid"
                else:
                    result["specification"]["ports"][0]["width"] = 0
                    expected = "port_width_invalid"
                with self.assertRaisesRegex(ValueError, expected):
                    self.repeat(state, result, history)


if __name__ == "__main__":
    unittest.main()
