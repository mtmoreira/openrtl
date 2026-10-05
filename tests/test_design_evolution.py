"""Local import/export and scoped evolution tests; all experts/runs are fixtures."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

from openrtl.adapters.design_export import export_design, export_material
from openrtl.adapters.design_selection import selection, select_paths
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.application.design_agent import DesignAgent
from openrtl.application.design_import_workflow import completion_plan, propose_import_work
from openrtl.application.design_local_review import approve_local, review_export, review_import, review_plan
from openrtl.design_cli import conversation
from openrtl.domain.design_session import JsonObject, STAGES, content_digest
from tests.test_design_agent import FakeExpert, FakeSimulator, contribution, manifest
from tests.test_design_conversation import ready_spec


class DesignEvolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.sources = self.root / "source files"
        self.sources.mkdir()
        self.store = DesignSessionStore(self.root / "session", create=True)
        self.addCleanup(self.store.close)
        self.expert, self.simulator = FakeExpert(), FakeSimulator()
        self.agent = DesignAgent(self.store, self.expert, self.simulator)
        self.contents = {row["path"]: row["content"] for stage in STAGES for row in contribution(stage)["files"]}
        for path, content in self.contents.items():
            selected = self.sources / path
            selected.parent.mkdir(parents=True, exist_ok=True)
            selected.write_text(content)

    def load(self, *, rtl_only: bool = False) -> None:
        paths = [p for p in self.contents if not rtl_only or p.startswith("rtl/")]
        approve_local(self.agent, review_import(self.agent, self.sources, paths, lambda _: None), "import")
        self.agent.propose(ready_spec())

    def request(self) -> JsonObject:
        paths = {stage: [r["path"] for r in contribution(stage)["files"]] if stage not in ("rtl", "assertions") else [] for stage in STAGES}
        return {"specification": ready_spec(), "stage_paths": paths, "manifest": manifest()}

    def chat(self, messages: list[str]) -> list[str]:
        output: list[str] = []
        inputs = iter([*messages, "/quit"])
        self.assertEqual(asyncio.run(conversation(self.agent, read=lambda _: next(inputs), emit=output.append)), 0)
        return output

    def test_selection_maps_explicit_files_and_requires_python_role(self) -> None:
        self.assertEqual(select_paths(["wire.sv", "requirements.md", "tests.py=dv/test_wire.py"]),
                         [("wire.sv", "rtl/wire.sv"), ("requirements.md", "docs/requirements.md"), ("tests.py", "dv/test_wire.py")])
        for values in (["test.py"], ["a/wire.sv", "b/wire.sv"], [], ["rtl/wire.sv"] * 65):
            with self.subTest(values=values), self.assertRaises(ValueError): select_paths(values)

    def test_late_sensitive_or_invalid_path_is_rejected_before_any_payload_read(self) -> None:
        for source in (".env", "secret.json", "credentials.txt", "../outside.md", "x.pem", "run.sh"):
            with self.subTest(source=source), patch("openrtl.adapters.design_selection.os.open") as opened:
                with self.assertRaises(ValueError): selection(self.sources, ["rtl/wire_top.sv", source])
                opened.assert_not_called()

    def test_all_metadata_is_checked_before_any_payload_read(self) -> None:
        large = self.sources / "large.md"
        large.write_bytes(b"x" * (256 * 1024 + 1))
        with patch("openrtl.adapters.design_selection.os.open") as opened:
            with self.assertRaises(ValueError): selection(self.sources, ["rtl/wire_top.sv", "large.md"])
            opened.assert_not_called()

    def test_links_special_files_and_non_lf_text_fail(self) -> None:
        alias = self.sources / "alias.sv"
        alias.symlink_to(self.sources / "rtl/wire_top.sv")
        with self.assertRaises(ValueError): selection(self.sources, ["alias.sv"])
        hard = self.sources / "hard.md"
        original = self.sources / "plain.md"
        original.write_text("plain\n")
        os.link(original, hard)
        with self.assertRaises(ValueError): selection(self.sources, ["hard.md"])
        pipe = self.sources / "pipe.md"
        os.mkfifo(pipe)
        with self.assertRaises(ValueError): selection(self.sources, ["pipe.md"])
        (self.sources / "bad.md").write_bytes(b"windows\r\n")
        with self.assertRaises(ValueError): selection(self.sources, ["bad.md"])

    def test_complete_preview_and_explicit_approval_without_execution(self) -> None:
        output: list[str] = []
        review = review_import(self.agent, self.sources, ["rtl/wire_top.sv"], output.append)
        self.assertFalse(self.store.read()["imports"])
        self.assertIn(self.contents["rtl/wire_top.sv"], output)
        approve_local(self.agent, review, "import")
        self.assertEqual(self.store.import_contents(self.store.read())["rtl/wire_top.sv"], self.contents["rtl/wire_top.sv"])
        self.assertEqual(self.store.read()["calls"], 0)
        self.assertFalse(self.simulator.calls)

    def test_source_change_after_preview_is_refused_atomically(self) -> None:
        review = review_import(self.agent, self.sources, list(self.contents), lambda _: None)
        (self.sources / "rtl/wire_top.sv").write_text("// changed\n")
        with self.assertRaisesRegex(ValueError, "selection_changed"): approve_local(self.agent, review, "import")
        self.assertFalse(self.store.read()["imports"])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM blobs").fetchone()[0], 0)

    def test_source_root_replacement_with_same_bytes_is_refused(self) -> None:
        review = review_import(self.agent, self.sources, ["rtl/wire_top.sv"], lambda _: None)
        self.sources.rename(self.root / "originals retained")
        (self.sources / "rtl").mkdir(parents=True)
        (self.sources / "rtl/wire_top.sv").write_text(self.contents["rtl/wire_top.sv"])
        with self.assertRaisesRegex(ValueError, "selection_changed"): approve_local(self.agent, review, "import")

    def test_stale_wrong_kind_and_absent_local_review_are_refused(self) -> None:
        review = review_import(self.agent, self.sources, ["rtl/wire_top.sv"], lambda _: None)
        with self.assertRaises(ValueError): approve_local(self.agent, review, "baseline")
        with self.assertRaises(ValueError): approve_local(self.agent, None, "import")
        self.agent.detail("brief")
        with self.assertRaisesRegex(ValueError, "stale"): approve_local(self.agent, review, "import")

    def test_chat_import_explain_and_export_use_reviewed_files_and_spaces(self) -> None:
        destination = self.root / "export files"
        output = self.chat(["/select " + shlex.quote(str(self.sources)) + " rtl/wire_top.sv",
                            "approve this import", "explain this circuit",
                            "/export " + shlex.quote(str(destination)), "export this design"])
        self.assertEqual(self.expert.seen[0][0], "explain")
        self.assertEqual((destination / "imports/rtl/wire_top.sv").read_text(), self.contents["rtl/wire_top.sv"])
        self.assertTrue(any("export" in row.lower() for row in output))
        self.assertFalse((destination / "session.sqlite3").exists())

    def test_revocation_invalidates_pending_local_review(self) -> None:
        self.chat(["/select " + shlex.quote(str(self.sources)) + " rtl/wire_top.sv",
                   "revoke provider permission", "approve this import"])
        self.assertFalse(self.store.read()["imports"])

    def test_source_explanation_requires_nonempty_valid_anchors(self) -> None:
        self.load(rtl_only=True)
        self.expert.responses["explain"] = {"explanation": "Unanchored synthetic response", "references": []}
        with self.assertRaisesRegex(ValueError, "expert_output_invalid"): asyncio.run(self.agent.explain("Explain"))
        self.assertEqual(self.store.read()["last_error"], "expert_output_invalid")

    def test_baseline_adopts_entirely_inside_chat_without_execution(self) -> None:
        self.load()
        self.expert.responses["change_planning"] = {"summary": "Fixture baseline proposal", "specification": ready_spec(),
            "stage_paths": {s: [] for s in STAGES}, "manifest": manifest()}
        output = self.chat(["prepare imported baseline", "approve this baseline"])
        self.assertEqual(self.store.read()["stage"], len(STAGES))
        self.assertEqual(set(self.store.read()["files"]), set(self.contents))
        self.assertIsNone(self.store.read()["simulation"])
        self.assertFalse(self.simulator.calls)
        self.assertFalse(any("Quit, then" in row for row in output))

    def test_baseline_manifest_file_can_be_approved_without_quitting(self) -> None:
        self.load()
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest()))
        self.chat(["/baseline " + str(path), "approve this baseline"])
        self.assertEqual(self.store.read()["stage"], len(STAGES))
        self.assertEqual(self.store.read()["calls"], 0)

    def test_completion_keeps_imports_and_independent_role_contexts(self) -> None:
        self.load(rtl_only=True)
        original = self.store.import_contents(self.store.read())
        self.expert.responses["change_planning"] = {"summary": "Synthetic independent collateral proposal", **self.request()}
        self.chat(["complete imported design", "approve this completion"])
        for _ in STAGES: asyncio.run(self.agent.advance())
        state = self.store.read()
        self.assertEqual(state["stage"], len(STAGES))
        self.assertEqual(state["manifest"], manifest())
        self.assertIsNone(state["simulation"])
        for path, data in original.items():
            self.assertEqual(self.store.contents(state)[path], data)
            self.assertEqual((self.sources / path).read_text(), data)
        for stage, context in self.expert.seen:
            if stage in ("reference_model", "dv"):
                self.assertFalse(any(p.startswith("rtl/") for p in context["artifacts"]))
                self.assertFalse(any(p.startswith("rtl/") for p in context["reference_artifacts"]))
        self.assertFalse(any(s in ("rtl", "assertions") for s, _ in self.expert.seen))

    def test_completion_rejects_rewriting_rtl_existing_files_or_requirements(self) -> None:
        self.load(rtl_only=True)
        for mutation in ("rtl", "requirements", "existing"):
            request = self.request()
            if mutation == "rtl": request["stage_paths"]["rtl"] = ["rtl/new.sv"]
            if mutation == "requirements": request["specification"]["title"] = "Changed"
            if mutation == "existing": request["stage_paths"]["assertions"] = ["rtl/wire_assertions.sv"]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): completion_plan(self.agent, request)
        self.assertFalse(self.store.read()["files"])

    def test_completion_plan_stale_after_preference_change(self) -> None:
        self.load(rtl_only=True)
        plan = completion_plan(self.agent, self.request())
        review = review_plan(self.agent, "completion", plan, lambda _: None)
        self.agent.detail("brief")
        with self.assertRaisesRegex(ValueError, "stale"): approve_local(self.agent, review, "completion")

    def test_baseline_provider_cannot_sneak_in_generation(self) -> None:
        self.load()
        self.expert.responses["change_planning"] = {"summary": "Invalid fixture", **self.request()}
        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(propose_import_work(self.agent, completion=False))
        self.assertEqual(self.store.read()["status"], "discovery")
        self.assertIsNone(self.store.read()["active"])

    def test_export_bytes_manifest_provenance_and_no_overwrite(self) -> None:
        self.load()
        before = self.store.read()
        plan, _ = export_material(self.store)
        target = self.root / "export"
        export_design(self.store, target, content_digest(plan))
        saved = json.loads((target / "openrtl-export.json").read_text())
        self.assertEqual(saved, plan)
        for row in saved["files"]:
            data = (target / row["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])
            self.assertEqual(len(data), row["size_bytes"])
        self.assertFalse((target / "INCOMPLETE").exists())
        self.assertEqual(self.store.read(), before)
        with self.assertRaises(ValueError): export_design(self.store, target, content_digest(plan))
        self.assertEqual((target / "openrtl-export.json").read_text(), json.dumps(saved, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")

    def test_export_stale_missing_blob_and_unsafe_destination_fail_before_creation(self) -> None:
        self.load()
        plan, _ = export_material(self.store)
        target = self.root / "stale"
        self.agent.detail("brief")
        with self.assertRaisesRegex(ValueError, "stale"): export_design(self.store, target, content_digest(plan))
        self.assertFalse(target.exists())
        plan, _ = export_material(self.store)
        for destination in (self.store.root / "nested", self.root):
            with self.assertRaises(ValueError): export_design(self.store, destination, content_digest(plan))
        linked = self.root / "linked"
        linked.symlink_to(self.sources, target_is_directory=True)
        with self.assertRaises(ValueError): export_design(self.store, linked / "export", content_digest(plan))
        self.store.connection.execute("UPDATE blobs SET content=?", (b"tampered",))
        with self.assertRaises(ValueError): export_material(self.store)

    def test_export_interruption_marks_partial_directory_without_overwriting(self) -> None:
        self.load()
        plan, _ = export_material(self.store)
        target = self.root / "partial"
        original = os.fsync
        calls = 0
        def fail_after_marker(fd: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError("synthetic interruption")
            original(fd)
        with patch("openrtl.adapters.design_export.os.fsync", side_effect=fail_after_marker):
            with self.assertRaises(OSError): export_design(self.store, target, content_digest(plan))
        self.assertTrue((target / "INCOMPLETE").is_file())
        with self.assertRaises(ValueError): export_design(self.store, target, content_digest(plan))

    def test_export_bound_evidence_is_copied_and_tampering_is_refused(self) -> None:
        # This manually manufactured receipt tests byte binding, never actual simulation.
        from tests.test_design_coaching import DesignCoachingTest
        self.load()
        baseline = self.agent.plan_baseline(manifest())
        self.agent.approve_baseline(manifest(), content_digest(baseline))
        asyncio.run(self.agent.advance())
        fixture = DesignCoachingTest()
        fixture.root, fixture.store = self.store.root, self.store
        state = fixture.recorded_run()
        plan, _ = export_material(self.store)
        destination = self.root / "evidence-export"
        export_design(self.store, destination, content_digest(plan))
        run_id = state["simulation"]["run_id"]
        relative = "runs/" + run_id + "/evidence/waves.vcd"
        self.assertEqual((destination / relative).read_bytes(), (self.store.root / relative).read_bytes())
        (self.store.root / relative).write_bytes(b"changed fixture\n")
        with self.assertRaises(ValueError): export_material(self.store)

    def test_export_review_contains_no_implicit_permission_or_execution(self) -> None:
        self.load()
        review = review_export(self.agent, self.root / "reviewed-export", lambda _: None)
        approve_local(self.agent, review, "export")
        self.assertEqual(self.store.read()["calls"], 0)
        self.assertFalse(self.simulator.calls)

    def test_explicit_sources_only_does_not_fabricate_or_retain_run_evidence(self) -> None:
        self.load()
        plan = self.agent.plan_baseline(manifest())
        self.agent.approve_baseline(manifest(), content_digest(plan))
        asyncio.run(self.agent.advance())
        with self.assertRaises(ValueError): export_material(self.store)  # Legacy synthetic receipt cannot qualify.
        exported, files = export_material(self.store, include_evidence=False)
        self.assertEqual(exported["evidence"], "explicitly-excluded")
        self.assertFalse(any(p.startswith("runs/") for p in files))
        self.assertIn("rtl/wire_top.sv", files)

    def test_feature_and_dv_approvals_stay_in_chat_and_preserve_unwritable_bytes(self) -> None:
        self.load()
        plan = self.agent.plan_baseline(manifest())
        self.agent.approve_baseline(manifest(), content_digest(plan))
        for intent in ("feature", "dv", "optimization"):
            before = self.store.contents(self.store.read())
            spec = copy.deepcopy(self.store.read()["spec"])
            if intent == "feature": spec["title"] = "Reviewed fixture feature"
            stage = "dv" if intent == "dv" else "rtl"
            paths = {s: ["dv/test_wire.py" if stage == "dv" else "rtl/wire_top.sv"] if s == stage else [] for s in STAGES}
            self.expert.responses["change_planning"] = {"summary": "Fixture proposal", "specification": spec, "stage_paths": paths, "manifest": manifest()}
            self.chat([{ "feature": "add a reviewed feature", "dv": "improve the tests", "optimization": "try an optimization"}[intent],
                       "approve this change"])
            for s in STAGES:
                output = contribution(s)
                if s == stage:
                    for row in output["files"]: row["content"] += "# fixture revision\n" if stage == "dv" else "// fixture revision\n"
                self.expert.responses[s] = output
                asyncio.run(self.agent.advance())
            after = self.store.contents(self.store.read())
            for path in before:
                if path not in paths[stage]: self.assertEqual(after[path], before[path])
            self.assertIsNone(self.store.read()["simulation"])
            self.assertIsNone(self.store.read()["review"])

    def test_exact_planned_change_can_be_approved_inside_chat(self) -> None:
        self.load()
        plan = self.agent.plan_baseline(manifest())
        self.agent.approve_baseline(manifest(), content_digest(plan))
        request = {"specification": ready_spec(), "stage_paths": {s: ["dv/test_wire.py"] if s == "dv" else [] for s in STAGES}, "manifest": manifest()}
        path = self.root / "change.json"
        path.write_text(json.dumps(request))
        self.chat(["/change-plan " + str(path), "approve this change"])
        self.assertIsNotNone(self.store.read()["change_plan"])
        self.assertEqual(self.store.read()["stage"], 0)

    def test_review_display_failure_returns_no_import_authority(self) -> None:
        def fail(message: str) -> None: raise OSError("closed output fixture")
        with self.assertRaises(OSError): review_import(self.agent, self.sources, ["rtl/wire_top.sv"], fail)
        self.assertFalse(self.store.read()["imports"])

    def test_later_normal_review_supersedes_a_local_change_review(self) -> None:
        self.load()
        baseline = self.agent.plan_baseline(manifest())
        self.agent.approve_baseline(manifest(), content_digest(baseline))
        selected = ready_spec()
        selected["title"] = "Latest displayed proposal"
        stages = {s: ["rtl/wire_top.sv"] if s == "rtl" else [] for s in STAGES}
        self.expert.responses["change_planning"] = {"summary": "Fixture proposal B", "specification": selected,
            "stage_paths": stages, "manifest": manifest()}
        asyncio.run(self.agent.propose_improvement("fixture B"))
        older = {"specification": ready_spec(), "stage_paths": stages, "manifest": manifest()}
        path = self.root / "older.json"
        path.write_text(json.dumps(older))
        self.chat(["/change-plan " + str(path), "review", "approve this change"])
        self.assertEqual(self.store.read()["spec"]["title"], "Latest displayed proposal")


if __name__ == "__main__":
    unittest.main()
