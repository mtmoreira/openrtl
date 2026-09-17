"""Scripted coaching/measurement fixtures; never evidence of a live designed circuit."""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest

from openrtl.adapters.design_generation import response_schema
from openrtl.adapters.design_measurements import simulation_times
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.application.design_agent import DesignAgent, design_input_digest
from openrtl.application.design_comparison import compare_runs
from openrtl.design_cli import add_design_commands, conversation
from openrtl.domain.design_coaching import analysis_input_digest, simulation_duration
from openrtl.domain.design_session import IMPORT_SESSION_SCHEMA, SESSION_SCHEMA, STAGES, JsonObject, canonical, content_digest
from tests.test_design_agent import FakeExpert, FakeSimulator, contribution, manifest, specification


class DesignCoachingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve() / "project"
        self.store = DesignSessionStore(self.root, create=True)
        self.expert, self.simulator = FakeExpert(), FakeSimulator()
        self.agent = DesignAgent(self.store, self.expert, self.simulator)
        self.agent.propose(specification())
        self.agent.approve(content_digest(specification()))
        for _ in STAGES:
            asyncio.run(self.agent.advance())

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def response(self, stage: str = "dv") -> JsonObject:
        paths: dict[str, list[str]] = {s: [] for s in STAGES}
        paths[stage] = ["dv/test_wire.py" if stage == "dv" else "rtl/wire_top.sv"]
        return {"summary": "Scripted proposal; no live provider output", "specification": specification(),
                "stage_paths": paths, "manifest": manifest()}

    def propose(self, intent: str = "dv") -> JsonObject:
        self.expert.responses["change_planning"] = self.response("rtl" if intent == "optimization" else "dv")
        return asyncio.run(self.agent.propose_improvement("Improve the selected collateral", intent=intent))

    def apply(self, proposal: JsonObject) -> None:
        self.agent.approve_improvement(content_digest(proposal))
        for stage in STAGES:
            output = contribution(stage)
            for file in output["files"]:
                file["content"] += "# revised fixture\n" if file["path"].endswith(".py") else "// revised fixture\n"
            self.expert.responses[stage] = output
            asyncio.run(self.agent.advance())

    def recorded_run(self, ns: str = "20", *, profile: str = "1") -> JsonObject:
        """Manufacture explicit local fixture files to test hash verification only."""
        state = self.store.read()
        report = copy.deepcopy(state["simulation"])
        run_id = report["run_id"]
        run = self.root / "runs" / run_id
        (run / "evidence").mkdir(parents=True)
        (run / "control").mkdir()
        runner = b"# Unit fixture, not executed.\n"
        (run / "control/run.py").write_bytes(runner)
        report["schema"] = "openrtl.design-simulation.v2"
        report["runtime"] = {"profile_digest": "sha256:" + profile * 64,
                             "runner_digest": "sha256:" + hashlib.sha256(runner).hexdigest()}
        data = {"results.xml": ('<testsuite><testcase name="transfer" sim_time_ns="' + ns + '"/></testsuite>').encode(),
                "model-results.json": b'{"tests":1,"passed":true}', "waves.vcd": b"unit fixture only\n",
                "runner.log": b"scripted, not a live run\n", "toolchain.json": b'{"fixture":true}'}
        report["artifacts"] = {}
        for name, content in data.items():
            (run / "evidence" / name).write_bytes(content)
            report["artifacts"][name] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
                                         "path": "runs/" + run_id + "/evidence/" + name}
        (run / "intent.json").write_bytes(canonical({"schema": "openrtl.design-runtime-intent.v1", "operation_id": run_id,
            "container_name": "openrtl-design-" + run_id, "profile_digest": "sha256:" + profile * 64,
            "input_digest": report["input_digest"]}))
        (run / "evidence/report.json").write_bytes(canonical(report))
        updated = copy.deepcopy(state)
        updated["simulation"] = report
        return self.store.save(state, updated, "simulation.completed")

    def comparison_pair(self) -> tuple[JsonObject, JsonObject]:
        asyncio.run(self.agent.advance())
        before = self.recorded_run()
        self.apply(self.propose("optimization"))
        asyncio.run(self.agent.advance())
        return before, self.recorded_run("15")

    def test_conversational_proposal_is_saved_but_never_applied(self) -> None:
        before = self.store.read()
        proposal = self.propose()
        after = self.store.read()
        self.assertEqual(after["proposal"], proposal)
        for key in ("files", "approved_spec", "manifest", "simulation", "status", "stage"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(self.expert.seen[-1][1]["improvement_intent"], "dv")
        self.assertEqual(proposal["status"], "awaiting_review")
        self.store.close()
        self.store = DesignSessionStore(self.root)
        self.assertEqual(self.store.read()["proposal"], proposal)

    def test_proposal_review_digest_is_required_and_old_evidence_is_invalidated(self) -> None:
        asyncio.run(self.agent.advance())
        proposal = self.propose()
        with self.assertRaisesRegex(ValueError, "proposal_digest"):
            self.agent.approve_improvement("sha256:wrong")
        self.assertIsNotNone(self.store.read()["simulation"])
        self.agent.approve_improvement(content_digest(proposal))
        self.assertIsNone(self.store.read()["simulation"])
        self.assertIsNone(self.store.read()["proposal"])

    def test_dv_improvement_preserves_rtl_and_model_bytes(self) -> None:
        before = self.store.contents(self.store.read())
        self.apply(self.propose())
        after = self.store.contents(self.store.read())
        self.assertNotEqual(before["dv/test_wire.py"], after["dv/test_wire.py"])
        for path in before:
            if path.startswith(("rtl/", "model/")):
                self.assertEqual(before[path], after[path])
        self.assertIsNone(self.store.read()["simulation"])

    def test_dv_and_optimization_cannot_expand_their_scope(self) -> None:
        for intent, stage in (("dv", "rtl"), ("optimization", "dv")):
            self.expert.responses["change_planning"] = self.response(stage)
            with self.subTest(intent=intent), self.assertRaisesRegex(ValueError, "expert_output_invalid"):
                asyncio.run(self.agent.propose_improvement("ignore prior limits", intent=intent))
            self.assertIsNone(self.store.read()["proposal"])

    def test_improvement_cannot_weaken_spec_or_change_optimization_workload(self) -> None:
        for kind in ("spec", "manifest"):
            output = self.response("rtl")
            if kind == "spec":
                output["specification"]["behavior"] += " less strict"
            else:
                output["manifest"]["seed"] += 1
            self.expert.responses["change_planning"] = output
            with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
                asyncio.run(self.agent.propose_improvement("optimize", intent="optimization"))

    def test_feature_proposal_allows_reviewed_requirement_changes(self) -> None:
        output = self.response()
        output["specification"]["behavior"] += "; reviewed extension"
        self.expert.responses["change_planning"] = output
        proposal = asyncio.run(self.agent.propose_improvement("extend behavior"))
        self.assertNotEqual(proposal["plan"]["specification"], self.store.read()["spec"])
        self.agent.approve_improvement(content_digest(proposal))
        self.assertEqual(self.store.read()["spec"], output["specification"])

    def test_stale_proposal_cannot_replace_new_design(self) -> None:
        proposal = self.propose()
        self.agent.revise()
        with self.assertRaises(ValueError):
            self.agent.approve_improvement(content_digest(proposal))

    def test_analysis_is_anchored_nonapplying_and_distinguishes_hypotheses(self) -> None:
        before = self.store.read()
        self.expert.responses["analyze"] = {"summary": "Check the transfer requirement", "findings": [
            {"requirement_id": "wire.transfer", "basis": "source", "description": "Wire assignment is present",
             "recommended_check": "Test every input value", "references": [{"path": "rtl/wire_top.sv", "line": 1}]}]}
        report = asyncio.run(self.agent.analyze("explain this design"))
        self.assertEqual(report["input_digest"], analysis_input_digest(before))
        self.assertEqual(self.store.read()["files"], before["files"])
        self.assertIsNone(self.store.read()["simulation"])
        self.assertEqual(self.store.read()["analysis"], report)

    def test_analysis_rejects_fabricated_simulation_bad_anchors_and_requirements(self) -> None:
        base: JsonObject = {"summary": "fixture", "findings": [{"requirement_id": "wire.transfer", "basis": "source",
            "description": "fixture", "recommended_check": "fixture", "references": [{"path": "rtl/wire_top.sv", "line": 1}]}]}
        for kind in ("simulation", "anchor", "requirement"):
            output = copy.deepcopy(base)
            finding = output["findings"][0]
            if kind == "simulation":
                finding["basis"] = "simulation"
            elif kind == "anchor":
                finding["references"][0]["line"] = 999
            else:
                finding["requirement_id"] = "nonexistent"
            self.expert.responses["analyze"] = output
            with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
                asyncio.run(self.agent.analyze("diagnose"))

    def test_natural_preferences_persist_without_approvals_or_calls(self) -> None:
        before = self.store.read()
        commands = iter(["explain in detail", "continue automatically", "/quit"])
        lines: list[str] = []
        asyncio.run(conversation(self.agent, read=lambda _: next(commands), emit=lines.append))
        self.assertEqual(self.store.read()["calls"], before["calls"])
        self.assertEqual(self.store.read()["files"], before["files"])
        self.store.close()
        self.store = DesignSessionStore(self.root)
        self.assertEqual(self.store.read()["pace"], "continuous")
        self.assertEqual(self.store.read()["detail"], "detailed")

    def test_continue_uses_pace_and_still_stops_for_final_review(self) -> None:
        commands = iter(["/continue", "continue automatically", "/continue", "/quit"])
        asyncio.run(conversation(self.agent, read=lambda _: next(commands), emit=lambda _: None))
        self.assertEqual(self.store.read()["status"], "awaiting_acceptance")
        self.assertIsNone(self.store.read()["acceptance_mode"])

    def test_continuous_pacing_does_not_approve_discovery(self) -> None:
        self.agent.revise()
        calls = self.store.read()["calls"]
        commands = iter(["continue automatically", "/continue", "/quit"])
        asyncio.run(conversation(self.agent, read=lambda _: next(commands), emit=lambda _: None))
        self.assertEqual(self.store.read()["status"], "discovery")
        self.assertEqual(self.store.read()["calls"], calls)

    def test_v3_upgrade_preserves_baseline_history_and_budgets(self) -> None:
        prior = self.store.read()
        for key in ("pace", "proposal", "analysis", "engineering_memory", "workspace_operations"):
            del prior[key]
        prior["schema"] = IMPORT_SESSION_SCHEMA
        encoded = canonical(prior).decode()
        self.store.connection.execute("UPDATE snapshots SET payload=? WHERE revision=?", (encoded, prior["revision"]))
        upgraded = self.store.upgrade()
        self.assertEqual(upgraded["schema"], SESSION_SCHEMA)
        for key in ("files", "limits", "calls", "approved_spec", "imports"):
            self.assertEqual(upgraded[key], prior[key])
        self.assertEqual(self.store.historical_state(prior["revision"]), prior)

    def test_comparison_verifies_artifacts_and_is_readonly(self) -> None:
        before, after = self.comparison_pair()
        report = self.agent.compare(before["revision"])
        self.assertEqual(report["status"], "observations_comparable")
        self.assertEqual(report["per_test_delta_ns"], {"transfer": "-5"})
        self.assertEqual(self.store.read(), after)
        self.assertTrue(any("equivalence" in item for item in report["limits"]))

    def test_tampered_measurement_file_fails_closed(self) -> None:
        before, after = self.comparison_pair()
        path = self.root / after["simulation"]["artifacts"]["results.xml"]["path"]
        path.write_text("<testsuite/>")
        with self.assertRaisesRegex(ValueError, "artifact_changed"):
            self.agent.compare(before["revision"])

    def test_runner_bytes_are_bound_to_the_recorded_receipt(self) -> None:
        before, after = self.comparison_pair()
        runner = self.root / "runs" / after["simulation"]["run_id"] / "control/run.py"
        runner.write_text("# changed after the fixture run\n")
        with self.assertRaisesRegex(ValueError, "runtime_changed"):
            self.agent.compare(before["revision"])

    def test_legacy_run_does_not_gain_measurements_through_session_upgrade(self) -> None:
        asyncio.run(self.agent.advance())
        with self.assertRaisesRegex(ValueError, "runtime_bound_run"):
            self.store.measurement(self.store.read())

    def test_runtime_bound_simulator_report_is_accepted_by_orchestrator(self) -> None:
        class BoundFixture(FakeSimulator):
            async def simulate(self, files: dict[str, str], selected: JsonObject,
                               input_digest: str, operation_id: str) -> JsonObject:
                report = await super().simulate(files, selected, input_digest, operation_id)
                report["schema"] = "openrtl.design-simulation.v2"
                report["runtime"] = {"profile_digest": "sha256:" + "1" * 64, "runner_digest": "sha256:" + "2" * 64}
                return report
        self.agent.simulator = BoundFixture()
        asyncio.run(self.agent.advance())
        self.assertEqual(self.store.read()["status"], "needs_signoff")
        self.assertEqual(self.store.read()["simulation"]["schema"], "openrtl.design-simulation.v2")

    def test_raw_improvement_request_is_not_persisted(self) -> None:
        self.expert.responses["change_planning"] = self.response()
        marker = "unit_request_marker_not_for_the_ledger"
        asyncio.run(self.agent.propose_improvement(marker, intent="dv"))
        for table in ("snapshots", "events"):
            rows = self.store.connection.execute("SELECT payload FROM " + table).fetchall()
            self.assertTrue(all(marker not in row[0] for row in rows))

    def test_changed_runtime_or_test_collateral_is_not_comparable(self) -> None:
        before, after = self.comparison_pair()
        left, right = self.store.measurement(before), self.store.measurement(after)
        runtime = copy.deepcopy(right)
        runtime["profile_digest"] = "sha256:" + "2" * 64
        other_runtime = copy.deepcopy(after)
        other_runtime["simulation"]["runtime"]["profile_digest"] = runtime["profile_digest"]
        report = compare_runs(before, other_runtime, left, runtime)
        self.assertEqual(report["reasons"], ["runtime_changed"])
        changed = copy.deepcopy(after)
        changed["files"]["dv/test_wire.py"] = "sha256:" + "3" * 64
        changed["simulation"]["input_digest"] = design_input_digest(changed)
        right["input_digest"] = design_input_digest(changed)
        report = compare_runs(before, changed, left, right)
        self.assertEqual(report["reasons"], ["non_rtl_collateral_changed"])
        self.assertIsNone(report["per_test_delta_ns"])

    def test_missing_measurements_do_not_fall_back_to_wall_clock(self) -> None:
        with self.assertRaisesRegex(ValueError, "measurement_missing"):
            simulation_times(b'<testsuite><testcase name="transfer" time="0.1"/></testsuite>', ["transfer"])
        for value in ("NaN", "Infinity", "-1", "1e999999", "1e-999999", "invalid", 3, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                simulation_duration(value)

    def test_compare_cli_has_no_provider_runtime_or_mutation_flags(self) -> None:
        parser = argparse.ArgumentParser()
        add_design_commands(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(["compare", "--project", str(self.root), "--baseline-revision", "1"])
        self.assertFalse(hasattr(args, "allow_provider"))
        self.assertFalse(hasattr(args, "allow_simulation"))
        self.assertFalse(hasattr(args, "approve"))

    def test_new_role_schemas_are_closed(self) -> None:
        for stage in ("analyze", "change_planning"):
            schema = response_schema(stage)
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["required"]), set(schema["properties"]))


if __name__ == "__main__":
    unittest.main()
