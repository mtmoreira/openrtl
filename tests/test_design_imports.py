"""Local multi-file import/change contracts; no provider or live simulator calls."""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openrtl.adapters.design_imports import import_design_files
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.application.design_agent import DesignAgent
from openrtl.design_cli import add_design_commands, conversation, run_design_command
from openrtl.domain.design_imports import validate_import_plan
from openrtl.domain.design_session import JsonObject, STAGES, SESSION_SCHEMA, canonical, content_digest, previous_initial_state, validate_state
from tests.test_design_agent import FakeExpert, FakeSimulator, contribution, manifest, specification


class DesignImportsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.sources = self.root / "sources"
        self.sources.mkdir()
        self.store = DesignSessionStore(self.root / "session", create=True)
        self.expert, self.simulator = FakeExpert(), FakeSimulator()
        self.agent = DesignAgent(self.store, self.expert, self.simulator)
        self.files = {f["path"]: f["content"] for stage in STAGES for f in contribution(stage)["files"]}
        for path, content in self.files.items():
            target = self.sources / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self.plan: JsonObject = {"schema": "openrtl.design-import.v1", "files": [
            {"source": path, "target": path, "digest": "sha256:" + hashlib.sha256(content.encode()).hexdigest()}
            for path, content in sorted(self.files.items())]}

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def load_imports(self) -> JsonObject:
        return import_design_files(self.store, self.sources, self.plan, content_digest(self.plan))

    def baseline(self) -> None:
        self.load_imports()
        self.agent.propose(specification())
        plan = self.agent.plan_baseline(manifest())
        self.agent.approve_baseline(manifest(), content_digest(plan))

    def change(self, stage: str = "rtl") -> JsonObject:
        spec = specification()
        spec["title"] = "Reviewed wire revision"
        stage_paths: dict[str, list[str]] = {s: [] for s in STAGES}
        stage_paths[stage] = ["rtl/wire_top.sv" if stage == "rtl" else "dv/test_wire.py"]
        return self.agent.plan_change({"specification": spec, "stage_paths": stage_paths, "manifest": manifest()})

    def test_multi_file_import_is_immutable_idempotent_and_not_execution(self) -> None:
        state = self.load_imports()
        self.assertEqual(set(state["imports"]), set(self.files))
        self.assertEqual(state["files"], {})
        self.assertEqual(state["calls"], 0)
        self.assertIsNone(state["simulation"])
        self.assertEqual(self.load_imports(), state)
        self.assertEqual(self.store.import_contents(state), self.files)
        self.assertEqual(self.expert.seen, [])
        self.assertEqual(self.simulator.calls, [])

    def test_all_paths_validated_before_any_payload_read(self) -> None:
        for source in (".env", "secrets.json", "../outside.py", "/outside.py", "folder/.token.py", "tool.sh"):
            plan = copy.deepcopy(self.plan)
            plan["files"][-1]["source"] = source
            with self.subTest(source=source), patch("openrtl.adapters.design_imports.os.open") as opened:
                with self.assertRaises(ValueError):
                    import_design_files(self.store, self.sources, plan, content_digest(plan))
                opened.assert_not_called()
        self.assertEqual(self.store.read()["revision"], 0)

    def test_review_digest_and_changed_source_fail_atomically(self) -> None:
        with self.assertRaisesRegex(ValueError, "reviewed_import_digest"):
            import_design_files(self.store, self.sources, self.plan, "sha256:wrong")
        (self.sources / "rtl/wire_top.sv").write_text("// different bytes\n")
        with self.assertRaisesRegex(ValueError, "source_digest_mismatch"):
            self.load_imports()
        self.assertEqual(self.store.read()["imports"], {})
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM blobs").fetchone()[0], 0)

    def test_symlink_source_is_rejected(self) -> None:
        link = self.sources / "rtl/alias.sv"
        link.symlink_to(self.sources / "rtl/wire_top.sv")
        plan = copy.deepcopy(self.plan)
        plan["files"][-1]["source"] = "rtl/alias.sv"
        plan["files"][-1]["target"] = "rtl/alias.sv"
        with self.assertRaisesRegex(ValueError, "symlink"):
            import_design_files(self.store, self.sources, plan, content_digest(plan))
        self.assertEqual(self.store.read()["imports"], {})

    def test_import_target_cannot_be_replaced_by_later_import(self) -> None:
        self.load_imports()
        plan = copy.deepcopy(self.plan)
        plan["files"] = [plan["files"][0]]
        with self.assertRaisesRegex(ValueError, "already_owned"):
            import_design_files(self.store, self.sources, plan, content_digest(plan))

    def test_resume_uses_frozen_bytes_not_later_source_file_changes(self) -> None:
        self.load_imports()
        (self.sources / "rtl/wire_top.sv").write_text("// later user edit\n")
        self.store.close()
        self.store = DesignSessionStore(self.root / "session")
        self.assertEqual(self.store.import_contents(self.store.read()), self.files)
        self.assertEqual((self.sources / "rtl/wire_top.sv").read_text(), "// later user edit\n")

    def test_imports_are_explainable_before_execution_with_checked_anchors(self) -> None:
        self.load_imports()
        response = asyncio.run(self.agent.explain("Explain the imported circuit"))
        self.assertEqual(response["references"][0]["path"], "rtl/wire_top.sv")
        self.assertEqual(self.store.read()["files"], {})
        self.assertIsNone(self.store.read()["simulation"])
        self.expert.responses["explain"] = {"explanation": "bad", "references": [{"path": "rtl/wire_top.sv", "line": 999}]}
        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(self.agent.explain("explain"))

    def test_imported_rtl_is_not_in_model_or_dv_context(self) -> None:
        self.load_imports()
        for stage in ("reference_model", "dv"):
            pack = self.agent.context(self.store.read(), stage)
            self.assertFalse(any(p.startswith("rtl/") for p in pack["reference_artifacts"]))
            self.assertEqual(pack["reference_status"], "untrusted_imports_not_run_evidence")

    def test_chat_explains_imports_without_replacing_discovery_specification(self) -> None:
        self.load_imports()
        commands = iter(["/explain How does the wire work?", "/quit"])
        lines: list[str] = []
        self.assertEqual(asyncio.run(conversation(self.agent, read=lambda _: next(commands), emit=lines.append)), 0)
        self.assertEqual([stage for stage, _ in self.expert.seen], ["explain"])
        self.assertIsNone(self.store.read()["spec"])
        self.assertIn("rtl/wire_top.sv:1", lines)

    def test_baseline_requires_exact_review_and_fresh_simulation(self) -> None:
        self.load_imports()
        self.agent.propose(specification())
        plan = self.agent.plan_baseline(manifest())
        before = self.store.read()
        with self.assertRaisesRegex(ValueError, "digest_mismatch"):
            self.agent.approve_baseline(manifest(), "sha256:wrong")
        self.assertEqual(self.store.read(), before)
        self.agent.approve_baseline(manifest(), content_digest(plan))
        self.assertIsNone(self.store.read()["simulation"])
        self.assertEqual(self.store.contents(self.store.read()), self.files)
        asyncio.run(self.agent.advance())
        self.assertEqual(len(self.simulator.calls), 1)
        self.assertEqual(self.store.read()["status"], "needs_signoff")

    def test_change_plan_invalidates_evidence_and_preserves_readonly_files(self) -> None:
        self.baseline()
        asyncio.run(self.agent.advance())
        asyncio.run(self.agent.advance())
        plan = self.change()
        self.agent.approve_change(plan, content_digest(plan))
        self.assertIsNone(self.store.read()["simulation"])
        self.assertIsNone(self.store.read()["review"])
        revised = contribution("rtl")
        revised["files"][0]["content"] += "// explicitly scoped revision\n"
        self.expert.responses["rtl"] = revised
        for _ in STAGES:
            asyncio.run(self.agent.advance())
        current = self.store.contents(self.store.read())
        self.assertNotEqual(current["rtl/wire_top.sv"], self.files["rtl/wire_top.sv"])
        self.assertEqual({p: c for p, c in current.items() if p != "rtl/wire_top.sv"},
                         {p: c for p, c in self.files.items() if p != "rtl/wire_top.sv"})
        self.assertEqual(self.store.import_contents(self.store.read()), self.files)
        asyncio.run(self.agent.advance())
        self.assertEqual(len(self.simulator.calls), 2)
        self.assertNotEqual(*self.simulator.calls)

    def test_change_cannot_edit_outside_stage_scope(self) -> None:
        self.baseline()
        plan = self.change()
        self.agent.approve_change(plan, content_digest(plan))
        for _ in range(3):
            asyncio.run(self.agent.advance())
        self.expert.responses["rtl"] = contribution("assertions")
        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(self.agent.advance())
        self.assertEqual(self.store.contents(self.store.read()), self.files)

    def test_reviewed_manifest_cannot_drift_during_dv_change(self) -> None:
        self.baseline()
        plan = self.change("dv")
        self.agent.approve_change(plan, content_digest(plan))
        for _ in range(5):
            asyncio.run(self.agent.advance())
        response = contribution("dv")
        response["manifest"]["seed"] += 1
        self.expert.responses["dv"] = response
        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(self.agent.advance())

    def test_stale_change_review_is_rejected(self) -> None:
        self.baseline()
        plan = self.change()
        plan["base_input_digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "baseline_stale"):
            self.agent.approve_change(plan, content_digest(plan))

    def test_v2_upgrade_preserves_limits_warnings_and_history(self) -> None:
        prior = previous_initial_state()
        prior["limits"] = {"max_calls": 1, "max_repairs": 0}
        payload = canonical(prior).decode()
        self.store.connection.execute("UPDATE snapshots SET payload=? WHERE revision=0", (payload,))
        with self.assertRaisesRegex(ValueError, "idle_discovery"):
            self.load_imports()
        self.store.upgrade()
        self.assertEqual(self.store.read()["schema"], SESSION_SCHEMA)
        self.assertEqual(self.store.read()["limits"], prior["limits"])
        self.assertEqual(self.store.connection.execute("SELECT payload FROM snapshots WHERE revision=0").fetchone()[0], payload)

    def test_readonly_artifact_tampering_is_rejected_by_state_contract(self) -> None:
        self.baseline()
        plan = self.change()
        self.agent.approve_change(plan, content_digest(plan))
        state = self.store.read()
        state["files"]["dv/test_wire.py"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "readonly_baseline"):
            validate_state(state)

    def test_import_cli_has_no_provider_or_runtime_flags(self) -> None:
        parser = argparse.ArgumentParser()
        add_design_commands(parser.add_subparsers(dest="command", required=True))
        plan_path = self.root / "imports.json"
        plan_path.write_bytes(canonical(self.plan))
        self.store.close()
        args = parser.parse_args(["import", "--project", str(self.root / "session"), "--source-root", str(self.sources),
                                  "--import-plan", str(plan_path), "--approve", content_digest(self.plan)])
        self.assertFalse(hasattr(args, "allow_provider"))
        with patch("builtins.print"):
            self.assertEqual(run_design_command(args), 0)
        self.store = DesignSessionStore(self.root / "session")
        self.assertEqual(self.store.read()["calls"], 0)

    def test_duplicate_targets_and_unknown_plan_fields_rejected(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["files"].append(plan["files"][0])
        with self.assertRaises(ValueError):
            validate_import_plan(plan)

        plan = copy.deepcopy(self.plan)
        plan["execute"] = True
        with self.assertRaises(ValueError):
            validate_import_plan(plan)

    def test_python_import_payload_is_stored_without_execution(self) -> None:
        payload = "raise RuntimeError('must_not_execute_imported_collateral')\n"
        (self.sources / "dv/untrusted.py").write_text(payload)
        plan: JsonObject = {"schema": "openrtl.design-import.v1", "files": [
            {"source": "dv/untrusted.py", "target": "dv/untrusted.py",
             "digest": "sha256:" + hashlib.sha256(payload.encode()).hexdigest()}]}
        state = import_design_files(self.store, self.sources, plan, content_digest(plan))
        self.assertEqual(self.store.import_contents(state)["dv/untrusted.py"], payload)
        self.assertIsNone(state["simulation"])


if __name__ == "__main__":
    unittest.main()
