"""Provider-free orchestration tests; fake simulator receipts are NOT live evidence."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest

from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_simulation import parse_test_results
from openrtl.application.design_agent import DesignAgent, DesignPolicy, ExpertReply, design_input_digest
from openrtl.design_cli import conversation
from openrtl.domain.design_session import (
    JsonObject, STAGES, canonical, content_digest, source_path, validate_manifest, validate_spec, validate_state,
)


def specification() -> JsonObject:
    return {"title": "Reviewed wire", "top": "wire_top", "behavior": "y equals a without a clock",
            "clock_reset": "Combinational; no clock or reset.",
            "requirements": [{"id": "wire.transfer", "text": "Transfer all input bits", "acceptance": "Check all 16 inputs"}],
            "ports": [{"name": "a", "direction": "input", "width": 4},
                      {"name": "y", "direction": "output", "width": 4}], "questions": [], "assumptions": []}


def manifest() -> JsonObject:
    return {"top": "wire_top", "sources": ["rtl/wire_top.sv", "rtl/properties.sv"],
            "test_modules": ["test_wire"], "expected_tests": ["transfer"], "seed": 71,
            "requirement_tests": [{"requirement_id": "wire.transfer", "tests": ["transfer"]}]}


def contribution(stage: str) -> JsonObject:
    entries = {
        "architecture": [("docs/architecture.md", "# Wire\nRequirement wire.transfer.\n")],
        "verification_plan": [("docs/verification-plan.md", "# DV\nCheck all 16 values for wire.transfer.\n")],
        "reference_model": [("model/wire.py", "def transfer(a):\n    return a\n"),
                            ("model/test_model.py", "import unittest\n# Unit fixture, not simulation evidence.\n")],
        "rtl": [("rtl/wire_top.sv", "module wire_top(input [3:0] a, output [3:0] y); assign y=a; endmodule\n")],
        "assertions": [("rtl/properties.sv", "// Unit fixture for artifact ownership only.\n")],
        "dv": [("dv/test_wire.py", "# Unit fixture; no live simulation is performed.\n")],
        "diagnosis": [("rtl/wire_top.sv", "module wire_top(input [3:0] a, output [3:0] y);\nassign y=a; endmodule\n")],
    }
    return {"summary": "Reviewable " + stage, "files": [{"path": p, "content": c} for p, c in entries[stage]],
            "manifest": manifest() if stage == "dv" else None}


class FakeExpert:
    def __init__(self) -> None:
        self.seen: list[tuple[str, JsonObject]] = []
        self.responses: dict[str, JsonObject] = {}
        self.fail = False

    async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
        self.seen.append((stage, copy.deepcopy(context)))
        if self.fail:
            raise RuntimeError("private prompt must not be logged")
        if stage in self.responses:
            output = self.responses[stage]
        elif stage == "discovery":
            output = specification()
        elif stage == "signoff":
            output = {"verdict": "accept", "summary": "Unit-only independent reviewer response", "findings": []}
        elif stage == "explain":
            output = {"explanation": "A wire maps input to output.", "references": [{"path": "rtl/wire_top.sv", "line": 1}]}
        else:
            output = contribution(stage)
        return ExpertReply(copy.deepcopy(output), "unit-test", "fake-not-a-live-model", 10, 20)


class FakeSimulator:
    """Injects a contract-shaped receipt to exercise gates, not actual simulation."""
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_first = False
        self.synthetic = False

    async def simulate(self, files: dict[str, str], selected: JsonObject,
                       input_digest: str, operation_id: str) -> JsonObject:
        self.calls.append(input_digest)
        failed = self.fail_first and len(self.calls) == 1
        return {"schema": "openrtl.design-simulation.v1", "input_digest": input_digest,
                "status": "failed" if failed else "passed", "run_id": operation_id,
                "evidence_kind": "synthetic" if self.synthetic else "isolated_verilator_cocotb",
                "tests": [] if failed else ["transfer"], "model_tests": 1,
                "artifacts": {"unit_fixture_only": "not_live_evidence"},
                "diagnostics": "unit fixture failure" if failed else "", "error_code": None}


class DesignAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve() / "session"
        self.store = DesignSessionStore(self.root, create=True)
        self.expert, self.simulator = FakeExpert(), FakeSimulator()
        self.agent = DesignAgent(self.store, self.expert, self.simulator)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def approve_spec(self) -> None:
        self.agent.propose(specification())
        self.agent.approve(content_digest(specification()))

    def generate_files(self) -> None:
        self.approve_spec()
        for _ in STAGES:
            asyncio.run(self.agent.advance())

    def test_exact_review_is_required_before_experts(self) -> None:
        self.agent.propose(specification())
        with self.assertRaises(ValueError):
            asyncio.run(self.agent.advance())
        with self.assertRaisesRegex(ValueError, "digest_mismatch"):
            self.agent.approve("sha256:wrong")
        self.assertEqual(self.expert.seen, [])
        self.agent.approve(content_digest(specification()))
        self.assertEqual(self.store.read()["status"], "building")

    def test_open_questions_block_review_and_assumptions_are_digest_bound(self) -> None:
        spec = specification()
        spec["questions"] = [{"id": "width", "text": "What width?"}]
        self.agent.propose(spec)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.agent.approve(content_digest(spec))
        spec["questions"] = []
        spec["assumptions"] = [{"id": "A1", "text": "4 bits", "rationale": "Small exploration"}]
        self.agent.propose(spec)
        with self.assertRaises(ValueError):
            self.agent.approve(content_digest(specification()))
        self.agent.approve(content_digest(spec))

    def test_roles_context_independence_and_bounded_metadata(self) -> None:
        self.generate_files()
        self.assertEqual([s for s, _ in self.expert.seen], list(STAGES))
        for stage, pack in self.expert.seen:
            self.assertEqual(pack["schema"], "openrtl.design-context.v2")
            if stage in ("reference_model", "dv"):
                self.assertFalse(any(p.startswith("rtl/") for p in pack["artifacts"]))
        receipts = [e for e in self.store.events() if e["event"] == "operation.received"]
        self.assertEqual(len(receipts), len(STAGES))
        self.assertEqual(receipts[0]["fields"]["input_tokens"], 10)
        self.assertNotIn("content", canonical(self.store.events()).decode())

    def test_raw_discussion_and_provider_error_are_not_persisted(self) -> None:
        asyncio.run(self.agent.discuss("PRIVATE USER WORDS ONLY IN EPHEMERAL CONTEXT"))
        self.assertNotIn("PRIVATE USER WORDS", canonical(self.store.read()).decode())
        self.assertNotIn("PRIVATE USER WORDS", canonical(self.store.events()).decode())
        self.expert.fail = True
        with self.assertRaisesRegex(ValueError, "expert_invocation_failed"):
            asyncio.run(self.agent.discuss("another ephemeral message"))
        self.assertNotIn("private prompt", canonical(self.store.events()).decode())
        self.assertIsNone(self.store.read()["active"])

    def test_complete_gate_chain_needs_final_user_acceptance(self) -> None:
        self.generate_files()
        asyncio.run(self.agent.advance())
        self.assertEqual(self.store.read()["status"], "needs_signoff")
        asyncio.run(self.agent.advance())
        state = self.store.read()
        self.assertEqual(state["status"], "awaiting_acceptance")
        final = content_digest({"input": design_input_digest(state), "simulation": state["simulation"], "review": state["review"]})
        with self.assertRaises(ValueError):
            self.agent.approve(state["approved_spec"])
        self.agent.approve(final)
        self.assertEqual(self.store.read()["status"], "accepted")

    def test_failed_simulation_requires_candidate_and_rerun(self) -> None:
        self.generate_files()
        self.simulator.fail_first = True
        asyncio.run(self.agent.advance())
        self.assertEqual(self.store.read()["status"], "needs_repair")
        asyncio.run(self.agent.advance())
        self.assertEqual(self.store.read()["status"], "building")
        asyncio.run(self.agent.advance())
        self.assertEqual(len(self.simulator.calls), 2)
        self.assertNotEqual(*self.simulator.calls)
        self.assertEqual(self.store.read()["status"], "needs_signoff")

    def test_diagnosis_cannot_weaken_tests(self) -> None:
        self.generate_files()
        self.simulator.fail_first = True
        asyncio.run(self.agent.advance())
        self.expert.responses["diagnosis"] = {"summary": "Weaken test", "files": [{"path": "dv/test_wire.py", "content": "pass\n"}], "manifest": None}
        before = self.store.read()["files"]
        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(self.agent.advance())
        self.assertEqual(self.store.read()["files"], before)

    def test_scripted_evidence_cannot_reach_signoff(self) -> None:
        self.generate_files()
        self.simulator.synthetic = True
        with self.assertRaisesRegex(ValueError, "simulation_execution_failed"):
            asyncio.run(self.agent.advance())
        self.assertEqual(self.store.read()["status"], "building")

    def test_missing_runtime_does_not_consume_expert_calls(self) -> None:
        self.generate_files()
        agent = DesignAgent(self.store, self.expert)
        before = self.store.read()
        with self.assertRaisesRegex(ValueError, "isolated_simulator_not_configured"):
            asyncio.run(agent.advance())
        self.assertEqual(before, self.store.read())

    def test_unresolved_signoff_does_not_self_approve(self) -> None:
        self.generate_files()
        asyncio.run(self.agent.advance())
        self.expert.responses["signoff"] = {"verdict": "revise", "summary": "Boundary tests inadequate", "findings": ["Add a boundary check"]}
        asyncio.run(self.agent.advance())
        self.assertEqual(self.store.read()["status"], "review_blocked")
        with self.assertRaises(ValueError):
            self.agent.approve("anything")

    def test_budget_exhaustion_is_persistent(self) -> None:
        self.approve_spec()
        agent = DesignAgent(self.store, self.expert, policy=DesignPolicy(max_calls=1))
        asyncio.run(agent.advance())
        with self.assertRaisesRegex(ValueError, "budget_exhausted"):
            asyncio.run(agent.advance())
        self.assertEqual(self.store.read()["calls"], 1)

    def test_role_cannot_overwrite_another_artifact(self) -> None:
        self.approve_spec()
        asyncio.run(self.agent.advance())
        self.expert.responses["verification_plan"] = contribution("architecture")
        before = self.store.read()["files"]
        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(self.agent.advance())
        self.assertEqual(self.store.read()["files"], before)

    def test_resume_and_stale_writer_fail_closed(self) -> None:
        before = self.store.read()
        self.agent.propose(specification())
        with self.assertRaisesRegex(ValueError, "concurrent_change"):
            self.store.save(before, before, "detail.changed")
        second = DesignSessionStore(self.root, read_only=True)
        try:
            self.assertEqual(second.read(), self.store.read())
        finally:
            second.close()

    def test_interrupted_operation_is_not_replayed(self) -> None:
        self.approve_spec()
        state = self.store.read()
        active = copy.deepcopy(state)
        active["active"] = {"id": "a" * 32, "kind": "expert", "stage": "architecture"}
        self.store.save(state, active, "operation.started")
        with self.assertRaisesRegex(ValueError, "reconciliation"):
            asyncio.run(self.agent.advance())
        self.assertEqual(self.expert.seen, [])

    def test_revision_preserves_history_but_invalidates_approval(self) -> None:
        self.generate_files()
        old_files = self.store.read()["files"]
        self.agent.revise()
        self.assertIsNone(self.store.read()["approved_spec"])
        self.assertEqual(self.store.read()["files"], {})
        self.assertTrue(old_files)
        self.assertGreater(self.store.connection.execute("SELECT COUNT(*) FROM blobs").fetchone()[0], 0)

    def test_explanations_cannot_change_state_and_anchors_are_checked(self) -> None:
        self.generate_files()
        before = self.store.read()
        asyncio.run(self.agent.explain("Explain this"))
        self.assertEqual(before["files"], self.store.read()["files"])
        self.assertEqual(before["approved_spec"], self.store.read()["approved_spec"])
        self.expert.responses["explain"] = {"explanation": "Invalid anchor", "references": [{"path": "rtl/wire_top.sv", "line": 500}]}
        with self.assertRaises(ValueError):
            asyncio.run(self.agent.explain("Explain again"))

    def test_interactive_progress_and_detail_do_not_approve(self) -> None:
        messages = iter(["Design a wire", "/detail detailed", "/quit"])
        output: list[str] = []
        result = asyncio.run(conversation(self.agent, read=lambda _: next(messages), emit=output.append))
        self.assertEqual(result, 0)
        self.assertEqual(self.store.read()["detail"], "detailed")
        self.assertIsNone(self.store.read()["approved_spec"])
        self.assertTrue(any("/approve sha256:" in line for line in output))

    def test_unknown_state_and_blob_tampering_rejected(self) -> None:
        state = self.store.read()
        state["status"] = "magic_success"
        with self.assertRaises(ValueError):
            validate_state(state)
        self.generate_files()
        self.store.connection.execute("UPDATE blobs SET content=?", (b"tampered",))
        with self.assertRaisesRegex(ValueError, "artifact_bytes_changed"):
            self.store.contents(self.store.read())


class DesignContractTest(unittest.TestCase):
    def test_path_escape_hidden_and_executable_payloads_rejected(self) -> None:
        for path in ("../x.py", "/rtl/x.sv", "rtl/../x.sv", "rtl/.hidden.sv", "dv/install.sh", "model/.env"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                source_path(path)

    def test_strict_spec_and_manifest_reject_drift(self) -> None:
        spec = specification()
        spec["ports"][0]["width"] = True
        with self.assertRaises(ValueError):
            validate_spec(spec)
        selected = manifest()
        selected["requirement_tests"] = []
        with self.assertRaises(ValueError):
            validate_manifest(selected, {"rtl/wire_top.sv": "", "rtl/properties.sv": "", "dv/test_wire.py": ""}, specification())

    def test_xml_needs_exact_executed_non_skipped_tests(self) -> None:
        good = b'<testsuites><testsuite tests="1"><testcase name="transfer"/></testsuite></testsuites>'
        self.assertEqual(parse_test_results(good, ["transfer"]), ["transfer"])
        for data in (b'<testsuites/>', good.replace(b'/></testsuite>', b'><skipped/></testcase></testsuite>'),
                     good.replace(b'transfer', b'wrong'), b'<!DOCTYPE a><testsuites/>',
                     good.replace(b'tests="1"', b'failures="1"')):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_test_results(data, ["transfer"])


if __name__ == "__main__":
    unittest.main()
